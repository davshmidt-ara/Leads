"""Per-business configuration. One folder per business: businesses/<slug>/business.json"""
import json
import os
import re
import string

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
        try:
            cfg = json.load(f)
        except json.JSONDecodeError as ex:
            raise ConfigError(f"{path} is not valid JSON: {ex}") from ex
    for key in ("name", "offer", "icp"):
        if key not in cfg:
            raise ConfigError(f"business.json is missing required key '{key}'")
    cfg["slug"] = slug
    cfg["scoring"] = {**DEFAULT_SCORING, **cfg.get("scoring", {})}
    cfg.setdefault("sources", {})
    cfg.setdefault("outreach", {})
    cfg.setdefault("landing", {})
    return cfg


KNOWN_SOURCES = ("overture", "osm", "places", "apollo", "reddit")
PLACEHOLDERS = {"first_name", "name", "company", "signal", "offer", "pitch", "cta", "sender", "business", "location"}
SOURCE_KEYS = {"places": "GOOGLE_PLACES_API_KEY", "apollo": "APOLLO_API_KEY"}


def validate(cfg):
    """Human-readable problems with a business config. Empty list = good to go."""
    probs = []
    if not cfg["offer"].get("summary"):
        probs.append("offer.summary is empty - outreach drafts will have no pitch")
    if not cfg["offer"].get("cta"):
        probs.append("offer.cta is empty - drafts will not ask for anything")
    if not cfg.get("outreach", {}).get("sender"):
        probs.append("outreach.sender is empty - drafts will be unsigned")
    icp = cfg["icp"]
    if not (icp.get("segments") or icp.get("keywords_include") or icp.get("titles")):
        probs.append("icp has no segments, keywords_include or titles - every lead will score the same")
    for i, seg in enumerate(icp.get("segments", [])):
        if not seg.get("name") or not seg.get("keywords"):
            probs.append(f"icp.segments[{i}] needs a name and keywords")
    for name in cfg["sources"]:
        if name not in KNOWN_SOURCES:
            probs.append(f"sources.{name} is not a known source ({', '.join(KNOWN_SOURCES)})")
    if not cfg["sources"]:
        probs.append("no sources enabled - only CSV import and the inbound form will add leads")
    osm = cfg["sources"].get("osm")
    if osm is not None and not (osm.get("area") or osm.get("around")):
        probs.append("sources.osm needs \"area\" or \"around\"")
    ov = cfg["sources"].get("overture")
    if ov is not None:
        if not (ov.get("around") or ov.get("bbox")):
            probs.append('sources.overture needs "around": [lat, lon, radius_m]')
        if not ov.get("categories"):
            probs.append("sources.overture needs \"categories\" (list them with: python -m leadgen categories <slug>)")
    tpls = dict(cfg.get("outreach", {}).get("templates", {}))
    for seg in icp.get("segments", []):
        tpls.update({f"{seg.get('name')}.{k}": v for k, v in seg.get("templates", {}).items()})
    for key, tpl in tpls.items():
        try:
            unknown = {f for _, f, _, _ in string.Formatter().parse(tpl) if f} - PLACEHOLDERS
        except ValueError as ex:
            probs.append(f"template {key}: {ex}")
            continue
        if unknown:
            probs.append(f"template {key} uses unknown placeholders: {', '.join(sorted(unknown))}")
    return probs


def db_path(slug):
    return os.path.join(business_dir(slug), "leads.db")


def env(name):
    val = os.environ.get(name)
    if not val:
        raise ConfigError(f"Environment variable {name} is not set")
    return val
