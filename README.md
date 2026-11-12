# Leads

A reusable lead-generation system: one codebase, one config folder per business.

```
leadgen/                 toolkit (Python 3.9+, standard library only)
  sources/               osm (free, no key), places (Google Maps), apollo (B2B people), reddit (intent posts)
  enrich.py              finds emails on leads' own websites
  server.py              inbound landing page + form endpoint + dashboard
businesses/<slug>/       business.json, MARKET.md (+ leads.db, exports/ - gitignored)
businesses/fruits/       worked example: fruit supplier, market overview, 16 fictional sample leads
.claude/skills/leadgen/  Claude skill that drives the workflow (plain Markdown; other agents can read it)
```

## Try it in two minutes (no keys, no network)
```
python -m leadgen import fruits businesses/fruits/sample_leads.csv --source sample
python -m leadgen draft fruits --min-score 40
python -m leadgen next fruits                 # today's prioritised list
python -m leadgen show fruits 1               # a lead and its message
python -m leadgen mark fruits contacted 1
python -m leadgen report fruits --by segment
LEADGEN_ADMIN_TOKEN=demo python -m leadgen serve fruits    # http://127.0.0.1:8000 and /admin?token=demo
python -m leadgen purge fruits --source sample             # remove the fictional leads
```

## Your own business
```
python -m leadgen new acme                    # scaffold from businesses/example
$EDITOR businesses/acme/business.json
python -m leadgen doctor acme                 # config, keys and network check
python -m leadgen collect acme                # OpenStreetMap works with no key
python -m leadgen enrich acme --min-score 40
python -m leadgen draft acme --min-score 40 && python -m leadgen next acme
```
Optional keys, as environment variables: `GOOGLE_PLACES_API_KEY`, `APOLLO_API_KEY`.

## How it works
1. **Collect** from local directories, B2B people search, community intent posts, the inbound form and CSV import.
2. **Dedupe**: people match on email or profile URL; businesses also on website domain and phone.
3. **Score** 0-100 with transparent rules: segment weight, contact data, title, keywords, location, intent
   (`hot` >= 70, `warm` >= 40, tunable in `scoring`).
4. **Enrich**: look for a contact email on each lead's own site (robots.txt respected).
5. **Draft** a first message and up to two follow-ups per lead, per channel, using the segment's pitch. Nothing is sent.
6. **Track** touches and status; `next` says what to do today, `report` shows which source and segment convert.

## Adding a source
Create `leadgen/sources/<name>.py` with `collect(cfg, source_cfg)` yielding dicts with any of
`name, company, title, email, phone, website, location, url, signal, notes`, register it in
`sources/__init__.py` and add the name to `KNOWN_SOURCES` in `config.py`.

Tests: `python -m unittest discover -s tests` (35 tests, offline).
