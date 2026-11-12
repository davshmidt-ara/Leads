"""Offline test of the Overture source: a fake S3 bucket (listing + HTTP Range) serving a real Parquet file."""
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from leadgen import geo
from leadgen.sources import overture

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # the source is optional; so is its test
    pa = None


def place(i, name, cat, lon, lat, **kw):
    return {
        "id": f"id-{i}", "names": {"primary": name}, "basic_category": cat,
        "taxonomy": {"primary": cat, "hierarchy": kw.get("hier", ["food_and_drink", cat]), "alternates": None},
        "confidence": kw.get("conf", 0.9), "websites": kw.get("web"), "socials": kw.get("socials"),
        "emails": kw.get("emails"), "phones": kw.get("phones"),
        "brand": {"wikidata": kw.get("wd"), "names": {"primary": kw.get("brand")}},
        "addresses": [{"freeform": f"{i} Main St", "locality": "Testville", "postcode": "00001", "region": "TX", "country": "US"}],
        "operating_status": kw.get("status", "open"),
        "bbox": {"xmin": lon, "xmax": lon, "ymin": lat, "ymax": lat},
    }


ROWS = (
    [place(i, f"Far Away Place Number {i} " + "x" * (i % 97), "cafe", -120.0 + i * 0.001, 45.0)
     for i in range(4000)]                                                                    # other row groups
    + [
        place(9100, "Zest Juice", "smoothie_juice_bar", -97.74, 30.27, emails=["investors@zest.test", "hello@zest.test"],
              web=["https://zest.test"], phones=["+15125550100"], socials=["https://facebook.com/zest"]),
        place(9101, "Corner Cafe", "cafe", -97.741, 30.271, phones=["5125550101"]),
        place(9102, "Closed Cafe", "cafe", -97.742, 30.272, status="permanently_closed", emails=["x@closed.test"]),
        place(9103, "Doubtful Cafe", "cafe", -97.743, 30.273, conf=0.2, emails=["x@doubt.test"]),
        place(9104, "Tyre Depot", "tire_shop", -97.744, 30.274, hier=["automotive", "tire_shop"], emails=["t@tyre.test"]),
        place(9105, "Dive Inn", "dive_bar", -97.745, 30.275, hier=["food_and_drink", "bar", "dive_bar"], emails=["d@dive.test"]),
        place(9106, "Edge Cafe", "cafe", -97.60, 30.27, emails=["e@edge.test"]),                    # in box, outside radius
    ]
    + [place(9200 + i, "MegaBrew", "cafe", -97.74 + i * 0.0001, 30.27, wd="Q1", brand="MegaBrew",
             emails=[f"s{i}@megabrew.test"] if i == 2 else None) for i in range(5)]
)


