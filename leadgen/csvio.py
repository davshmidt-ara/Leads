"""CSV import (LinkedIn Sales Navigator exports, event lists, purchased/own lists) and export."""
import csv

from .db import FIELDS

ALIASES = {
    "name": ["name", "full name", "fullname", "contact"],
    "company": ["company", "organization", "account name", "business"],
    "title": ["title", "job title", "position"],
    "email": ["email", "e-mail", "email address"],
    "phone": ["phone", "phone number", "mobile"],
    "website": ["website", "domain", "company website", "url"],
    "location": ["location", "city", "address"],
    "url": ["linkedin", "linkedin url", "profile url", "profile"],
    "notes": ["notes", "comment"],
}


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            low = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            lead = {}
            for field, names in ALIASES.items():
                for n in names:
                    if low.get(n):
                        lead[field] = low[n]
                        break
            if "name" not in lead and (low.get("first name") or low.get("last name")):
                lead["name"] = f"{low.get('first name','')} {low.get('last name','')}".strip()
            yield lead


def write(path, leads):
    cols = ["id", "score", "status", "source", *FIELDS, "draft", "created_at"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(leads)
