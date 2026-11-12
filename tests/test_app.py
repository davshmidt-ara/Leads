"""The interactive app's JSON API, offline (the Overture source is replaced by a stub)."""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from leadgen import config, geo, server
from leadgen.sources import overture

FOUND = [
    {"name": "Zest Juice", "company": "Zest Juice", "email": "hello@zest.test", "phone": "+15125550100",
     "website": "https://zest.test", "location": "1 Main St, Austin, TX", "signal": "Overture Maps: smoothie juice bar",
     "notes": "category: smoothie_juice_bar", "url": "https://www.google.com/maps/search/?api=1&query=Zest"},
    {"name": "Corner Cafe", "company": "Corner Cafe", "phone": "5125550101", "location": "2 Main St, Austin, TX",
     "signal": "Overture Maps: cafe", "notes": "category: cafe"},
    {"name": "Tyre Depot", "company": "Tyre Depot", "location": "3 Main St", "signal": "Overture Maps: tire shop"},
]


class AppApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        shutil.copy(os.path.join(config.ROOT, "fruits", "business.json"), self.tmp)
        for p in (mock.patch.object(config, "db_path", return_value=os.path.join(self.tmp, "a.db")),
                  mock.patch.object(config, "business_dir", return_value=self.tmp),
                  mock.patch.dict(os.environ, {"LEADGEN_ADMIN_TOKEN": "tok"}),
                  mock.patch.object(overture, "collect", lambda cfg, sc, log=print: iter(FOUND)),
                  mock.patch.object(overture, "categories", lambda sc, text="", log=print:
                                    [(c, n, m) for c, n, m in [("cafe", 9, 4), ("smoothie_juice_bar", 3, 1)] if text in c])):
            p.start()
            self.addCleanup(p.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler("fruits"))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def call(self, path, body=None, token="tok", marker=True):
        headers = {"X-Token": token}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
            if marker:
                headers["X-Leadgen"] = "1"
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + "/api/" + path, data, headers)) as r:
                raw = r.read().decode()
                return r.status, json.loads(raw) if "json" in r.headers["Content-Type"] else raw
        except urllib.error.HTTPError as ex:
            return ex.code, json.loads(ex.read().decode())

    def wait_job(self):
        for _ in range(100):
            job = self.call("state")[1]["job"]
            if not job["running"] and job["finished"]:
                return job
            time.sleep(0.05)
        self.fail("job did not finish")

    def test_requires_token_and_marker_header(self):
        self.assertEqual(self.call("state", token="nope")[0], 403)
        self.assertEqual(self.call("mark", {"ids": [1], "status": "won"}, marker=False)[0], 403)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.base + "/app")
        page = urllib.request.urlopen(self.base + "/app?token=tok").read().decode()
        self.assertIn("Lead Finder", page)

    def test_find_then_work_a_lead(self):
        code, state = self.call("state")
        self.assertEqual((code, state["total"], state["search"]["lat"]), (200, 0, 30.2672))

        code, cats = self.call("categories?lat=30.27&lon=-97.74&radius_km=5&q=juice")
        self.assertEqual([c["cat"] for c in cats["categories"]], ["smoothie_juice_bar"])

        search = {"lat": 30.27, "lon": -97.74, "radius_km": 5, "categories": ["smoothie_juice_bar", "cafe"],
                  "min_confidence": 0.7, "per_category": 50, "skip_chains": True}
        self.assertEqual(self.call("find", search)[0], 200)
        job = self.wait_job()
        self.assertIsNone(job["error"])
        self.assertIn("3 businesses matched, 3 new", job["log"][0])
        with open(os.path.join(self.tmp, "business.json")) as f:                     # the search was saved
            saved = json.load(f)["sources"]["overture"]
        self.assertEqual((saved["around"], saved["categories"], saved["skip_chains"]),
                         ([30.27, -97.74, 5000], ["smoothie_juice_bar", "cafe"], True))

        state = self.call("state")[1]
        self.assertEqual((state["total"], state["with_email"], state["tiers"]["hot"]), (3, 1, 1))
        leads = self.call("leads")[1]["leads"]
        self.assertEqual([l["display"] for l in leads], ["Zest Juice", "Corner Cafe", "Tyre Depot"])
        self.assertEqual((leads[0]["tier"], leads[0]["segment"], leads[0]["channel"]), ("hot", "juice_bars", "email"))
        self.assertEqual(self.call("leads?has=email")[1]["total"], 1)
        self.assertEqual(self.call("leads?q=corner")[1]["leads"][0]["channel"], "call")
        self.assertEqual(self.call("leads?segment=-")[1]["leads"][0]["display"], "Tyre Depot")
        self.assertEqual(self.call("leads?tier=cold")[1]["total"], 1)

        today = self.call("leads?view=today")[1]["groups"]
        self.assertEqual((today[0]["key"], len(today[0]["leads"])), ("first_touch", 2))

        lead = self.call("lead?id=1")[1]
        self.assertIn("free sample box", lead["draft"])
        self.assertEqual(self.call("draft", {"id": 1, "text": "My own words"})[1]["draft"], "My own words")
        self.assertEqual(self.call("lead?id=1")[1]["draft"], "My own words")
        self.assertIn("Subject:", self.call("draft", {"id": 1})[1]["draft"])          # rewrite from template

        self.assertEqual(self.call("mark", {"ids": [1], "status": "contacted"})[1], {"updated": 1})
        lead = self.call("lead?id=1")[1]
        self.assertEqual((lead["status"], lead["touches"], lead["draft"]), ("contacted", 1, ""))
        self.call("mark", {"ids": [1], "status": "replied"})
        self.assertEqual(self.call("leads?view=today")[1]["groups"][0]["key"], "reply_now")
        report = self.call("report")[1]
        self.assertEqual(report["source"][0]["reply_rate"], "100%")
        code, text = self.call("export.csv")
        self.assertIn("Zest Juice", text)
        self.assertIn("first_name", text.splitlines()[0])

    def test_bad_input_is_explained(self):
        self.assertIn("latitude", self.call("find", {"categories": ["cafe"]})[1]["error"].lower())
        ok = {"lat": 30, "lon": -97, "radius_km": 5}
        self.assertIn("business types", self.call("find", {**ok, "categories": []})[1]["error"])
        self.assertIn("business types", self.call("find", {**ok, "categories": ['x"; drop']})[1]["error"])
        self.assertIn("radius", self.call("find", {**ok, "radius_km": 900, "categories": ["cafe"]})[1]["error"])
        self.assertEqual(self.call("mark", {"ids": [1], "status": "bogus"})[0], 400)
        self.assertEqual(self.call("lead?id=999")[0], 404)
        self.assertEqual(self.call("nothing")[0], 404)

    def test_source_failure_is_reported_not_fatal(self):
        def boom(cfg, sc, log=print):
            raise overture.OvertureError("cannot read Overture data")
            yield

        with mock.patch.object(overture, "collect", boom):
            self.call("find", {"lat": 30, "lon": -97, "radius_km": 5, "categories": ["cafe"]})
            self.assertIn("cannot read Overture data", self.wait_job()["error"])
        self.assertEqual(self.call("state")[0], 200)

    def test_locate_falls_back_with_advice(self):
        with mock.patch.object(geo, "locate", side_effect=OSError("blocked")):
            code, body = self.call("locate?q=Austin")
        self.assertEqual(code, 502)
        self.assertIn("latitude and longitude", body["error"])
        with mock.patch.object(geo, "locate", return_value=[{"name": "Austin", "lat": 30.27, "lon": -97.74}]):
            self.assertEqual(self.call("locate?q=Austin")[1]["places"][0]["lat"], 30.27)


if __name__ == "__main__":
    unittest.main()
