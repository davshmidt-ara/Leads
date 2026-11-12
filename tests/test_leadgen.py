import contextlib
import io
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from leadgen import cli, config, csvio, enrich, outreach, scoring, server
from leadgen.db import Store, keys
from leadgen.sources import _http, apollo, osm, places, reddit

CFG = config.load("example")
FRUITS = config.load("fruits")
SAMPLE = os.path.join(config.ROOT, "fruits", "sample_leads.csv")


def tmp_store():
    return Store(os.path.join(tempfile.mkdtemp(), "t.db"))


def serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


class DedupeTests(unittest.TestCase):
    def setUp(self):
        self.store = tmp_store()
        self.addCleanup(self.store.close)

    def test_business_merges_across_sources_and_fills_blanks(self):
        self.assertTrue(self.store.add("places", {"company": "Joe's", "name": "Joe's", "website": "https://www.joes.com/x",
                                                  "url": "https://maps.google.com/?cid=1"}))
        self.assertFalse(self.store.add("osm", {"company": "Joe's", "name": "Joe's", "website": "joes.com",
                                                "phone": "555 123 4567", "url": "https://www.openstreetmap.org/node/9"}))
        # later only the phone matches
        self.assertFalse(self.store.add("import", {"company": "Joes Pizza", "phone": "+1 (555) 123-4567"}))
        leads = self.store.all()
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0]["phone"], "555 123 4567")

    def test_people_at_one_company_stay_separate(self):
        a = {"name": "Ann Lee", "company": "Acme", "title": "Owner", "website": "acme.com", "phone": "555 000 1111"}
        b = {"name": "Bob Ray", "company": "Acme", "title": "Chef", "website": "acme.com", "phone": "555 000 1111"}
        self.assertTrue(self.store.add("apollo", a))
        self.assertTrue(self.store.add("apollo", b))
        self.assertFalse(self.store.add("apollo", dict(a)))

    def test_shared_hosts_are_not_identity(self):
        self.assertTrue(self.store.add("import", {"company": "A", "website": "https://instagram.com/a"}))
        self.assertTrue(self.store.add("import", {"company": "B", "website": "https://instagram.com/b"}))
        self.assertEqual(keys({"company": "A", "website": "https://instagram.com/a"}), ["name:|a"])

    def test_empty_rows_are_ignored(self):
        self.assertFalse(self.store.add("import", {}))
        self.assertFalse(self.store.add("import", {"notes": "nothing to identify"}))

    def test_do_not_contact_survives_reimport(self):
        self.store.add("import", {"name": "Sam", "email": "s@x.com"})
        self.store.mark(1, "do_not_contact")
        self.assertFalse(self.store.add("apollo", {"name": "Sam", "email": "S@X.com"}))
        self.assertEqual(self.store.get(1)["status"], "do_not_contact")
        self.assertEqual(outreach.draft_all(self.store, CFG), 0)

    def test_migrates_v1_database(self):
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        conn = sqlite3.connect(path)
        conn.executescript("""CREATE TABLE leads (id INTEGER PRIMARY KEY AUTOINCREMENT, dedupe_key TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL, name TEXT, company TEXT, title TEXT, email TEXT, phone TEXT, website TEXT,
            location TEXT, url TEXT, signal TEXT, notes TEXT, score INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'new', draft TEXT, raw TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE INDEX idx_leads_score ON leads(score DESC);
            INSERT INTO leads (dedupe_key, source, name, email, status, created_at, updated_at)
            VALUES ('email:old@x.com', 'import', 'Old', 'old@x.com', 'replied', 1, 1);""")
        conn.close()
        store = Store(path)
        self.addCleanup(store.close)
        self.assertEqual(store.get(1)["status"], "replied")
        self.assertEqual(store.get(1)["touches"], 0)
        self.assertFalse(store.add("import", {"name": "Old", "email": "old@x.com"}))
        self.assertTrue(store.add("import", {"name": "New", "email": "new@x.com"}))


