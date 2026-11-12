"""SQLite lead store with multi-key deduplication and touch tracking."""
import json
import re
import sqlite3
import time

STATUSES = ["new", "contacted", "replied", "qualified", "won", "lost", "do_not_contact"]
FIELDS = ["name", "company", "title", "email", "phone", "website", "location", "url", "signal", "notes"]
EXTRA = ["segment", "score", "status", "draft", "touches", "contacted_at", "enriched_at"]

# Shared hosts: a website here says nothing about which business it is, so never dedupe on it.
GENERIC_DOMAINS = {
    "facebook.com", "instagram.com", "linktr.ee", "yelp.com", "google.com", "goo.gl", "twitter.com", "x.com",
    "linkedin.com", "tiktok.com", "youtube.com", "wixsite.com", "squarespace.com", "business.site",
    "doordash.com", "ubereats.com", "grubhub.com", "toasttab.com", "square.site", "weebly.com",
    "wordpress.com", "godaddysites.com", "opentable.com", "tripadvisor.com", "booking.com",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  name TEXT, company TEXT, title TEXT,
  email TEXT, phone TEXT, website TEXT, location TEXT, url TEXT,
  signal TEXT,            -- why this lead surfaced (post title, message, query)
  notes TEXT,
  segment TEXT,
  score INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'new',
  draft TEXT,
  touches INTEGER NOT NULL DEFAULT 0,   -- outreach attempts made so far
  contacted_at REAL,                    -- time of the latest touch
  enriched_at REAL,
  raw TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lead_keys (key TEXT PRIMARY KEY, lead_id INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
"""


def domain(site):
    site = (site or "").lower().strip()
    site = re.sub(r"^https?://", "", site)
    site = re.sub(r"^www\.", "", site)
    return re.split(r"[/?#:]", site)[0]


def is_business(lead):
    """True for a company/place record, False for a named person."""
    name, company = lead.get("name"), lead.get("company")
    return not lead.get("title") and (not name or name == company)


def display_name(lead):
    return lead.get("name") or lead.get("company") or ""


def keys(lead):
    """Every identity a lead can be matched on, strongest first.
    People match on email/profile URL; businesses also match on website domain and phone,
    so the same shop found via two sources becomes one lead."""
    out = []
    email = (lead.get("email") or "").strip().lower()
    if email:
        out.append("email:" + email)
    url = re.sub(r"^https?://(www\.)?", "", (lead.get("url") or "").strip().lower()).rstrip("/")
    if url:
        out.append("url:" + url)
    if is_business(lead):
        dom = domain(lead.get("website"))
        if dom and "." in dom and not any(dom == g or dom.endswith("." + g) for g in GENERIC_DOMAINS):
            out.append("domain:" + dom)
        digits = re.sub(r"\D", "", lead.get("phone") or "")
        if len(digits) >= 7:
            out.append("phone:" + digits[-10:])
    if not out:
        nm = re.sub(r"\s+", " ", f"{lead.get('name') or ''}|{lead.get('company') or ''}".lower()).strip()
        if nm != "|":
            out.append("name:" + nm)
    return out


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._migrate()
        self.conn.executescript(SCHEMA)
        self._backfill_keys()

    def close(self):
        self.conn.close()

    def _migrate(self):
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(leads)")]
        if cols and "dedupe_key" in cols:  # v0.1 schema: rebuild without the single unique key
            self.conn.execute("ALTER TABLE leads RENAME TO leads_v1")
            self.conn.executescript(SCHEMA)
            new = [r[1] for r in self.conn.execute("PRAGMA table_info(leads)")]
            common = ",".join(c for c in new if c in cols)
            self.conn.execute(f"INSERT INTO leads ({common}) SELECT {common} FROM leads_v1")
            self.conn.execute("DROP TABLE leads_v1")
            self.conn.commit()

    def _backfill_keys(self):
        rows = self.conn.execute(
            "SELECT * FROM leads WHERE id NOT IN (SELECT lead_id FROM lead_keys)").fetchall()
        for r in rows:
            self._add_keys(r["id"], keys(dict(r)))
        if rows:
            self.conn.commit()

    def _add_keys(self, lead_id, ks):
        self.conn.executemany("INSERT OR IGNORE INTO lead_keys (key, lead_id) VALUES (?, ?)",
                              [(k, lead_id) for k in ks])

    def _find(self, ks):
        for k in ks:
            hit = self.conn.execute("SELECT lead_id FROM lead_keys WHERE key=?", (k,)).fetchone()
            if hit:
                row = self.conn.execute("SELECT * FROM leads WHERE id=?", (hit[0],)).fetchone()
                if row:
                    return row
        return None

    def add(self, source, lead):
        """Insert a lead. Returns True if new, False if it matched an existing lead
        (in which case empty fields on the existing row are filled in)."""
        clean = {f: str(lead[f]).strip() for f in FIELDS if lead.get(f) and str(lead[f]).strip()}
        ks = keys(clean)
        if not ks:
            return False
        now = time.time()
        row = self._find(ks)
        if row:
            fills = {f: v for f, v in clean.items() if not row[f]}
            if fills:
                sets = ", ".join(f"{k}=?" for k in fills)
                self.conn.execute(f"UPDATE leads SET {sets}, updated_at=? WHERE id=?",
                                  (*fills.values(), now, row["id"]))
            self._add_keys(row["id"], keys({**dict(row), **fills}))
            self.conn.commit()
            return False
        cols = ["source", "raw", "created_at", "updated_at", *clean]
        vals = [source, json.dumps(lead, default=str), now, now, *clean.values()]
        cur = self.conn.execute(
            f"INSERT INTO leads ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        self._add_keys(cur.lastrowid, ks)
        self.conn.commit()
        return True

    def get(self, lead_id):
        row = self.conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return dict(row) if row else None

    def all(self, min_score=0, status=None, limit=None, source=None):
        q, args = "SELECT * FROM leads WHERE score>=?", [min_score]
        for col, val in (("status", status), ("source", source)):
            if val:
                q += f" AND {col}=?"
                args.append(val)
        q += " ORDER BY score DESC, created_at DESC, id DESC"
        if limit:
            q += " LIMIT ?"
            args.append(limit)
        return [dict(r) for r in self.conn.execute(q, args)]

    def update(self, lead_id, **fields):
        bad = [k for k in fields if k not in FIELDS + EXTRA]
        if bad:
            raise ValueError(f"unknown lead fields: {bad}")
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE leads SET {sets}, updated_at=? WHERE id=?",
                          (*fields.values(), time.time(), lead_id))
        if any(k in fields for k in ("email", "phone", "website", "url")):
            row = self.get(lead_id)
            if row:
                self._add_keys(lead_id, keys(row))
        self.conn.commit()

    def mark(self, lead_id, status):
        """Record an outcome. 'contacted' counts as one more touch and clears the draft,
        so the next `draft` run writes the follow-up message."""
        lead = self.get(lead_id)
        if not lead:
            return False
        if status == "contacted":
            self.update(lead_id, status=status, touches=lead["touches"] + 1,
                        contacted_at=time.time(), draft=None)
        else:
            self.update(lead_id, status=status)
        return True

    def purge(self, source):
        ids = [r[0] for r in self.conn.execute("SELECT id FROM leads WHERE source=?", (source,))]
        self.conn.executemany("DELETE FROM lead_keys WHERE lead_id=?", [(i,) for i in ids])
        self.conn.execute("DELETE FROM leads WHERE source=?", (source,))
        self.conn.commit()
        return len(ids)

    def stats(self):
        by = lambda col: {r[0]: r[1] for r in self.conn.execute(
            f"SELECT {col}, COUNT(*) FROM leads GROUP BY {col}")}
        return {"total": self.conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
                "by_source": by("source"), "by_status": by("status")}

    def report(self, group="source"):
        """Funnel per source or segment: which channel actually produces replies and wins."""
        if group not in ("source", "segment"):
            raise ValueError("group must be 'source' or 'segment'")
        rows = self.conn.execute(f"""
            SELECT COALESCE({group}, '-') AS grp, COUNT(*) AS leads,
                   SUM(email IS NOT NULL) AS with_email,
                   SUM(touches > 0) AS contacted,
                   SUM(status IN ('replied','qualified','won')) AS replied,
                   SUM(status IN ('qualified','won')) AS qualified,
                   SUM(status = 'won') AS won,
                   ROUND(AVG(score)) AS avg_score
            FROM leads GROUP BY grp ORDER BY won DESC, replied DESC, leads DESC""").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["reply_rate"] = f"{100 * d['replied'] / d['contacted']:.0f}%" if d["contacted"] else "-"
            out.append(d)
        return out
