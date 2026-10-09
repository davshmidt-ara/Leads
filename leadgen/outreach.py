"""Outreach drafting from templates in business.json. Drafts only - nothing is sent.

Template placeholders: {first_name} {name} {company} {signal} {offer} {cta} {sender} {business}
"""
import re


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
    "email": "Subject: Quick question, {company}\n\nHi {first_name},\n\n{offer}\n\n{cta}\n\n{sender}\n\n"
             "(Not relevant? Reply 'stop' and I won't contact you again.)",
    "dm": "Hi {first_name}, {offer} {cta}",
    "call": "Call opener: Hi {first_name}, this is {sender} from {business}. {offer} {cta}",
    "community": "Helpful reply to: \"{signal}\"\n\nShare genuinely useful advice first; mention {business} only if "
                 "it directly helps and the subreddit allows it. {offer}",
}


def draft(lead, cfg):
    o = cfg.get("outreach", {})
    tpl = o.get("templates", {}).get(channel_for(lead)) or DEFAULTS[channel_for(lead)]
    name = lead.get("name") or ""
    first = name.split()[0] if name and lead["source"] != "places" else (lead.get("company") or "there")
    vals = _Safe(first_name=first, name=name, company=lead.get("company") or "your team",
                 signal=lead.get("signal") or "", offer=cfg["offer"].get("summary", ""),
                 cta=cfg["offer"].get("cta", ""), sender=o.get("sender", ""), business=cfg["name"])
    text = tpl.format_map(vals)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def draft_all(store, cfg, min_score=0, limit=None, force=False):
    n = 0
    for lead in store.all(min_score=min_score, limit=limit):
        if lead["status"] in ("do_not_contact", "lost") or (lead["draft"] and not force):
            continue
        store.update(lead["id"], draft=draft(lead, cfg))
        n += 1
    return n
