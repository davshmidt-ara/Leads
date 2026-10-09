"""B2B decision makers via Apollo.io people search. Needs APOLLO_API_KEY.
Swap this file for any provider (Hunter, PDL, Lusha...) - just yield the same lead dict shape.
Verify the endpoint/params against Apollo's current API docs for your plan.

source config: {"titles": ["Owner","Founder"], "locations": ["Texas, US"],
                "employee_ranges": ["1,50"], "keywords": ["dental"], "pages": 3}
"""
from .. import config
from ._http import request_json

URL = "https://api.apollo.io/api/v1/mixed_people/api_search"


def collect(cfg, sc):
    key = config.env("APOLLO_API_KEY")
    for page in range(1, sc.get("pages", 1) + 1):
        body = {
            "person_titles": sc.get("titles") or cfg["icp"].get("titles", []),
            "person_locations": sc.get("locations", []),
            "organization_num_employees_ranges": sc.get("employee_ranges", []),
            "q_keywords": " ".join(sc.get("keywords", [])),
            "page": page, "per_page": 100,
        }
        resp = request_json(URL, "POST", {"X-Api-Key": key}, body)
        people = resp.get("people", [])
        if not people:
            break
        for p in people:
            org = p.get("organization") or {}
            yield {
                "name": " ".join(filter(None, [p.get("first_name"), p.get("last_name")])),
                "title": p.get("title"),
                "company": org.get("name"),
                "email": p.get("email") if "not_unlocked" not in (p.get("email") or "") else None,
                "website": org.get("website_url"),
                "url": p.get("linkedin_url"),
                "location": ", ".join(filter(None, [p.get("city"), p.get("state"), p.get("country")])),
                "signal": "B2B people search",
            }