class ScoringTests(unittest.TestCase):
    def test_hot_inbound_and_excluded(self):
        good = {"source": "inbound", "email": "a@b.co", "title": "Owner", "signal": "need an accountant", "company": "Austin restaurant"}
        bad = {"source": "places", "company": "Franchise restaurant"}
        self.assertGreaterEqual(scoring.score(good, CFG), CFG["scoring"]["hot"])
        self.assertEqual(scoring.score(bad, CFG), 0)

    def test_whole_word_matching(self):
        self.assertTrue(scoring.has("bar", "two juice bars downtown"))
        self.assertFalse(scoring.has("bar", "a barber shop"))
        self.assertFalse(scoring.has("deli", "fast delivery"))

    def test_segments_pick_highest_weight(self):
        lead = {"source": "osm", "company": "Sunrise Hotel Cafe", "notes": "hotel cafe"}
        self.assertEqual(scoring.segment_for(lead, FRUITS)["name"], "hotels_catering")
        self.assertIsNone(scoring.segment_for({"source": "osm", "company": "Tyre Depot"}, FRUITS))

    def test_fruit_sample_ranks_sensibly(self):
        store = tmp_store()
        self.addCleanup(store.close)
        rows = list(csvio.read_rows(SAMPLE))
        new = sum(store.add("sample", r) for r in rows)
        self.assertEqual((len(rows), new), (16, 15))  # one duplicate juice bar
        scoring.score_all(store, FRUITS)
        by = {l["company"]: l for l in store.all() if not l["title"]}
        self.assertEqual(by["Green Press Juice Bar (SAMPLE)"]["segment"], "juice_bars")
        self.assertEqual(scoring.tier(by["Green Press Juice Bar (SAMPLE)"]["score"], FRUITS), "hot")
        self.assertEqual(scoring.tier(by["Lone Star Wholesale Produce (SAMPLE)"]["score"], FRUITS), "cold")
        self.assertGreater(by["Acai Republic (SAMPLE)"]["score"], by["Iron Peak Fitness (SAMPLE)"]["score"])


