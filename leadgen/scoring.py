"""Transparent rule-based lead scoring driven by the business's ICP + scoring weights."""
import re


def _text(lead):
    return " ".join(str(lead.get(k) or "") for k in
                    ("name", "company", "title", "website", "location", "signal", "notes")).lower()


def has(term, text):
    """Whole-word match (plural tolerant): 'bar' matches 'bars' but not 'barber'."""
    return re.search(r"(?<!\w)" + re.escape(term.lower()) + r"(?:s|es)?(?!\w)", text) is not None


def segment_for(lead, cfg):
    """Best-weighted ICP segment whose keywords match the lead, or None."""
    text = _text(lead)
    best = None
    for seg in cfg["icp"].get("segments", []):
        if any(has(k, text) for k in seg.get("keywords", [])):
            if best is None or seg.get("weight", 0) > best.get("weight", 0):
                best = seg
    return best


def score(lead, cfg):
    w, icp = cfg["scoring"], cfg["icp"]
    text = _text(lead)
    s = 0
    if lead.get("email"):
        s += w["has_email"]
    if lead.get("phone"):
        s += w["has_phone"]
    if lead.get("website"):
        s += w["has_website"]
    if any(has(t, (lead.get("title") or "").lower()) for t in icp.get("titles", [])):
        s += w["title_match"]
    seg = segment_for(lead, cfg)
    if seg:
        s += seg.get("weight", 0)
    kw = sum(1 for k in icp.get("keywords_include", []) if has(k, text))
    s += min(kw * w["keyword_match"], w["keyword_cap"])
    if any(loc.lower() in (lead.get("location") or "").lower() for loc in icp.get("locations", [])):
        s += w["location_match"]
    hits = sum(1 for p in icp.get("intent_phrases", []) if p.lower() in text)
    s += min(hits * w["intent_phrase"], w["intent_cap"])
    if lead.get("source") == "inbound":
        s += w["inbound_bonus"]
    if any(has(k, text) for k in icp.get("keywords_exclude", [])):
        s -= w["exclude_penalty"]
    return max(0, min(100, s))


def tier(s, cfg):
    w = cfg["scoring"]
    return "hot" if s >= w["hot"] else "warm" if s >= w["warm"] else "cold"


def score_all(store, cfg):
    n = 0
    for lead in store.all():
        new = score(lead, cfg)
        seg = segment_for(lead, cfg)
        seg = seg["name"] if seg else None
        if new != lead["score"] or seg != lead["segment"]:
            store.update(lead["id"], score=new, segment=seg)
            n += 1
    return n
