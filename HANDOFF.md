# Handoff: lead-gen project

Read this first in a new session, then README.md and .claude/skills/leadgen/SKILL.md.

## State
- Toolkit is built and pushed on branch `claude/sleepy-babbage-p875vh` (`leadgen/`, `businesses/example/`, skill, README, tests).
- 6 offline tests pass: `python3 -m unittest discover -s tests`.
- Sources (Google Places, Apollo, Reddit) are only tested against mocks. The cloud sandbox blocked live calls to api.apollo.io and reddit.com (proxy 403), so nothing has run against real APIs yet.
- No real business is configured yet. The user chose "build generic first"; `businesses/example/business.json` is only a sample (a bookkeeping firm).

## Decisions so far
- Format: skill + Python toolkit + web app (landing page, form endpoint, dashboard).
- Channels: local directories/Maps, B2B people search, community posts (Reddit), inbound.
- Outreach is draft-only; nothing is ever sent automatically.

## Open items
1. Ask the user for the first real business (offer, target buyer, region), then create `businesses/<slug>/business.json`.
2. Keys, set as environment variables only and never committed:
   - `GOOGLE_PLACES_API_KEY`. A card is needed for a billing account; a free monthly allowance applies.
   - `APOLLO_API_KEY`. The user pasted an old key in chat, so it must be rotated. The free plan may not allow the people-search API; the fallback is a CSV export from Apollo's UI plus `python -m leadgen import <slug> file.csv`.
3. The user wants Claude in Chrome to create the keys. This needs a session that runs on their computer (Claude Desktop app, or `claude remote-control`) with the Claude in Chrome extension installed.
4. DONE: free OpenStreetMap source (`leadgen/sources/osm.py`, mocked test only; verify live from an unrestricted network).
5. Verify the Apollo endpoint/params (`mixed_people/api_search`) against Apollo's current docs on first live run.
