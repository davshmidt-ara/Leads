"""Free local-business source: OpenStreetMap via the Overpass API. No key, no card.
Coverage of phone/website/email is patchier than Google Places - run `leadgen enrich` afterwards
to pull emails from the websites it does find.
Be gentle with the public server (one query per search, modest limits).

source config:
  {"area": "Austin", "searches": ["amenity=cafe", "shop=greengrocer"], "limit": 100, "skip_chains": true}
  area         city/region name, matched against OSM administrative boundaries. Ambiguous names
               (there are many Springfields) match all of them - use "around" instead.
  around       [lat, lon, radius_m] - search a circle instead of a named area, e.g. [30.2672, -97.7431, 15000]
  searches     OSM key=value tags (see wiki.openstreetmap.org/wiki/Map_features)
  skip_chains  drop places tagged as part of a brand (chains usually buy centrally)
"""
import re

from ._http import request_form_json

URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
SAFE = re.compile(r"^[\w .,'&:-]+$")


def _check(value, what):
    if not SAFE.match(value):
        raise ValueError(f"Unsupported characters in {what}: {value!r}")
    return value


def _query(sc, tag):
    k, _, v = tag.partition("=")
    _check(k, "tag key"), _check(v, "tag value")
    limit = int(sc.get("limit", 100))
    if sc.get("around"):
        lat, lon, radius = (float(x) for x in sc["around"])
        return (f'[out:json][timeout:60];nwr["{k}"="{v}"]["name"](around:{radius:.0f},{lat},{lon});'
                f'out tags center {limit};')
    return (f'[out:json][timeout:60];area["name"="{_check(sc["area"], "area")}"]["boundary"="administrative"]->.a;'
            f'nwr["{k}"="{v}"]["name"](area.a);out tags center {limit};')


def _fetch(query):
    err = None
    for url in URLS:
        try:
            return request_form_json(url, {"data": query})
        except OSError as e:  # try the mirror
            err = e
    raise err


def collect(cfg, sc):
    if not sc.get("area") and not sc.get("around"):
        raise ValueError("osm source needs \"area\" or \"around\"")
    where = sc.get("area") or "the search radius"
    for tag in sc.get("searches", []):
        for el in _fetch(_query(sc, tag)).get("elements", []):
            t = el.get("tags", {})
            if sc.get("skip_chains") and (t.get("brand") or t.get("brand:wikidata")):
                continue
            addr = " ".join(filter(None, [t.get("addr:housenumber"), t.get("addr:street"),
                                          t.get("addr:city") or sc.get("area"), t.get("addr:postcode")]))
            extra = "; ".join(f"{k}: {t[k]}" for k in ("cuisine", "opening_hours", "operator") if t.get(k))
            yield {
                "company": t.get("name"), "name": t.get("name"),
                "phone": t.get("phone") or t.get("contact:phone"),
                "email": t.get("email") or t.get("contact:email"),
                "website": t.get("website") or t.get("contact:website"),
                "location": addr,
                "url": f"https://www.openstreetmap.org/{el.get('type')}/{el.get('id')}",
                "signal": f"OpenStreetMap {tag} in {where}",
                "notes": extra,
            }
