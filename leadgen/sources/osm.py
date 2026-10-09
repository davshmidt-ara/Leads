"""Free local-business source: OpenStreetMap via the Overpass API. No key, no card.
Coverage of phone/website/email is patchier than Google Places - good for a free start.
Be gentle with the public server (one query per search, modest limits).

source config: {"area": "Austin", "searches": ["amenity=restaurant", "craft=plumber"], "limit": 100}
  area     name of a city/region (matched against OSM administrative boundaries)
  searches OSM key=value tags (see wiki.openstreetmap.org/wiki/Map_features)
"""
import re

from ._http import request_form_json

URL = "https://overpass-api.de/api/interpreter"
SAFE = re.compile(r"^[\w .,'&-]+$")


def _check(value, what):
    if not SAFE.match(value):
        raise ValueError(f"Unsupported characters in {what}: {value!r}")
    return value


def _query(area, tag, limit):
    k, _, v = tag.partition("=")
    _check(k, "tag key"), _check(v, "tag value")
    return (f'[out:json][timeout:60];area["name"="{_check(area, "area")}"]["boundary"="administrative"]->.a;'
            f'nwr["{k}"="{v}"]["name"](area.a);out tags center {int(limit)};')


def collect(cfg, sc):
    area = sc["area"]
    for tag in sc.get("searches", []):
        data = request_form_json(URL, {"data": _query(area, tag, sc.get("limit", 100))})
        for el in data.get("elements", []):
            t = el.get("tags", {})
            addr = " ".join(filter(None, [t.get("addr:housenumber"), t.get("addr:street"),
                                          t.get("addr:city") or area]))
            yield {
                "company": t.get("name"), "name": t.get("name"),
                "phone": t.get("phone") or t.get("contact:phone"),
                "email": t.get("email") or t.get("contact:email"),
                "website": t.get("website") or t.get("contact:website"),
                "location": addr,
                "url": f"https://www.openstreetmap.org/{el.get('type')}/{el.get('id')}",
                "signal": f"OpenStreetMap {tag} in {area}",
            }
