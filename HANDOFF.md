# Handoff: lead-gen project

Read this first in a new session, then README.md and .claude/skills/leadgen/SKILL.md.

## State (v0.2)
- Branch `claude/sleepy-babbage-p875vh`. Toolkit in `leadgen/`, skill in `.claude/skills/leadgen/`.
- Worked example: `businesses/fruits/` (fictional fruit supplier, Austin TX) with `business.json`,
  `MARKET.md` and `sample_leads.csv` (16 fictional leads).
- 35 offline tests pass: `python -m unittest discover -s tests`.
- Verified by running it: import, dedupe, scoring, drafting, follow-up sequence, `next`, `report`, export,
  email enrichment against a local test site, and the landing page + dashboard in a real browser.
- NOT verified: any live source. The cloud sandbox blocks overpass-api.de, www.reddit.com, api.apollo.io
  and Google (proxy 403), and a request to allow the first two was rejected. `collect` and `enrich` have
  never returned real-world data. First thing to do on an unrestricted machine:
  `python -m leadgen doctor fruits` then `python -m leadgen collect fruits --source osm`.

## Open items
1. Run the live check above; fix whatever the real Overpass/Places/Apollo responses reveal.
2. Ask the user for their real business (offer, buyer, region); follow the skill to write its MARKET.md
   and business.json.
3. Keys as environment variables only: `GOOGLE_PLACES_API_KEY`, `APOLLO_API_KEY`. The user pasted an
   Apollo key in an earlier chat; it must be rotated. Verify Apollo's endpoint (`mixed_people/api_search`)
   on first use; the free plan may not allow it (fallback: CSV export + `import`).
4. The inbound server is for local use; real traffic needs a public host behind HTTPS.
