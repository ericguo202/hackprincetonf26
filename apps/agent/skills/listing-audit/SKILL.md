---
name: listing-audit
description: "Check one listing against its scan, graph and audit; report contradictions with evidence"
---

# listing-audit

The audit (`get_audit`) is computed offline by deterministic checks that compare the CoStar listing with the
Matterport scan and Vexcel data. You report it; you never redo its math.

## The checks (one finding each)

| check | listed | observed | A contradiction looks like |
|---|---|---|---|
| `stories_vs_scan` | `listing.stories` | scanned floors, staircase rooms | "Listed as one story, but the scan shows a staircase to a second floor." |
| `beds_vs_rooms` | `listing.beds` | rooms labelled bedroom or primary_bedroom | "3 beds listed, 2 bedrooms scanned." |
| `baths_vs_rooms` | `listing.baths` | rooms labelled bathroom | "2 baths listed, 1 bathroom scanned." |
| `area_vs_scan` | `listing.area_m2` | summed scanned room area (garage excluded) | "The scan is 15% smaller than the listed area." |
| `pool_vs_vexcel` | `features.pool` | pool in the aerial data | "No pool listed, but the aerial data shows one." |

## Severity

- `info`: checked and consistent, or not checked (the evidence says which). Say "checked" for these; they
  are not contradictions.
- `warn`: worth mentioning (area off by more than 15%). Report it as a contradiction with both values.
- `conflict`: a real contradiction (area off by more than 30%, a staircase in a one-story listing, a bed or
  bath count mismatch). Lead with it.

Always quote `listed` and `observed` and the room ids from `evidence`. An inside-wall scan usually measures
a little less than the listed area, so a `warn` on area is softer than a `conflict`; say so.

## Fit against the buyer's needs

For each need in the brief, `met` is true, false or null (unknown), with the field and value: use
`/search/results.json` evidence for the listing, and `get_house_graph` / `get_rooms` to confirm room facts
(for example the nursery's area and which room it opens onto).
