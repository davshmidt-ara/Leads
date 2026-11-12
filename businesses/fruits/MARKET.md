# Market overview: fresh fruit supply to businesses (Austin, TX example)

Written November 2026 for the example business "Sunny Orchard Fruit Supply" (fictional).
Figures come from public summaries of paid reports found by web search; the reports themselves
were not read. Treat them as orders of magnitude and check the source before quoting one.

## 1. Size and direction

| What | Figure | Source |
|---|---|---|
| US fruit and vegetable wholesaling revenue | about $110-122bn; two IBISWorld editions disagree ($109.0bn for 2025, $122.2bn for 2026); growth under 1% a year | [IBISWorld](https://www.ibisworld.com/united-states/industry/fruit-vegetable-wholesaling/978/) |
| US fresh fruit market | about $70bn in 2025, forecast to grow about 4% a year to 2030 | [Statista](https://statista.com/outlook/cmo/food/fruits-nuts/fresh-fruits/united-states) |
| Fresh produce retail sales, 2025 | dollars up 2.7%, volume up 2.0% | [IFPA / Circana](https://www.freshproduce.com/siteassets/files/reports/retail/iri/2025-dec-u.s.-produce-retail-point-of-sales.pdf) |
| US juice and smoothie bars | about $4.5bn revenue, 5,500-5,700 businesses, no company above 5% share | [IBISWorld](https://www.ibisworld.com/united-states/market-size/juice-smoothie-bars/4325/) |
| US fresh produce imports, 2024 | over $38bn | IFPA commentary in [Produce Business](https://producebusiness.com/) |

What this means for a local supplier: the market is huge, mature and slow-growing. Nobody wins by
"growing with the market"; you win by taking accounts from broadline distributors and cash-and-carry
runs, one neighbourhood at a time.

## 2. What buyers care about

- **Fresh produce matters to menus, and its cost worries operators.** IFPA's foodservice assessment
  (Technomic research, undated) reports 83% of operators call fresh produce important to their menu and
  70% are very concerned about its cost. ([IFPA](https://www.freshproduce.com/resources/foodservice/foodservice-produce-category-assessment/))
- **Health-led eating is the main demand driver** in foodservice produce, per Datassential commentary;
  Acosta reports 55% of GLP-1 users buy more produce. (Trade coverage in [Produce Business](https://producebusiness.com/); exact articles not re-checked.)
- **"Local" sells, but mostly as positioning.** IFPA commentary notes local sourcing is often a
  marketing claim rather than supply-chain reality. A supplier that can actually name its farms has an edge.
- **Risks:** tariffs on Mexican and Canadian produce push wholesale prices up and make seasonal supply
  less predictable (IBISWorld); food-safety scares temporarily cut demand (IFPA reported about one in six
  consumers avoiding fresh produce at the peak of a 2025 Cyclospora outbreak).

## 3. Who to sell to, in priority order

These weights are the ones in `business.json` (`icp.segments`). They are judgement calls, not data:
revise them from `python -m leadgen report fruits --by segment` once real replies come in.

| Segment | Why it ranks here | Buyer | Best channel |
|---|---|---|---|
| Juice and smoothie bars (30) | Fruit is most of their cost of goods; fragmented, independent; ripeness problems are daily | Owner | Walk-in with sample box, then email/call |
| Hotels and caterers (25) | Large, regular orders; appearance matters; events create spikes | F&B director, exec chef | Email to named person (Apollo/LinkedIn), call |
| Independent grocers (20) | Volume and repeat orders; want variety in small case sizes | Owner | Call mid-afternoon, visit |
| Cafes and bakeries (20) | Many of them; too small for distributor minimums | Owner/manager | Call 2-4pm, Instagram DM |
| Offices (20) | Weekly subscription = predictable revenue; no existing supplier to displace | Office manager, HR | Email, landing page, LinkedIn |
| Restaurants (15) | Biggest count but usually locked into a broadline distributor; win as backup supplier first | Chef, kitchen manager | Call 2-4pm |
| Schools and childcare (15) | Steady, but slow decisions and tight budgets | Director | Email |
| Gyms (10) | Small orders; nice add-on route density | Manager | Email |

Office fruit delivery has no reliable published market size (vendor claims only; the largest US
provider, The FruitGuys, reports serving about 3,500 offices - a company figure found by web search).
Treat it as a test segment.

## 4. Competition

| Competitor type | Their strength | Where you beat them |
|---|---|---|
| Broadline distributors (Sysco, US Foods and similar) | Price, one invoice for everything | Order minimums, generic quality, slow credits |
| Regional produce houses | Range, established routes | Small accounts are low priority for them |
| Cash-and-carry / warehouse clubs | Cheapest per case | Owner's time; inconsistent ripeness |
| Farmers' markets / direct farms | Story, freshness | Reliability, year-round range |
| Office snack services | Bundled perks | Fresh fruit quality, local delivery |

Positioning that follows: **no minimum, six-morning delivery, credit without argument, free sample box.**
That is what `offer` and the segment pitches in `business.json` say.

## 5. Lead-generation plan

1. **Build the list** with `collect` (OpenStreetMap is free; Google Places adds phone/website coverage),
   then `enrich` to pull emails from the businesses' own websites.
2. **Work the hot and warm tiers first** (`next`). Juice bars and caterers get a sample-box visit;
   everyone else gets the email or call script from `draft`.
3. **Three touches, then stop**: day 0, day 3, day 10 (`followup_days: [3, 7]`). Mark outcomes with `mark`.
4. **Inbound in parallel**: put the landing page URL on flyers, delivery vans and the Instagram bio,
   each with its own `utm_source`, so `report` shows which one produces leads.
5. **Weekly review**: `report --by segment` and `--by source`. Raise the weight of segments that reply,
   lower the ones that don't.

### Planning numbers (assumptions, not benchmarks)

Replace each with your own figure after two weeks of real outreach.

| Step | Assumption | Per 200 leads |
|---|---|---|
| Reachable (has email or phone) | 70% | 140 |
| Reply or pick up, any answer | 15% of reachable | 21 |
| Accept a sample box | 40% of replies | 8 |
| Become a weekly account | 40% of samples | 3 |

If a weekly account is worth $150-400 a week, three accounts from 200 leads is roughly $450-1,200 a
week of recurring revenue. The lever that matters most is the sample-to-account rate, which is about
fruit quality and follow-up, not about the software.
