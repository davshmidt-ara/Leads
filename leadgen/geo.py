"""Small geography helpers: radius -> bounding box, distance, and free place-name lookup."""
import math
import urllib.parse

from .sources._http import request_json


def bbox_around(lat, lon, radius_m):
    """(west, south, east, north) box enclosing a circle."""
    dlat = radius_m / 111_320
    dlon = radius_m / (111_320 * max(0.01, math.cos(math.radians(lat))))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def distance_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def locate(place):
    """Look a place name up with OpenStreetMap's free Nominatim service.
    Returns [{"name", "lat", "lon"}]. One request per call; do not loop over it."""
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": place, "format": "json", "limit": 5})
    return [{"name": r.get("display_name"), "lat": round(float(r["lat"]), 5), "lon": round(float(r["lon"]), 5)}
            for r in request_json(url)]