class OutreachTests(unittest.TestCase):
    def setUp(self):
        self.store = tmp_store()
        self.addCleanup(self.store.close)

    def test_channels_and_personalisation(self):
        self.store.add("apollo", {"name": "Sam Lee", "title": "Owner", "email": "s@x.com", "company": "X"})
        self.store.add("reddit", {"name": "u1", "url": "https://r/1", "signal": "need an accountant"})
        self.store.add("osm", {"name": "Cafe Uno", "company": "Cafe Uno", "phone": "555 222 3333"})
        self.assertEqual(outreach.draft_all(self.store, CFG), 3)
        d = {l["source"]: l["draft"] for l in self.store.all()}
        self.assertIn("Hi Sam", d["apollo"])
        self.assertIn("need an accountant", d["reddit"])
        self.assertIn("Call opener", d["osm"])
        self.assertNotIn("{", "".join(d.values()))

    def test_segment_pitch_used(self):
        self.store.add("osm", {"name": "Zest", "company": "Zest", "email": "hi@zest.co", "notes": "juice bar"})
        scoring.score_all(self.store, FRUITS)
        outreach.draft_all(self.store, FRUITS)
        self.assertIn("ripe-to-blend", self.store.get(1)["draft"])
        self.assertIn("Hi there", self.store.get(1)["draft"])

    def test_followup_sequence(self):
        self.store.add("import", {"name": "Sam Lee", "title": "Owner", "email": "s@x.com", "company": "X"})
        outreach.draft_all(self.store, CFG)
        first = self.store.get(1)["draft"]
        self.store.mark(1, "contacted")
        lead = self.store.get(1)
        self.assertEqual((lead["touches"], lead["draft"]), (1, None))
        self.assertFalse(outreach.is_due(lead, CFG))                       # too soon
        self.assertEqual(outreach.draft_all(self.store, CFG), 0)
        later = time.time() + 4 * outreach.DAY
        self.assertTrue(outreach.is_due(lead, CFG, later))
        self.assertEqual([l["id"] for l in outreach.agenda(self.store, CFG, now=later)["followups_due"]], [1])
        self.store.update(1, contacted_at=time.time() - 4 * outreach.DAY)
        self.assertEqual(outreach.draft_all(self.store, CFG), 1)
        second = self.store.get(1)["draft"]
        self.assertNotEqual(first, second)
        self.assertIn("Following up", second)
        self.store.mark(1, "contacted")
        self.store.update(1, contacted_at=time.time() - 8 * outreach.DAY)
        outreach.draft_all(self.store, CFG)
        self.assertIn("Last note", self.store.get(1)["draft"])
        self.store.mark(1, "contacted")                                    # third and final touch
        self.store.update(1, contacted_at=time.time() - 8 * outreach.DAY)
        lead = self.store.get(1)
        self.assertFalse(outreach.is_due(lead, CFG))
        self.assertTrue(outreach.is_stale(lead, CFG))
        self.assertEqual(outreach.draft_all(self.store, CFG), 0)

    def test_replied_leads_stop_the_sequence(self):
        self.store.add("import", {"name": "Sam Lee", "title": "Owner", "email": "s@x.com"})
        self.store.mark(1, "contacted")
        self.store.mark(1, "replied")
        self.store.update(1, contacted_at=time.time() - 30 * outreach.DAY)
        self.assertFalse(outreach.is_due(self.store.get(1), CFG))
        self.assertEqual([l["id"] for l in outreach.agenda(self.store, CFG)["reply_now"]], [1])
        self.assertEqual(self.store.report()[0]["reply_rate"], "100%")


