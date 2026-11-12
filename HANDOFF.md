# Handoff: lead-gen project

Read this first in a new session, then README.md and .claude/skills/leadgen/SKILL.md.

## State (v0.3)
- Branch `claude/sleepy-babbage-p875vh`. Toolkit in `leadgen/`, skill in `.claude/skills/leadgen/`.
- Worked example: `businesses/fruits/` (fictional fruit supplier, Austin TX).
- 48 offline tests pass: `python -m unittest discover -s tests` (needs `pip install pyarrow`).

## What has run against real data
- **Overture source: yes.** Read live from the public S3 bucket (release 2026-10-21.0). For the fruit
  config it produced 1,196 real Austin leads (790 with email, 1,152 with phone). The interactive app
  (`python -m leadgen app fruits`) was driven end to end in a browser on that data.
- **Not yet:** osm (Overpass), reddit, apollo, Nominatim place lookup and website email enrichment are
  blocked by the cloud sandbox's network policy (proxy 403), so they are tested against mocks or a local
  test site only. Google Places' host is reachable but needs the user's API key.
  On an unrestricted machine run `python -m leadgen doctor fruits` first.

## Open items
1. Ask the user for their real business (offer, buyer, region); follow the skill to write its MARKET.md
   and business.json, then `categories` + `collect`.
2. Try `enrich`, `locate` and `collect --source osm` on an unrestricted network and fix what real
   responses reveal.
3. Keys as environment variables only: `GOOGLE_PLACES_API_KEY`, `APOLLO_API_KEY`. The user pasted an
   Apollo key in an earlier chat; it must be rotated.
4. The app and inbound server are for local use; real traffic needs a public host behind HTTPS and a
   `LEADGEN_ADMIN_TOKEN`.
