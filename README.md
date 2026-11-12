# Leads

A reusable lead-generation system: one codebase, one config folder per business.

```
leadgen/                 toolkit (Python 3.9+; standard library, plus pyarrow for the Overture source)
  sources/               overture (free, no key, worldwide), osm (free), places (Google Maps), apollo, reddit
  app.py                 interactive Lead Finder (browser)
  enrich.py              finds emails on leads' own websites
  server.py              inbound landing page + form endpoint + dashboard
businesses/<slug>/       business.json, MARKET.md (+ leads.db, exports/ - gitignored)
businesses/fruits/       worked example: fruit supplier, market overview, 16 fictional sample leads
.claude/skills/leadgen/  Claude skill that drives the workflow (plain Markdown; other agents can read it)
```

## Find real leads in two minutes (free, no keys)
```
pip install pyarrow                           # only extra package; needed for the free Overture source
python -m leadgen app fruits                  # open http://127.0.0.1:8000
```
Set a latitude, longitude and radius, click "Show types in this area", pick business types, click
"Find leads". The first search of an area downloads about 20-60 MB and takes about 30 seconds; after
that the area is cached. Then work the "Today" tab: open a lead, edit the message, send it yourself,
click "I sent it".

The same from the command line:
```
python -m leadgen categories fruits juice     # business types in the configured area
python -m leadgen collect fruits --source overture
python -m leadgen draft fruits --min-score 40 && python -m leadgen next fruits
```

## Try it with sample data (no network at all)
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
python -m leadgen locate "Lyon, France"       # coordinates for sources.overture.around
python -m leadgen categories acme             # pick business types for sources.overture.categories
python -m leadgen collect acme                # Overture and OpenStreetMap need no key
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

Tests: `python -m unittest discover -s tests` (48 tests, offline; the Overture tests need pyarrow).

Data: Overture Maps places are published under CDLA-Permissive-2.0 (overturemaps.org).
