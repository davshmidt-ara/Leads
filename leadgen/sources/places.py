"""Local businesses via Google Places API (New) Text Search. Needs GOOGLE_PLACES_API_KEY.

source config: {"queries": ["dentist in Austin TX"], "max_per_query": 40}
"""
from .. import config
from ._http import request_json

URL = "https://places.googleapis.com/v1/places:searchText"
MASK = ("places.displayName,places.formattedAddress,places.nationalPhoneNumber,"
        "places.websiteUri,places.googleMapsUri,places.rating,places.userRatingCount,nextPageToken")


def collect(cfg, sc):
    key = config.env("GOOGLE_PLACES_API_KEY")
    for query in sc.get("queries", []):
        token, got = None, 0
        while got < sc.get("max_per_query", 40):
            body = {"textQuery": query, "pageSize": 20}
            if token:
                body["pageToken"] = token
            resp = request_json(URL, "POST", {"X-Goog-Api-Key": key, "X-Goog-FieldMask": MASK}, body)
            for p in resp.get("places", []):
                got += 1
                rating = f"{p.get('rating')} stars / {p.get('userRatingCount')} reviews" if p.get("rating") else ""
                yield {
                    "company": (p.get("displayName") or {}).get("text"),
                    "name": (p.get("displayName") or {}).get("text"),
                    "phone": p.get("nationalPhoneNumber"),
                    "website": p.get("websiteUri"),
                    "location": p.get("formattedAddress"),
                    "url": p.get("googleMapsUri"),
                    "signal": f"Found via '{query}'",
                    "notes": rating,
                }
            token = resp.get("nextPageToken")
            if not token:
                break
