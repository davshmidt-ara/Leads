"""Per-business configuration. One folder per business: businesses/<slug>/business.json"""
import json
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "businesses")

DEFAULT_SCORING = {
    "has_email": 20,
    "has_phone": 10,
    "has_website": 5,
    "title_match": 20,
    "keyword_match": 10,      # per include keyword, capped by keyword_cap
    "keyword_cap": 30,
    "location_match": 10,
    "intent_phrase": 25,      # per intent phrase found (community/inbound signals)
    "intent_cap": 40,
    "inbound_bonus": 30,      # they raised their hand
    "exclude_penalty": 40,
    "hot": 70,
    "warm": 40,
}


class ConfigError(Exception):
    pass


def business_dir(slug):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug):
        raise ConfigError("slug must be lowercase letters, digits, - or _")
    return os.path.join(ROOT, slug)


def load(slug):
    path = os.path.join(business_dir(slug), "business.json")
    if not os.path.exists(path):
        raise ConfigError(f"No config at {path}. Create one with: python -m leadgen new {slug}")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    for key in ("name", "offer", "icp"):
        if key not in cfg:
            raise ConfigError(f"business.json is missing required key '{key}'")
    cfg["slug"] = slug
    cfg["scoring"] = {**DEFAULT_SCORING, **cfg.get("scoring", {})}
    cfg.setdefault("sources", {})
    cfg.setdefault("outreach", {})
    cfg.setdefault("landing", {})
    return cfg


def db_path(slug):
    return os.path.join(business_dir(slug), "leads.db")


def env(name):
    val = os.environ.get(name)
    if not val:
        raise ConfigError(f"Environment variable {name} is not set")
    return val
