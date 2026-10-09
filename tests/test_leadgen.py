import json
import os
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from leadgen import config, outreach, scoring, server
from leadgen.db import Store
from leadgen.sources import places, reddit

CFG = config.load("example")


class Tests(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))

    def test_dedupe_and_fill(self):
        self.assertTrue(self.store.add("places", {"company": "Joe's", "website": "https://www.joes.com/x"}))
        self.assertFalse(self.store.add("apollo", {"company": "Joe's", "website": "joes.com", "phone": "555 123 4567"}))
        self.assertEqual(len(self.store.all()), 1)
        self.assertEqual(self.store.all()[0]["phone"], "555 123 4567")

    def test_scoring_and_exclude(self):
        good = {"source": "inbound", "email": "a@b.co", "title": "Owner", "signal": "need an accountant", "company": "Austin restaurant"}
        bad = {"source": "places", "company": "Franchise restaurant"}
        self.assertGreaterEqual(scoring.score(good, CFG), CFG["scoring"]["hot"])
        self.assertEqual(scoring.score(bad, CFG), 0)

    def test_drafts(self):
        self.store.add("apollo", {"name": "Sam Lee", "email": "s@x.com", "company": "X"})
        self.store.add("reddit", {"name": "u1", "url": "https://r/1", "signal": "need an accountant"})
        self.assertEqual(outreach.draft_all(self.store, CFG), 2)
        d = {l["source"]: l["draft"] for l in self.store.all()}
        self.assertIn("Hi Sam", d["apollo"])
        self.assertIn("need an accountant", d["reddit"])

    def test_places_adapter(self):
        resp = {"places": [{"displayName": {"text": "Cafe"}, "websiteUri": "https://cafe.com"}]}
        with mock.patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "k"}), \
             mock.patch.object(places, "request_json", return_value=resp):
            out = list(places.collect(CFG, {"queries": ["cafe"]}))
        self.assertEqual(out[0]["company"], "Cafe")

    def test_reddit_adapter(self):
        resp = {"data": {"children": [{"data": {"author": "u", "permalink": "/r/a/1", "title": "t"}}]}}
        with mock.patch.object(reddit, "request_json", return_value=resp):
            out = list(reddit.collect(CFG, {"subreddits": ["a"], "queries": ["q"]}))
        self.assertEqual(out[0]["url"], "https://www.reddit.com/r/a/1")

    def test_inbound_server(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.object(config, "db_path", return_value=os.path.join(tmp, "s.db")), \
             mock.patch.dict(os.environ, {"LEADGEN_ADMIN_TOKEN": "tok"}):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler("example"))
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            post = lambda d: urllib.request.urlopen(base + "/lead", urllib.parse.urlencode(d).encode())
            post({"name": "Ann", "email": "ann@x.com", "utm_source": "ads"})
            post({"name": "Bot", "email": "bot@x.com", "website_url": "spam"})
            html = urllib.request.urlopen(base + "/admin?token=tok").read().decode()
            self.assertIn("Ann", html)
            self.assertNotIn("Bot", html)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/admin?token=wrong")
            httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
