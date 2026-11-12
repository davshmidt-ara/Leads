---
name: leadgen
description: Run lead generation for a business with the leadgen toolkit in this repo - research the market, build and score prospect lists, find emails, draft outreach and follow-ups, run the inbound landing page and dashboard, and report what is working. Use when the user wants more leads or customers, wants a new business or campaign set up, asks who to contact today, or asks for a market overview for a business they sell to.
---

# Lead generation playbook

The toolkit is `leadgen/` (Python 3.9+, no installs). Each business is one folder,
`businesses/<slug>/`, holding `business.json` (the config), `MARKET.md` (the market overview) and its own
`leads.db`. A new business means a new folder; the code never changes.

`businesses/fruits/` is the worked example: a fruit supplier with eight customer segments, a market
overview, and 16 fictional sample leads. Read it before setting up a new business.

Run every command from the repo root as `python -m leadgen <command> <slug>`.

## 1. Set up a business

1. **Interview.** Ask only for what is missing: what they sell and the price range; who buys (job title,
   business type, size, region); their best current customers; the words a buyer uses when they need this;
   the one thing they want a lead to do next (call, sample, demo); who signs the messages.
2. **Market overview.** Research the market with web search and write `businesses/<slug>/MARKET.md` from
   `market-overview-template.md` in this skill folder. Link a source for every figure; label judgement as
   judgement. Never invent numbers. The segment ranking you reach here becomes `icp.segments`.
3. **Config.** `python -m leadgen new <slug>`, then edit `business.json`:
   - `offer.summary` and `offer.cta`: one sentence each. The CTA should be a small, concrete ask.
   - `icp.segments`: `name`, `keywords`, `weight` (10-30) and a `pitch` written for that segment. The pitch
     replaces the generic offer in drafts, so write it in the buyer's terms.
   - `icp.titles`, `keywords_include`, `keywords_exclude` (competitors, chains, bad fits), `locations`,
     `intent_phrases` (what a buyer types when looking).
   - `sources`: remove a key to disable that source.
     - `overture`: **start here.** Free, no key, worldwide: Overture Maps' open business directory
       (name, category, address, phone, website, often email). Needs `pip install pyarrow`.
       Set `around` to [lat, lon, radius_m] - you know city coordinates, or run
       `python -m leadgen locate "<city>"`. Then run `python -m leadgen categories <slug> [text]` to see
       which business types exist in that area and how many have an email, and put the wanted ones in
       `categories` in priority order (wildcards like `*restaurant*` work; a parent such as `bar` matches
       all its sub-types). `per_category` caps each type; `skip_chains` drops multi-location brands.
     - `osm`: free, no key. `area` (city name) or `around` ([lat, lon, radius_m], better for ambiguous
       names). `searches` are OpenStreetMap tags like `amenity=cafe`. `skip_chains` drops branded chains.
     - `places`: Google Places, needs `GOOGLE_PLACES_API_KEY`. Better phone and website coverage.
     - `apollo`: named decision makers, needs `APOLLO_API_KEY`. Verify the endpoint on first use.
     - `reddit`: public posts matching buyer phrases. For helpful replies only.
   - `outreach.sender`, `outreach.followup_days` (default `[3, 7]`), optional `templates`.
   - `landing`: headline, bullets and button for the inbound page.
4. **Check.** `python -m leadgen doctor <slug>` validates the config, API keys and network access.
   Fix everything it flags before collecting. API keys are environment variables; never write them to files.

## 2. Build the list

```
python -m leadgen app <slug>                     # interactive: pick area + business types, click Find
python -m leadgen collect <slug>                 # same from the command line: all enabled sources
python -m leadgen import <slug> list.csv --source <label>   # any CSV: LinkedIn export, trade show list
python -m leadgen enrich <slug> --min-score 40   # find emails on the leads' own websites
```
The same business found in two sources is merged (matched on email, website, phone). `enrich` reads only
each lead's own public site, honours robots.txt and pauses between sites; keep `--limit` modest.

**The interactive app** (`python -m leadgen app <slug>`, then open the printed address) does the whole loop
in a browser: choose where and what kind of business, find leads, see today's list, open a lead, edit its
message, open it in the user's email app, and record what happened. Offer it to users who prefer clicking
to commands. It has no password when started this way, so it only listens on the user's own computer.

**Research by hand when a source is missing.** If the buyers are not in a directory (a niche B2B role,
a new trend), use web search to find them - association member lists, "best X in <city>" articles, event
exhibitor lists, people publicly asking for the product - and write what you find to a CSV with the columns
`Name, Company, Title, Email, Phone, Website, Location, Notes`, then `import` it with a `--source` label
naming where it came from. Record only details that are published; never guess an email address.

## 3. Work the leads every day

```
python -m leadgen draft <slug> --min-score 40    # first messages + follow-ups that are due
python -m leadgen next <slug>                    # today's list, in priority order
python -m leadgen show <slug> <id>               # one lead and its message
python -m leadgen mark <slug> contacted <id>...  # after a human sends it
python -m leadgen mark <slug> replied|qualified|won|lost|do_not_contact <id>
```
`next` orders the work: replies first, inbound second, follow-ups due, then the best new leads.
Before a human sends a draft, improve it: use the lead's `signal`, `notes` and website to add one specific,
true detail. A draft is a starting point. Marking `contacted` counts a touch and clears the draft, so the
next `draft` run writes the follow-up. After the last follow-up plus a week, `next` lists the lead under
close-out: mark it `lost`.

## 4. Inbound

```
LEADGEN_ADMIN_TOKEN=<secret> python -m leadgen serve <slug> --port 8000
```
`/` is the landing page and form; `/admin?token=<secret>` is the dashboard. Put the landing URL on every
flyer, profile and ad with its own `?utm_source=`, so the report shows which placement works. To take real
traffic it must run on a public host behind HTTPS; on a laptop it is for testing.

## 5. Review weekly and tune

```
python -m leadgen report <slug> --by source
python -m leadgen report <slug> --by segment
python -m leadgen export <slug> --min-score 40   # CSV for mail merge or a CRM
```
Raise the `weight` of segments that reply and lower those that don't; add the words winning leads have in
common to `keywords_include`; add bad fits to `keywords_exclude`. Then `python -m leadgen score <slug>`.
Update the planning numbers in `MARKET.md` with the real rates.

## Rules

- **Nothing is sent automatically.** The toolkit only drafts. A human reviews and sends every message.
- **Opt-outs are permanent.** Mark `do_not_contact` on any "stop"; the lead is never drafted again and
  re-imports cannot revive it. Keep the opt-out line in cold emails and follow CAN-SPAM, GDPR or CASL for
  the recipient's country.
- **Communities:** reply helpfully and follow each community's self-promotion rules. Never auto-post.
- **Data:** public business contact data only. Overture data is openly licensed; directory emails can be
  out of date, so expect some bounces and remove them. LinkedIn scraping is not supported; use exports or a
  licensed provider.
- **Be honest about verification.** `python -m unittest discover -s tests` covers the pipeline offline.
  A source is only proven once `doctor` reports it reachable and a `collect` returns real leads; say which
  sources you have and have not seen work. Overture has been run live; the others had not been at the
  time of writing.
- **Sample data:** the fruit example's leads are fictional. Remove them with
  `python -m leadgen purge fruits --source sample` before real use.