class SourceTests(unittest.TestCase):
    def test_places_adapter_skips_closed(self):
        resp = {"places": [{"displayName": {"text": "Cafe"}, "websiteUri": "https://cafe.com"},
                           {"displayName": {"text": "Gone"}, "businessStatus": "CLOSED_PERMANENTLY"}]}
        with mock.patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "k"}), \
             mock.patch.object(places, "request_json", return_value=resp):
            out = list(places.collect(CFG, {"queries": ["cafe"]}))
        self.assertEqual([o["company"] for o in out], ["Cafe"])

    def test_places_needs_key(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(config.ConfigError):
            list(places.collect(CFG, {"queries": ["cafe"]}))

    def test_apollo_adapter(self):
        resp = {"people": [{"first_name": "Ann", "last_name": "Lee", "title": "Owner", "email": "email_not_unlocked@domain.com",
                            "organization": {"name": "Acme", "website_url": "http://acme.com"}, "linkedin_url": "http://li/ann"}]}
        with mock.patch.dict(os.environ, {"APOLLO_API_KEY": "k"}), \
             mock.patch.object(apollo, "request_json", side_effect=[resp, {"people": []}]):
            out = list(apollo.collect(CFG, {"pages": 2}))
        self.assertEqual((out[0]["name"], out[0]["email"], out[0]["company"]), ("Ann Lee", None, "Acme"))

    def test_osm_adapter(self):
        resp = {"elements": [
            {"type": "node", "id": 7, "tags": {"name": "Pizza", "contact:phone": "555", "website": "https://p.com", "cuisine": "pizza"}},
            {"type": "way", "id": 8, "tags": {"name": "MegaChain", "brand": "MegaChain"}}]}
        with mock.patch.object(osm, "request_form_json", return_value=resp) as m:
            out = list(osm.collect(CFG, {"area": "Austin", "searches": ["amenity=restaurant"], "skip_chains": True}))
        self.assertEqual([o["company"] for o in out], ["Pizza"])
        self.assertEqual((out[0]["phone"], out[0]["notes"]), ("555", "cuisine: pizza"))
        self.assertIn('area["name"="Austin"]', m.call_args[0][1]["data"])
        self.assertIn('nwr["amenity"="restaurant"]["name"](area.a)', m.call_args[0][1]["data"])

    def test_osm_around_and_injection(self):
        q = osm._query({"around": [30.26, -97.74, 5000], "limit": 10}, "shop=greengrocer")
        self.assertEqual(q, '[out:json][timeout:60];nwr["shop"="greengrocer"]["name"](around:5000,30.26,-97.74);out tags center 10;')
        with self.assertRaises(ValueError):
            list(osm.collect(CFG, {"area": 'x"];bad', "searches": ["a=b"]}))
        with self.assertRaises(ValueError):
            list(osm.collect(CFG, {"searches": ["a=b"]}))

    def test_osm_falls_back_to_mirror(self):
        with mock.patch.object(osm, "request_form_json", side_effect=[OSError("down"), {"elements": []}]) as m:
            self.assertEqual(list(osm.collect(CFG, {"area": "Austin", "searches": ["amenity=cafe"]})), [])
        self.assertEqual(m.call_count, 2)

    def test_reddit_adapter(self):
        resp = {"data": {"children": [{"data": {"author": "u", "permalink": "/r/a/1", "title": "t"}}]}}
        with mock.patch.object(reddit, "request_json", return_value=resp):
            out = list(reddit.collect(CFG, {"subreddits": ["a"], "queries": ["q"]}))
        self.assertEqual(out[0]["url"], "https://www.reddit.com/r/a/1")

    def test_http_retries_then_succeeds(self):
        calls = []

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(1)
                code = 429 if len(calls) < 3 else 200
                self.send_response(code)
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *a):
                pass

        httpd, base = serve(H)
        self.addCleanup(httpd.shutdown)
        with mock.patch.object(_http, "_sleep") as sleep:
            self.assertEqual(_http.request_json(base + "/x"), {"ok": True})
        self.assertEqual((len(calls), sleep.call_count), (3, 2))

    def test_http_reports_client_errors(self):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"bad key")

            def log_message(self, *a):
                pass

        httpd, base = serve(H)
        self.addCleanup(httpd.shutdown)
        with self.assertRaisesRegex(OSError, "HTTP 401.*bad key"):
            _http.request_json(base + "/x")


SITE = {
    "/": "<html><a href='/menu'>Menu</a> <a href='/contact-us'>Contact</a> <img src='logo@2x.png'> "
         "<script>dsn='abc@sentry.io'</script></html>",
    "/contact-us": "<html>Email <a href='mailto:noreply@127.0.0.1.nip.test'>x</a> or "
                   "<a href='mailto:Orders%40freshcafe.test?subject=Hi'>orders</a> / press@freshcafe.test</html>",
    "/robots.txt": "User-agent: *\nDisallow: /private\n",
}


