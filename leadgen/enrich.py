"""Find a contact email for leads that have a website but no email.

Reads the homepage plus up to two contact/about pages of each lead's own public website.
Polite by design: honours robots.txt, small page budget, a pause between sites.
"""
import html
import ipaddress
import os
import re
import socket
import time
import urllib.parse
import urllib.request
import urllib.robotparser

from .db import domain
from .sources._http import UA

EMAIL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
LINK = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"'#]+)["'][^>]*>(.*?)</a>""", re.I | re.S)
CONTACT_HINT = re.compile(r"contact|about|kontakt|impressum|get-in-touch|reach-us|team|catering|wholesale", re.I)
JUNK_DOMAINS = {"example.com", "domain.com", "email.com", "yourdomain.com", "sentry.io", "wixpress.com",
                "sentry-next.wixpress.com", "godaddy.com", "squarespace.com", "wix.com", "schema.org",
                "w3.org", "google.com", "gstatic.com"}
JUNK_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")
AVOID = ("noreply", "no-reply", "donotreply", "privacy", "abuse", "careers", "jobs", "press", "legal", "webmaster")
PREFER = ("owner", "purchasing", "orders", "catering", "manager", "hello", "info", "contact", "office", "sales")
MAX_BYTES = 400_000


def _public(host):
    """Refuse loopback/private addresses unless explicitly allowed (tests, intranets)."""
    if os.environ.get("LEADGEN_ALLOW_PRIVATE") == "1":
        return True
    try:
        return all(ipaddress.ip_address(info[4][0]).is_global for info in socket.getaddrinfo(host, None))
    except (OSError, ValueError):
        return False


def fetch(url, timeout=10, html_only=True):
    """Return page text, or '' on any failure (blocked, not HTML, timeout...)."""
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or not _public(parts.hostname):
        return ""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if html_only and "html" not in (resp.headers.get("Content-Type") or "html"):
                return ""
            return resp.read(MAX_BYTES).decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""


def extract_emails(page, site_domain=""):
    """Emails found in a page, best first: same domain as the site, useful mailboxes, then the rest."""
    text = html.unescape(urllib.parse.unquote(page))
    text = re.sub(r"\s*(\[at\]|\(at\))\s*", "@", text, flags=re.I)
    found = []
    for m in EMAIL.findall(text):
        e = m.strip(".").lower()
        dom = e.split("@")[1]
        if e.endswith(JUNK_EXT) or dom in JUNK_DOMAINS or any(dom.endswith("." + j) for j in JUNK_DOMAINS):
            continue
        if re.fullmatch(r"[0-9a-f]{16,}", e.split("@")[0]):  # tracking hashes
            continue
        if e not in found:
            found.append(e)

    def rank(e):
        local, dom = e.split("@")
        same = 0 if site_domain and (dom == site_domain or dom.endswith("." + site_domain)) else 1
        avoid = 1 if any(a in local for a in AVOID) else 0
        pref = next((i for i, p in enumerate(PREFER) if local.startswith(p)), len(PREFER))
        return (avoid, same, pref)

    return sorted(found, key=rank)


def contact_links(page, base):
    host = urllib.parse.urlparse(base).netloc
    out = []
    for href, label in LINK.findall(page):
        if href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue
        if CONTACT_HINT.search(href) or CONTACT_HINT.search(re.sub(r"<[^>]+>", "", label)):
            url = urllib.parse.urljoin(base, href)
            if urllib.parse.urlparse(url).netloc == host and url not in out and url.rstrip("/") != base.rstrip("/"):
                out.append(url)
    return out[:2]


def _allowed(url):
    parts = urllib.parse.urlparse(url)
    rp = urllib.robotparser.RobotFileParser()
    robots = fetch(f"{parts.scheme}://{parts.netloc}/robots.txt", timeout=5, html_only=False)
    if not robots or "<html" in robots.lower():
        return lambda u: True
    rp.parse(robots.splitlines())
    return lambda u: rp.can_fetch(UA, u)


def find_email(website):
    url = website if re.match(r"^https?://", website, re.I) else "https://" + website
    allowed = _allowed(url)
    if not allowed(url):
        return None
    site = domain(url)
    home = fetch(url)
    emails = extract_emails(home, site)
    if not (emails and emails[0].endswith("@" + site)):
        for link in contact_links(home, url):
            if allowed(link):
                emails = extract_emails(home + fetch(link), site) or emails
                if emails and emails[0].endswith("@" + site):
                    break
    emails = [e for e in emails if not any(a in e.split("@")[0] for a in AVOID)]
    return emails[0] if emails else None


def enrich_all(store, min_score=0, limit=50, pause=1.0, log=print):
    """Fill in missing emails. Returns (sites checked, emails found)."""
    todo = [l for l in store.all(min_score=min_score)
            if l["website"] and not l["email"] and not l["enriched_at"]
            and l["status"] not in ("do_not_contact", "lost")][:limit]
    found = 0
    for i, lead in enumerate(todo):
        if i and pause:
            time.sleep(pause)
        email = find_email(lead["website"])
        fields = {"enriched_at": time.time()}
        if email:
            fields["email"] = email
            fields["draft"] = None  # channel changes from call/dm to email: redraft
            found += 1
        store.update(lead["id"], **fields)
        log(f"  {lead['company'] or lead['name']}: {email or 'no email found'}")
    return len(todo), found
