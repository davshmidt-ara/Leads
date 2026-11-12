"""Outreach drafting from templates in business.json. Drafts only - nothing is sent.

Sequence: first message, then one follow-up per entry in outreach.followup_days
(default [3, 7] = follow up 3 days after the first touch, then 7 days after the second).

Template keys (outreach.templates, or per segment in icp.segments[].templates):
  email, email_followup_1, email_followup_2, dm, dm_followup_1, call, call_followup_1, community
Placeholders: {first_name} {name} {company} {signal} {offer} {pitch} {cta} {sender} {business} {location}
  {pitch} is the matched segment's "pitch" line, falling back to the offer summary.
"""
import re
import time

from .db import is_business
from .scoring import segment_for

DAY = 86400


class _Safe(dict):
    def __missing__(self, key):
        return ""


def channel_for(lead):
    if lead["source"] == "reddit":
        return "community"
    if lead.get("email"):
        return "email"
    if lead.get("phone"):
        return "call"
    return "dm"


DEFAULTS = {
    "email": "Subject: Quick question for {company}\n\nHi {first_name},\n\n{pitch}\n\n{cta}\n\n{sender}\n\n"
             "(Not relevant? Reply 'stop' and I won't contact you again.)",
    "email_followup_1": "Subject: Re: Quick question for {company}\n\nHi {first_name},\n\nFollowing up on my note "
                        "from a few days ago. {pitch}\n\n{cta}\n\n{sender}\n\n(Reply 'stop' and I won't email again.)",
    "email_followup_2": "Subject: Re: Quick question for {company}\n\nHi {first_name},\n\nLast note from me - I don't "
                        "want to crowd your inbox. If the timing is wrong, just say so and I'll check back later. "
                        "{cta}\n\n{sender}\n\n(Reply 'stop' and I won't email again.)",
    "dm": "Hi {first_name}, {pitch} {cta}",
    "dm_followup_1": "Hi {first_name}, just bumping this in case it got buried. {cta}",
    "call": "Call opener: Hi, this is {sender} from {business}. Who handles purchasing at {company}? ... {pitch} {cta}",
    "call_followup_1": "Second call: Hi, {sender} from {business} again - I called earlier this week. {cta}",
    "community": "Helpful reply to: \"{signal}\"\n\nShare genuinely useful advice first; mention {business} only if "
                 "it directly helps and the community allows it. {offer}",
}


def followup_days(cfg):
    return cfg.get("outreach", {}).get("followup_days", [3, 7])


def max_touches(lead, cfg):
    return 1 if channel_for(lead) == "community" else 1 + len(followup_days(cfg))


def is_due(lead, cfg, now=None):
    """A contacted lead with no reply whose next follow-up date has arrived."""
    if lead["status"] != "contacted" or not lead["touches"] or lead["touches"] >= max_touches(lead, cfg):
        return False
    wait = followup_days(cfg)[lead["touches"] - 1] * DAY
    return (now or time.time()) - (lead["contacted_at"] or 0) >= wait


def is_stale(lead, cfg, now=None):
    """All touches used and still no reply a week later: close it out."""
    return (lead["status"] == "contacted" and lead["touches"] >= max_touches(lead, cfg)
            and (now or time.time()) - (lead["contacted_at"] or 0) >= 7 * DAY)


def first_name(lead):
    name = lead.get("name") or ""
    if is_business(lead) or lead.get("source") == "reddit" or not name:
        return "there"
    return name.split()[0]


def draft(lead, cfg):
    o = cfg.get("outreach", {})
    seg = segment_for(lead, cfg) or {}
    channel, step = channel_for(lead), lead.get("touches") or 0
    key = channel if step == 0 else f"{channel}_followup_{step}"
    tpl = None
    for source in (seg.get("templates", {}), o.get("templates", {}), DEFAULTS):
        tpl = source.get(key)
        if tpl:
            break
    if not tpl:  # e.g. no dm_followup_2: reuse the last follow-up we do have
        tpl = DEFAULTS.get(f"{channel}_followup_1") or DEFAULTS[channel]
    vals = _Safe(first_name=first_name(lead), name=lead.get("name") or "",
                 company=lead.get("company") or lead.get("name") or "your team",
                 signal=lead.get("signal") or "", offer=cfg["offer"].get("summary", ""),
                 pitch=seg.get("pitch") or cfg["offer"].get("summary", ""),
                 cta=cfg["offer"].get("cta", ""), sender=o.get("sender", ""), business=cfg["name"],
                 location=lead.get("location") or "")
    text = tpl.format_map(vals)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def draft_all(store, cfg, min_score=0, limit=None, force=False):
    """Write first messages for new leads and follow-ups for leads that are due."""
    n = 0
    for lead in store.all(min_score=min_score):
        if limit and n >= limit:
            break
        wanted = lead["status"] == "new" or is_due(lead, cfg)
        if not wanted or (lead["draft"] and not force):
            continue
        store.update(lead["id"], draft=draft(lead, cfg))
        n += 1
    return n


def agenda(store, cfg, limit=15, now=None):
    """Today's work, in priority order."""
    leads = store.all()
    new = [l for l in leads if l["status"] == "new"]
    return {
        "reply_now": [l for l in leads if l["status"] == "replied"],
        "inbound": [l for l in new if l["source"] == "inbound"],
        "followups_due": [l for l in leads if is_due(l, cfg, now)],
        "first_touch": [l for l in new if l["source"] != "inbound"
                        and l["score"] >= cfg["scoring"]["warm"]][:limit],
        "close_out": [l for l in leads if is_stale(l, cfg, now)],
    }