class EnrichTests(unittest.TestCase):
    def setUp(self):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = SITE.get(self.path)
                self.send_response(200 if body else 404)
                self.send_header("Content-Type", "text/plain" if self.path == "/robots.txt" else "text/html")
                self.end_headers()
                self.wfile.write((body or "").encode())

            def log_message(self, *a):
                pass

        self.httpd, self.base = serve(H)
        self.addCleanup(self.httpd.shutdown)

    def test_extract_ranks_and_filters(self):
        page = "a@b.com, hello@shop.com, privacy@shop.com, x@example.com, pic@2x.png, info [at] shop.com"
        self.assertEqual(enrich.extract_emails(page, "shop.com"), ["hello@shop.com", "info@shop.com", "a@b.com", "privacy@shop.com"])

    def test_private_hosts_refused_by_default(self):
        with mock.patch.dict(os.environ, {"LEADGEN_ALLOW_PRIVATE": ""}):
            self.assertEqual(enrich.fetch(self.base + "/"), "")
            self.assertIsNone(enrich.find_email(self.base))

    def test_finds_email_on_contact_page(self):
        with mock.patch.dict(os.environ, {"LEADGEN_ALLOW_PRIVATE": "1"}):
            self.assertEqual(enrich.find_email(self.base), "orders@freshcafe.test")

    def test_respects_robots(self):
        with mock.patch.dict(os.environ, {"LEADGEN_ALLOW_PRIVATE": "1"}), \
             mock.patch.dict(SITE, {"/robots.txt": "User-agent: *\nDisallow: /\n"}):
            self.assertIsNone(enrich.find_email(self.base))

    def test_enrich_all_updates_lead_once(self):
        store = tmp_store()
        self.addCleanup(store.close)
        store.add("osm", {"name": "Fresh Cafe", "company": "Fresh Cafe", "website": self.base, "phone": "555 444 0000"})
        store.add("osm", {"name": "No Site", "company": "No Site", "phone": "555 444 0001"})
        outreach.draft_all(store, FRUITS)
        self.assertIn("Call script", store.get(1)["draft"])
        with mock.patch.dict(os.environ, {"LEADGEN_ALLOW_PRIVATE": "1"}):
            self.assertEqual(enrich.enrich_all(store, pause=0, log=lambda m: None), (1, 1))
            self.assertEqual(enrich.enrich_all(store, pause=0, log=lambda m: None), (0, 0))
        self.assertEqual(store.get(1)["email"], "orders@freshcafe.test")
        outreach.draft_all(store, FRUITS)
        self.assertIn("Subject:", store.get(1)["draft"])          # channel switched from call to email
        self.assertFalse(store.add("import", {"name": "Dup", "email": "orders@freshcafe.test"}))


class ConfigTests(unittest.TestCase):
    def test_shipped_configs_are_valid(self):
        self.assertEqual(config.validate(CFG), [])
        self.assertEqual(config.validate(FRUITS), [])

    def test_validate_catches_mistakes(self):
        bad = {"name": "X", "offer": {}, "icp": {"segments": [{"name": "a"}]}, "sources": {"yelp": {}, "osm": {}},
               "outreach": {"templates": {"email": "Hi {frist_name}"}}}
        probs = " | ".join(config.validate(bad))
        for needle in ("offer.summary", "offer.cta", "sender", "segments[0]", "sources.yelp", "area", "frist_name"):
            self.assertIn(needle, probs)

    def test_bad_slug_and_missing_business(self):
        with self.assertRaises(config.ConfigError):
            config.load("../etc")
        with self.assertRaises(config.ConfigError):
            config.load("does-not-exist")


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_full_pipeline_on_fruit_sample(self):
        tmp = tempfile.mkdtemp()
        shutil.copy(os.path.join(config.ROOT, "fruits", "business.json"), tmp)
        with mock.patch.object(config, "db_path", return_value=os.path.join(tmp, "f.db")), \
             mock.patch.object(config, "business_dir", return_value=tmp):
            self.assertIn("16 rows, 15 new", self.run_cli("import", "fruits", SAMPLE, "--source", "sample")[1])
            self.assertIn("0 new", self.run_cli("import", "fruits", SAMPLE, "--source", "sample")[1])
            self.assertIn("drafts written", self.run_cli("draft", "fruits", "--min-score", "40")[1])
            agenda = self.run_cli("next", "fruits")[1]
            self.assertIn("FIRST CONTACT", agenda)
            self.assertIn("Green Press", agenda)
            self.assertNotIn("Lone Star Wholesale", agenda)
            self.assertIn("free sample box", self.run_cli("show", "fruits", "1")[1])
            self.assertIn("#1: contacted", self.run_cli("mark", "fruits", "contacted", "1", "999")[1])
            self.assertIn("#2: replied", self.run_cli("mark", "fruits", "replied", "2")[1])
            self.assertIn("REPLIED", self.run_cli("next", "fruits")[1])
            self.assertIn("juice_bars", self.run_cli("report", "fruits", "--by", "segment")[1])
            self.assertIn("15 leads", self.run_cli("export", "fruits")[1])
            with open(os.path.join(tmp, "exports", "leads.csv"), encoding="utf-8") as f:
                header = f.readline()
            self.assertIn("tier", header)
            self.assertIn("first_name", header)
            self.assertIn("15 leads deleted", self.run_cli("purge", "fruits", "--source", "sample")[1])

    def test_collect_survives_a_failing_source(self):
        tmp = tempfile.mkdtemp()

        def boom(cfg, sc):
            yield {"company": "One", "name": "One", "phone": "555 123 9999"}
            raise OSError("network down")

        with mock.patch.object(config, "db_path", return_value=os.path.join(tmp, "f.db")), \
             mock.patch.object(osm, "collect", boom), mock.patch.object(reddit, "collect", lambda c, s: iter(())), \
             mock.patch.dict(os.environ, {}, clear=True):
            code, out = self.run_cli("collect", "fruits")
        self.assertEqual(code, 0)
        self.assertIn("[osm] stopped after 1 leads: network down", out)
        self.assertIn("[osm] 1 found, 1 new", out)
        self.assertIn("GOOGLE_PLACES_API_KEY", out)


