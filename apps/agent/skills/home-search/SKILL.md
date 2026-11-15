---
name: home-search
description: "Turn a buyer's needs into hard and soft criteria for search_listings; includes the new-parents (infant) profile"
---

# home-search

Only homes with a scanned interior on the player's block are searched: today that is two houses on Navidad
Road. The job is to pick the better one for these buyers and show why, with the data.

## 1. Hard criteria (search_listings arguments)

| The buyer says | Argument |
|---|---|
| "buy", "for sale", a purchase budget | `status="for_sale"`, `max_price_usd` |
| "rent", a monthly budget | `status="for_rent"`, `max_rent_usd_month` |
| "3 bedrooms" | `min_beds=3` |
| "2 bathrooms" | `min_baths=2` |
| "a house" | `property_type="single_family"` |

Unknown is not a failure: a listing with `price_usd = null` is kept and its evidence says `passed: null`.
Sold homes are still `for_sale` in this data, so do not filter on that.

## 2. Soft needs (search_listings `needs`, with weights)

New parents with an infant (the infant profile):

| need | weight | What it checks |
|---|---|---|
| `single_story` | high | `listing.stories == 1` and the scan has one floor: no stairs with a baby |
| `small_bedroom_near_primary` | high | A bedroom under 12 m2 within 2 rooms of the primary bedroom in `graph.json` (a nursery). Directly off the primary scores higher than across a hallway. |
| `no_pool` | high | `features.pool == false` |
| `fenced_yard` | medium | `features.fenced_yard`; often not listed (null) |
| `elementary_school` | low | An elementary school in `listing.schools` (names only: no distance or rating in this data) |

Other households: a family with kids (single_story medium, fenced_yard high, elementary_school medium,
garage low); a remote worker (small_bedroom_near_primary is not the need: ask what they want).

## 3. Delegate, rank, act

1. Write `/briefs/search.md` with the buyers, the hard criteria and the needs with weights.
2. `search_listings`; it saves `/search/results.json`. Its `market` block counts the whole CoStar index
   under the same hard criteria (`passed_hard` of `listings`, median price and price per sq ft); each match
   has `price_per_sqft_usd` and `vs_market_pct`. Quote these numbers; never compute your own.
3. One `listing-auditor` task per match, **all in the same message** so they run in parallel.
4. Rank: search score first; then contradictions the auditors found count against a home; then price.
5. `focus_listing` on the winner (the player is taken to its front door).
6. Answer with the pick and the facts that decided it, with values and their fields: e.g. "7014 Navidad:
   the small bedroom (105 sq ft) opens straight off the primary bedroom; $220,000; one story." Then name any
   contradiction or audit warning for either home as its `say` sentence from get_audit (in sq ft).
