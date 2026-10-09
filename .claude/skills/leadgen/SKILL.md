---
name: leadgen
description: Set up and run lead generation for a business using the leadgen toolkit in this repo. Use when the user wants more leads, a new business/campaign onboarded, prospect lists built, leads scored, outreach drafted, or an inbound landing page/dashboard run.
---

# Lead generation playbook

The toolkit lives in `leadgen/` (stdlib-only Python). Each business is one folder: `businesses/<slug>/business.json` + its own `leads.db`. Reusing this for a new business = new folder, same code.

## Onboard a new business
1. Interview the user (ask only what's missing): what they sell, who buys (titles, industries, company size, region), best customers today, what a buyer says when they need it (intent phrases), price/offer, the one call-to-action, sender name.
2. `python -m leadgen new <slug>` then edit `businesses/<slug>/business.json`:
   - `icp` drives scoring: `titles`, `keywords_include/exclude`, `locations`, `intent_phrases`.
   - `sources` enables channels (omit a key to disable): `places` (needs `GOOGLE_PLACES_API_KEY`), `apollo` (needs `APOLLO_API_KEY`), `reddit` (no key).
   - `outreach.templates` optionally overrides `email`/`dm`/`call`/`community` drafts; `landing` configures the inbound page.
3. Never commit API keys; use env vars.

## Run the loop
```
python -m leadgen collect <slug>            # pull from enabled sources, dedupe, score
python -m leadgen import <slug> file.csv    # LinkedIn/Sales Nav/other lists (auto-maps columns)
python -m leadgen draft <slug> --min-score 40
python -m leadgen export <slug> --min-score 40   # businesses/<slug>/exports/leads.csv
python -m leadgen stats <slug>
LEADGEN_ADMIN_TOKEN=... python -m leadgen serve <slug>   # landing page "/", dashboard "/admin?token="
```
Inbound: point ads/posts at the landing URL with `utm_source=...&utm_campaign=...`; leads land scored (+inbound bonus) in the same DB.

## Improve results (iterate weekly)
- Review top-scored vs. actually-replied leads; adjust `scoring` weights and `icp` keywords.
- Check `stats` by source and status to see which channel produces replies, then double down.
- Rewrite weak drafts yourself with Claude before sending; templates are a starting point.

## Rules
- Drafts only: never send emails/DMs or post to communities automatically. A human reviews and sends.
- Community leads (Reddit): reply helpfully first, follow each subreddit's self-promotion rules.
- Cold email: include a clear opt-out, honor `do_not_contact`, comply with CAN-SPAM/GDPR/CASL for the recipient's region. Scrape only public business data; respect each platform's terms (LinkedIn scraping is not supported - use exports or a licensed provider).
- Adapters are untested against live APIs in CI; verify provider endpoints/params when first enabling one.