@unittest.skipIf(pa is None, "pyarrow not installed")
class OvertureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.key = "release/2026-01-01.0/theme=places/type=place/part-0.parquet"
        cls.path = os.path.join(cls.dir, "part-0.parquet")
        pq.write_table(pa.Table.from_pylist(ROWS), cls.path, row_group_size=1000)
        size = os.path.getsize(cls.path)
        cls.requests = requests = []

        class S3(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Range")))
                if self.path.startswith("/?"):
                    if "delimiter" in self.path:
                        body = b"<R><Prefix>release/</Prefix><Prefix>release/2025-12-01.0/</Prefix><Prefix>release/2026-01-01.0/</Prefix></R>"
                    else:
                        body = f"<R><Contents><Key>{cls.key}</Key><ETag>x</ETag><Size>{size}</Size></Contents></R>".encode()
                    self.send_response(200)
                else:
                    a, b = self.headers["Range"].split("=")[1].split("-")
                    with open(cls.path, "rb") as f:
                        f.seek(int(a))
                        body = f.read(int(b) - int(a) + 1)
                    self.send_response(206)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), S3)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.bucket = f"http://127.0.0.1:{cls.httpd.server_address[1]}/"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        for p in (mock.patch.object(overture, "BUCKET", self.bucket),
                  mock.patch.object(overture, "CACHE", tempfile.mkdtemp())):
            p.start()
            self.addCleanup(p.stop)
        self.sc = {"around": [30.27, -97.74, 3000], "categories": ["smoothie_juice_bar", "cafe", "bar"]}
        self.quiet = lambda m: None

    def collect(self, **kw):
        return list(overture.collect({}, {**self.sc, **kw}, log=self.quiet))

    def test_latest_release(self):
        self.assertEqual(overture.latest_release(), "2026-01-01.0")

    def test_collect_filters_and_maps_fields(self):
        leads = {l["name"]: l for l in self.collect()}
        # closed, low-confidence, wrong category, out-of-radius and far-away rows are all dropped
        self.assertEqual(sorted(leads), ["Corner Cafe", "Dive Inn", "MegaBrew", "Zest Juice"])
        z = leads["Zest Juice"]
        self.assertEqual((z["email"], z["phone"], z["website"]), ("hello@zest.test", "+15125550100", "https://zest.test"))
        self.assertEqual(z["location"], "9100 Main St, Testville, TX 00001")
        self.assertIn("smoothie juice bar", z["signal"])
        self.assertIn("social: https://facebook.com/zest", z["notes"])
        self.assertTrue(z["url"].startswith("https://www.google.com/maps/search/"))
        # five MegaBrew branches collapse into one lead, keeping the branch that has an email
        self.assertEqual(leads["MegaBrew"]["email"], "s2@megabrew.test")
        self.assertIn("5 locations in area", leads["MegaBrew"]["notes"])
        # "bar" matches through the category hierarchy
        self.assertIn("Dive Inn", leads)

    def test_priority_order_caps_and_chains(self):
        self.assertEqual([l["name"] for l in self.collect()][0], "Zest Juice")           # first category first
        self.assertEqual(len(self.collect(per_category=1)), 3)
        self.assertEqual(len(self.collect(limit=2)), 2)
        self.assertNotIn("MegaBrew", [l["name"] for l in self.collect(skip_chains=True)])
        self.assertIn("Doubtful Cafe", [l["name"] for l in self.collect(min_confidence=0.1)])
        self.assertEqual([l["name"] for l in self.collect(categories=["*juice*"])], ["Zest Juice"])

    def test_only_needed_bytes_are_fetched_then_cached(self):
        self.requests.clear()
        self.collect()
        ranged = [r for p, r in self.requests if r]
        fetched = sum(int(b) - int(a) + 1 for a, b in (r.split("=")[1].split("-") for r in ranged))
        self.assertTrue(ranged)
        self.assertLess(fetched, os.path.getsize(self.path) / 2)    # the far-away row groups were skipped
        self.requests.clear()
        self.collect()
        self.assertEqual([r for p, r in self.requests if r], [])     # second run served from the cache

    def test_categories_listing(self):
        cats = dict((c, (n, m)) for c, n, m in overture.categories(self.sc, log=self.quiet))
        self.assertEqual(cats["cafe"], (7, 2))                       # open cafes in radius; two have email
        self.assertEqual(cats["smoothie_juice_bar"], (1, 1))
        self.assertEqual([c for c, _, _ in overture.categories(self.sc, "juice", log=self.quiet)], ["smoothie_juice_bar"])

    def test_bad_config(self):
        with self.assertRaises(ValueError):
            self.collect(categories=[])
        with self.assertRaises(ValueError):
            list(overture.collect({}, {"categories": ["cafe"]}, log=self.quiet))
        with self.assertRaises(ValueError):
            list(overture.collect({}, {"around": [30, -97, 900_000], "categories": ["cafe"]}, log=self.quiet))


class GeoTests(unittest.TestCase):
    def test_bbox_and_distance(self):
        w, s, e, n = geo.bbox_around(30.0, -97.0, 10_000)
        self.assertAlmostEqual(geo.distance_m(30.0, -97.0, n, -97.0), 10_000, delta=60)
        self.assertAlmostEqual(geo.distance_m(30.0, -97.0, 30.0, e), 10_000, delta=60)
        self.assertTrue(w < -97.0 < e and s < 30.0 < n)

    def test_locate_parses_nominatim(self):
        resp = [{"display_name": "Austin, Texas, United States", "lat": "30.2711286", "lon": "-97.7436995"}]
        with mock.patch.object(geo, "request_json", return_value=resp) as m:
            self.assertEqual(geo.locate("Austin, TX"), [{"name": "Austin, Texas, United States", "lat": 30.27113, "lon": -97.7437}])
        self.assertIn("q=Austin%2C+TX", m.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
