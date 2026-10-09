# Leads

A reusable lead-generation system: one codebase, one config folder per business.

```
leadgen/                 toolkit (stdlib only, Python 3.9+)
  sources/               places (Google Maps), apollo (B2B people), reddit (intent posts)
  server.py              inbound landing page + form endpoint + dashboard
businesses/<slug>/       business.json (+ leads.db, exports/ - gitignored)
.claude/skills/leadgen/  Claude skill that drives the workflow
```

## Quick start
```
python -m leadgen new acme            # scaffold from businesses/example
$EDITOR businesses/acme/business.json
export GOOGLE_PLACES_API_KEY=...      # and/or APOLLO_API_KEY
python -m leadgen collect acme
python -m leadgen draft acme --min-score 40
python -m leadgen export acme --min-score 40
LEADGEN_ADMIN_TOKEN=secret python -m leadgen serve acme   # http://127.0.0.1:8000
```

## How it works
1. **Collect** from four channels: local directories, B2B people search, community intent posts, inbound form (plus CSV import for any other list).
2. **Dedupe** by email > profile URL > website domain > phone > name.
3. **Score** 0-100 with transparent rules from the business's ICP (`hot` >= 70, `warm` >= 40, tunable).
4. **Draft** per-channel outreach (email / DM / call / community reply). Nothing is auto-sent.
5. **Track** status (new -> contacted -> replied -> qualified -> won/lost/do_not_contact) in the dashboard or CSV.

## Adding a source
Create `leadgen/sources/<name>.py` with `collect(cfg, source_cfg)` yielding dicts with any of
`name, company, title, email, phone, website, location, url, signal, notes`, and register it in `sources/__init__.py`.

Tests: `python -m unittest discover -s tests`.
