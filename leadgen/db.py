"""SQLite lead store with deduplication."""
import json
import re
import sqlite3
import time

STATUSES = ["new", "contacted", "replied", "qualified", "won", "lost", "do_not_contact"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dedupe_key TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL,
  name TEXT, company TEXT, title TEXT,
  email TEXT, phone TEXT, website TEXT, location TEXT, url TEXT,
  signal TEXT,            -- why this lead surfaced (post title, message, query)
  notes TEXT,
  score INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'new',
  draft TEXT,
  raw TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
"""

FIELDS = ["name", "company", "title", "email", "phone", "website", "location", "url", "signal", "notes"]


def _domain(site):
    site = (site or "").lower().strip()
    site = re.sub(r"^https?://", "", site)
    site = re.sub(r"^www\.", "", site)
    return site.split("/")[0]


def dedupe_key(lead):
    email = (lead.get("email") or "").strip().lower()
    if email:
        return "email:" + email
    if lead.get("url"):
        return "url:" + lead["url"].strip().lower().rstrip("/")
    dom = _domain(lead.get("website"))
    if dom:
        return "domain:" + dom
    digits = re.sub(r"\D", "", lead.get("phone") or "")
    if len(digits) >= 7:
        return "phone:" + digits[-10:]
    return "name:" + re.sub(r"\s+", " ", f"{lead.get('name','')}|{lead.get('company','')}".lower())


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def add(self, source, lead):
        """Insert a lead. Returns True if new, False if it was a duplicate
        (in which case empty fields on the existing row are filled in)."""
        lead = {k: (str(v).strip() if v else None) for k, v in lead.items() if k in FIELDS or k == "raw"}
        key = dedupe_key(lead)
        if key == "name:|":
            return False
        now = time.time()
        row = self.conn.execute("SELECT * FROM leads WHERE dedupe_key=?", (key,)).fetchone()
        if row:
            fills = {f: lead[f] for f in FIELDS if lead.get(f) and not row[f]}
            if fills:
                sets = ", ".join(f"{k}=?" for k in fills)
                self.conn.execute(f"UPDATE leads SET {sets}, updated_at=? WHERE id=?",
                                  (*fills.values(), now, row["id"]))
                self.conn.commit()
            return False
        cols = ["dedupe_key", "source", "raw", "created_at", "updated_at"] + [f for f in FIELDS if lead.get(f)]
        vals = [key, source, lead.get("raw") or json.dumps(lead), now, now] + [lead[f] for f in FIELDS if lead.get(f)]
        self.conn.execute(
            f"INSERT INTO leads ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        self.conn.commit()
        return True

    def all(self, min_score=0, status=None, limit=None):
        q, args = "SELECT * FROM leads WHERE score>=?", [min_score]
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY score DESC, created_at DESC"
        if limit:
            q += " LIMIT ?"
            args.append(limit)
        return [dict(r) for r in self.conn.execute(q, args)]

    def update(self, lead_id, **fields):
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE leads SET {sets}, updated_at=? WHERE id=?",
                          (*fields.values(), time.time(), lead_id))
        self.conn.commit()

    def stats(self):
        by = lambda col: {r[0]: r[1] for r in self.conn.execute(
            f"SELECT {col}, COUNT(*) FROM leads GROUP BY {col}")}
        return {"total": self.conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
                "by_source": by("source"), "by_status": by("status")}