class ServerTests(unittest.TestCase):
    def test_inbound_form_and_dashboard(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.object(config, "db_path", return_value=os.path.join(tmp, "s.db")), \
             mock.patch.dict(os.environ, {"LEADGEN_ADMIN_TOKEN": "tok"}):
            httpd, base = serve(server.make_handler("fruits"))
            self.addCleanup(httpd.shutdown)

            def post(path, d):
                try:
                    return urllib.request.urlopen(base + path, urllib.parse.urlencode(d).encode()).status
                except urllib.error.HTTPError as ex:
                    return ex.code

            self.assertIn("free sample box", urllib.request.urlopen(base + "/?utm_source=flyer").read().decode())
            self.assertEqual(post("/lead", {"name": "Ann <script>", "email": "ann@x.com", "utm_source": "ads",
                                            "message": "need a fruit supplier for my cafe"}), 200)
            self.assertEqual(post("/lead", {"name": "Bot", "email": "bot@x.com", "website_url": "spam"}), 200)
            self.assertEqual(post("/lead", {"name": "Bad", "email": "not-an-email"}), 400)
            page = urllib.request.urlopen(base + "/admin?token=tok").read().decode()
            self.assertIn("Ann &lt;script&gt;", page)
            self.assertNotIn("Ann <script>", page)
            self.assertNotIn("Bot", page)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/admin?token=wrong")
            self.assertEqual(post("/admin/status", {"id": 1, "status": "contacted", "token": "wrong"}), 403)
            post("/admin/status", {"id": 1, "status": "contacted", "token": "tok"})
            store = Store(os.path.join(tmp, "s.db"))
            lead = store.get(1)
            store.close()
            self.assertEqual((lead["source"], lead["status"], lead["touches"], lead["notes"]),
                             ("inbound", "contacted", 1, "utm_source=ads"))
            self.assertGreaterEqual(lead["score"], FRUITS["scoring"]["hot"])
            codes = [post("/lead", {"name": "Flood", "email": f"f{i}@x.com"}) for i in range(6)]
            self.assertEqual(codes[-1], 429)

    def test_admin_disabled_without_token(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.object(config, "db_path", return_value=os.path.join(tmp, "s.db")), \
             mock.patch.dict(os.environ, {"LEADGEN_ADMIN_TOKEN": ""}):
            httpd, base = serve(server.make_handler("fruits"))
            self.addCleanup(httpd.shutdown)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/admin?token=")


if __name__ == "__main__":
    unittest.main()
