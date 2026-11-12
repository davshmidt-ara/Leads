"""Free worldwide business directory: Overture Maps "places" (overturemaps.org), read straight
from its public Amazon S3 bucket. No key, no account. Open licence (CDLA-Permissive-2.0).
Around 60+ million places with name, category, address and - where known - phone, website,
email and social links. Released monthly.

Needs one extra package:  pip install pyarrow

source config:
  {"around": [30.2672, -97.7431, 15000],            # lat, lon, radius in metres (or "bbox": [w, s, e, n])
   "categories": ["smoothie_juice_bar", "*cafe*"],   # Overture category names, wildcards allowed
   "min_confidence": 0.6, "skip_chains": false, "per_category": 150, "limit": 1000}
  categories are in priority order: each gets up to per_category leads (best-documented first),
  earlier categories fill first, and the total stops at limit.
List the categories that exist in your area with:  python -m leadgen categories <slug> [text]

How it works: the dataset is ~12 GB of Parquet files sorted by location. Only the file footers and
the few blocks covering your area are downloaded (about 20 MB for a large city), then cached under
.cache/ so later searches of the same area are instant.
"""
import fnmatch
import gzip
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .. import geo
from ..db import domain
from ._http import UA

BUCKET = "https://overturemaps-us-west-2.s3.amazonaws.com/"
CACHE = os.environ.get("LEADGEN_CACHE") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".cache")
COLUMNS = ["id", "names", "basic_category", "taxonomy", "confidence", "websites", "socials", "emails",
           "phones", "brand", "addresses", "operating_status", "bbox"]
MAX_AREA_DEG2 = 4.0  # refuse boxes much bigger than a metro area (downloads would be huge)


class OvertureError(OSError):
    pass


def _get(url, headers=None, timeout=60, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except OSError as ex:
            if attempt == tries - 1 or "403" in str(ex):
                raise OvertureError(f"cannot read Overture data from {urllib.parse.urlparse(url).netloc}: {ex}") from ex
            time.sleep(1 + attempt)


def _list(prefix, delimiter=""):
    out, token = [], ""
    while True:
        q = {"list-type": "2", "prefix": prefix}
        if delimiter:
            q["delimiter"] = delimiter
        if token:
            q["continuation-token"] = token
        xml = _get(BUCKET + "?" + urllib.parse.urlencode(q)).decode()
        out += re.findall(r"<Prefix>([^<]+)</Prefix>", xml) if delimiter else \
            re.findall(r"<Key>([^<]+)</Key>.*?<Size>(\d+)</Size>", xml, re.S)
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", xml)
        if not m:
            return out
        token = m.group(1)


def latest_release():
    rels = sorted(p.split("/")[1] for p in _list("release/", "/") if p != "release/")
    if not rels:
        raise OvertureError("no Overture releases found")
    return rels[-1]


class RangeFile(io.RawIOBase):
    """A remote file read with HTTP Range requests, so pyarrow fetches only the bytes it needs."""

    def __init__(self, url, size):
        self.url, self.size, self.pos, self.fetched = url, size, 0, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def read(self, n=-1):
        if n < 0:
            n = self.size - self.pos
        if n == 0 or self.pos >= self.size:
            return b""
        data = _get(self.url, {"Range": f"bytes={self.pos}-{min(self.pos + n, self.size) - 1}"})
        self.pos += len(data)
        self.fetched += len(data)
        return data

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)


def _slim(r):
    """Keep only what lead generation needs from one Overture place record."""
    tax = r.get("taxonomy") or {}
    cats = [c for c in [tax.get("primary"), r.get("basic_category"), *(tax.get("hierarchy") or []),
                        *(tax.get("alternates") or [])] if c]
    brand = r.get("brand") or {}
    addr = (r.get("addresses") or [{}])[0] or {}
    box = r.get("bbox") or {}
    return {
        "id": r.get("id"), "name": ((r.get("names") or {}).get("primary") or "").strip(),
        "cat": cats[0] if cats else None, "cats": list(dict.fromkeys(cats)),
        "conf": round(r.get("confidence") or 0, 2),
        "web": (r.get("websites") or [None])[0], "socials": r.get("socials") or [],
        "emails": (r.get("emails") or [])[:4], "phone": (r.get("phones") or [None])[0],
        "brand": (brand.get("names") or {}).get("primary"), "brand_wd": brand.get("wikidata"),
        "addr": addr.get("freeform"), "city": addr.get("locality"), "region": addr.get("region"),
        "postcode": addr.get("postcode"), "country": addr.get("country"),
        "status": r.get("operating_status"), "lon": box.get("xmin"), "lat": box.get("ymin"),
    }


def _scan_file(key, size, box):
    import pyarrow.parquet as pq
    w, s, e, n = box
    f = RangeFile(BUCKET + key, int(size))
    pf = pq.ParquetFile(f, pre_buffer=True)
    md = pf.metadata
    ix = {md.schema.column(i).path: i for i in range(md.num_columns)}

    def overlaps(rg):
        st = {c: rg.column(ix[f"bbox.{c}"]).statistics for c in ("xmin", "xmax", "ymin", "ymax")}
        if any(v is None or not v.has_min_max for v in st.values()):
            return True
        return st["xmin"].min <= e and st["xmax"].max >= w and st["ymin"].min <= n and st["ymax"].max >= s

    groups = [g for g in range(md.num_row_groups) if overlaps(md.row_group(g))]
    rows = []
    if groups:
        cols = [c for c in COLUMNS if c in pf.schema_arrow.names]
        for r in pf.read_row_groups(groups, columns=cols).to_pylist():
            b = r.get("bbox") or {}
            if b.get("xmin") is not None and w <= b["xmin"] <= e and s <= b["ymin"] <= n and (r.get("names") or {}).get("primary"):
                rows.append(_slim(r))
    return rows, f.fetched


