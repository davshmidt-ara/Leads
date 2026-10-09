"""Public posts where people ask for what you sell. No key needed (Reddit may rate-limit
or block some cloud IPs; if so, export from a different network or use an official API app).
Leads are *surfaced for a human to reply to helpfully* - never auto-post.

source config: {"subreddits": ["smallbusiness"], "queries": ["looking for a bookkeeper"], "limit": 25}
"""
import urllib.parse

from ._http import request_json


def collect(cfg, sc):
    for sub in sc.get("subreddits", []):
        for q in sc.get("queries", []):
            params = urllib.parse.urlencode(
                {"q": q, "restrict_sr": 1, "sort": "new", "limit": sc.get("limit", 25)})
            data = request_json(f"https://www.reddit.com/r/{sub}/search.json?{params}")
            for child in data.get("data", {}).get("children", []):
                d = child["data"]
                yield {
                    "name": d.get("author"),
                    "url": "https://www.reddit.com" + d.get("permalink", ""),
                    "signal": d.get("title"),
                    "notes": (d.get("selftext") or "")[:500],
                    "location": f"r/{sub}",
                }