def area_rows(box, release=None, log=print):
    """Every place inside (west, south, east, north), from the local cache or S3."""
    try:
        import pyarrow  # noqa: F401
    except ImportError as ex:
        raise OvertureError("the overture source needs pyarrow: run  pip install pyarrow") from ex
    w, s, e, n = box
    if w >= e or s >= n or (e - w) * (n - s) > MAX_AREA_DEG2:
        raise ValueError("search area is empty or too large (keep the radius under about 100 km)")
    box = (round(w, 3) - 0.001, round(s, 3) - 0.001, round(e, 3) + 0.001, round(n, 3) + 0.001)
    release = release or latest_release()
    name = "v2_" + "_".join(f"{v:.3f}" for v in box) + ".json.gz"
    path = os.path.join(CACHE, "overture", release, name)
    if os.path.exists(path):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    files = _list(f"release/{release}/theme=places/type=place/")
    files = [(k, sz) for k, sz in files if k.endswith(".parquet")]
    if not files:
        raise OvertureError(f"no place files in Overture release {release}")
    log(f"Overture {release}: scanning {len(files)} files for your area (first time only)...")
    t = time.time()
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda fs: _scan_file(fs[0], fs[1], box), files))
    rows = [r for part, _ in results for r in part]
    mb = sum(b for _, b in results) / 1e6
    log(f"Overture: {len(rows)} places in the area ({mb:.0f} MB downloaded, {time.time() - t:.0f}s)")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(rows, fh)
    os.replace(tmp, path)
    return rows


def _box(sc):
    if sc.get("around"):
        lat, lon, radius = (float(x) for x in sc["around"])
        return geo.bbox_around(lat, lon, radius)
    if sc.get("bbox"):
        return tuple(float(x) for x in sc["bbox"])
    raise ValueError('overture source needs "around": [lat, lon, radius_m] or "bbox": [w, s, e, n]')


def in_area(sc, log=print):
    """Places inside the configured circle/box that are not known to be closed."""
    rows = area_rows(_box(sc), sc.get("release"), log)
    if sc.get("around"):
        lat, lon, radius = (float(x) for x in sc["around"])
        rows = [r for r in rows if geo.distance_m(lat, lon, r["lat"], r["lon"]) <= radius]
    return [r for r in rows if r["status"] not in ("permanently_closed", "temporarily_closed")]


def categories(sc, text="", log=print):
    """[(category, places, with_email)] in the area, most common first."""
    total, mail = Counter(), Counter()
    for r in in_area(sc, log):
        for c in r["cats"][:1] or ["(uncategorised)"]:
            total[c] += 1
            mail[c] += bool(r["emails"])
    return [(c, n, mail[c]) for c, n in total.most_common() if text.lower() in c.lower()]


def matches(row, patterns):
    return any(fnmatch.fnmatchcase(c, p) for p in patterns for c in row["cats"])


def best_email(emails, website):
    """Prefer a mailbox on the business's own domain; drop investor/press/no-reply addresses."""
    site = domain(website)
    bad = ("noreply", "no-reply", "donotreply", "privacy", "abuse", "careers", "jobs", "press", "legal",
           "webmaster", "investor", "media", "support", "billing")

    def rank(e):
        local, _, dom = e.lower().partition("@")
        return (any(a in local for a in bad), not (site and (dom == site or dom.endswith("." + site))))

    emails = [e for e in emails or [] if "@" in e and not rank(e)[0]]
    return sorted(emails, key=rank)[0].lower() if emails else None


def collect(cfg, sc, log=print):
    rows = in_area(sc, log)
    chain = Counter(r["name"].lower() for r in rows)          # same name many times in one area = chain
    chain_size = int(sc.get("chain_size", 4))
    pats = [p.lower() for p in sc.get("categories", [])]
    if not pats:
        raise ValueError('overture source needs "categories" (see: python -m leadgen categories <slug>)')
    min_conf = float(sc.get("min_confidence", 0.6))
    picked = {}
    for r in rows:
        if r["conf"] < min_conf or not matches(r, pats):
            continue
        n = chain[r["name"].lower()]
        if sc.get("skip_chains") and (n >= chain_size or r["brand_wd"]):
            continue
        r = dict(r, email=best_email(r["emails"], r["web"]))
        best = picked.get(r["name"].lower())                   # one lead per business name
        rank = (bool(r["email"]), bool(r["phone"]), bool(r["web"]), r["conf"])
        if best is None or rank > best[0]:
            picked[r["name"].lower()] = (rank, r, n)
    per_cat, limit = int(sc.get("per_category", 150)), int(sc.get("limit", 1000))
    ordered, used = [], set()
    for pat in pats:                                           # categories in priority order
        group = sorted((v for k, v in picked.items() if k not in used and matches(v[1], [pat])),
                       key=lambda x: x[0], reverse=True)[:per_cat]
        used.update(v[1]["name"].lower() for v in group)
        ordered += group
    ordered = ordered[:limit]
    for _, r, n in ordered:
        place = ", ".join(filter(None, [r["addr"], r["city"], " ".join(filter(None, [r["region"], r["postcode"]]))]))
        notes = [f"category: {r['cat']}"]
        if n > 1:
            notes.append(f"{n} locations in area")
        if r["socials"]:
            notes.append("social: " + r["socials"][0])
        yield {
            "name": r["name"], "company": r["name"], "email": r["email"], "phone": r["phone"],
            "website": r["web"], "location": place,
            "url": "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote_plus(f"{r['name']} {place}"),
            "signal": f"Overture Maps: {(r['cat'] or 'place').replace('_', ' ')}",
            "notes": "; ".join(notes),
        }
