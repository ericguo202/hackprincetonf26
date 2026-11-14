# Demo candidates: which real homes to build the map from

Scout report, generated 2026-11-14T07:41:50Z by `worldgen/scout/report.py` (Lane A). Brief: `docs/prompts/demo-property-scout.md`. Full per-home data (every field and sub-score, with sources): `worldgen/out/scout/shortlist.json` (not committed; regenerate with the steps in `worldgen/out/scout/PROGRESS.md`). Field citations read `file:field`.

## 1. Recommendation

**Strategy A (one shared block) on Navidad Rd, Houston (Mission Bend, 77083).** Suggested `block_id`: `hou_navidad_01`, centre **29.70113, -95.670841**, radius **200 m**.

- **Houses:** 6915 Navidad Road (`xpspAtb5H59`, hero) and 7014 Navidad Road (`y1cVPR1HsnL`), 194 m apart on the same street (`clusters.json:demo_areas.pairs[0]`). Both have OUTSIDE drone splats, so nobody has to invent walls.
- **They answer all three scripted prompts** (`scores.jsonl:strong_personas`): 6915 Navidad Road: infant, movie; 7014 Navidad Road: infant, movie, nerf. Both are Active, one story, under $450k ($230,000 and $220,000, `listings.jsonl:price_usd`).
- **Neighbourhood mesh size:** 140 parcels within 197 m, 124 with a primary structure (`vexcel_clusters.json:demo_pair_1`, MCP `property_metadata_count`). Vexcel urban ortho + DSM `us-tx-houston-2026`, captured 2025-12-19..2026-01-02.
- **Why not the densest splat group?** By the brief's rank (splat homes first) the #1 area is **Nantucket Dr** (4 splat homes within 300 m, span 236 m, 162 parcels). But those are 3-4-storey townhome units (two share one building), $699,000-$1,490,000, with no private yards; their heroes are strong only for big_family, movie. No house there fits the scripted 'new parents under $450k' search. Houston never has more than 4 splat homes within 300 m (`clusters.json:markets.houston.density`).

**Strategy B (one small lot map per house)** gives many more heroes and every persona, but changes the one-block design in `AGENTS.md` and needs a human decision.

| Strategy | Hero houses | Personas with a strong hero | Notes |
|---|---|---|---|
| **A: Navidad Rd (recommended)** | 2 (194 m apart) | infant, movie, nerf | Covers the stage script; small one-storey houses are the easiest packs to build |
| A: Nantucket Dr (brief rank #1) | 4 (3 buildings, 236 m) | big_family, movie | Townhomes, mostly furnished scans, no house under $450k |
| B: lot map per splat home | 168 (158 Houston, 10 LA) | big_family, contradiction, infant, movie, nerf, pool, renter | Each lot map is the splat near field (about 100-200 m across); needs an `AGENTS.md` change |

If the team adopts B later, the lot maps to build first are the B rows in the persona table (section 3).

## 2. Hero houses

### 6915 Navidad Road, Houston 77083

*Block hero: the house the new-parents search lands on, furnished on stage.*

- **Links:** Matterport [xpspAtb5H59](https://my.matterport.com/show/?m=xpspAtb5H59), [portal](https://beta-app.matterport.com/2026-princeton-hackathon/models/xpspAtb5H59). CoStar listing `237901269` (property `500019960065`).
- **Listing:** Active sale, $230,000; 3 bd / 2 ba, 122.4 m2 (1317 sqft), 1 storey(s), built 1979; tax $4,123/yr; schools: Petrosky Elementary School.
- **Exterior:** splat tour **OUTSIDE**, splat 420 MB, 629 drone photos (`splat-models.tsv`). Vexcel: parcel 526.6 m2, yard 336.6 m2, pool False, roof 4.62 m (`vexcel_verify.jsonl`).
- **Interior:** 1 floor(s), 7 labelled rooms, scanned living 94.5 m2; defurnish: bundle `unlocked`, defurnished mesh **yes**; wall/door vectors yes; DXF yes (`mp_derived.jsonl`). Interior score 27.5/30.
- **Best persona:** New parents with an infant, buying under $450k (17.0/20): $230,000 <= $450k; one floor; 10.5 m2 bedroom 2 step(s) from primary; no pool listed; elementary school named. Strong: infant, movie.
- **Visual check:** layout yes (from GraphQL: 7 labelled rooms, 86% closed polygons); the plan render is almost fully covered by roof planes; drone: low front view: whole front facade, 2-car garage; furnished: unclear from images; defurnished mesh available (`visual_check.jsonl`).
- **Contradictions:** area_vs_scan (warn): listed 1317 sqft = 122.4 m2 (property.sqftMin), observed 94.5 m2 scanned living area (ratio 0.77)

### 7014 Navidad Road, Houston 77083

*Block second house: walkable loops for the nerf war; same street, 194 m away.*

- **Links:** Matterport [y1cVPR1HsnL](https://my.matterport.com/show/?m=y1cVPR1HsnL), [portal](https://beta-app.matterport.com/2026-princeton-hackathon/models/y1cVPR1HsnL). CoStar listing `236595179` (property `500019227383`).
- **Listing:** Active sale, $220,000; 3 bd / 2 ba, 103.2 m2 (1111 sqft), 1 storey(s), built 1981; tax $3,220/yr; schools: Mission Bend Elementary School.
- **Exterior:** splat tour **OUTSIDE**, splat 419 MB, 630 drone photos (`splat-models.tsv`). Vexcel: parcel 664.6 m2, yard 471.3 m2, pool False, roof 4.17 m (`vexcel_verify.jsonl`).
- **Interior:** 1 floor(s), 8 labelled rooms, scanned living 84.9 m2; defurnish: bundle `unlocked`, defurnished mesh **no**; wall/door vectors yes; DXF yes (`mp_derived.jsonl`). Interior score 22.5/30.
- **Best persona:** New parents with an infant, buying under $450k (17.0/20): $220,000 <= $450k; one floor; 9.0 m2 bedroom 1 step(s) from primary; no pool listed; elementary school named. Strong: infant, movie, nerf.
- **Visual check:** layout yes (from GraphQL: 8 labelled rooms, 100% closed polygons); roof planes cover part of the plan render; drone: oblique: whole house from the front corner; furnished: unclear from images; no defurnished mesh (`visual_check.jsonl`).
- **Contradictions:** area_vs_scan (warn): listed 1111 sqft = 103.2 m2 (property.sqftMin), observed 84.9 m2 scanned living area (ratio 0.82)

### 5507 Bertellis Lane, Houston 77091

*Best single-house backup: infant + movie, defurnished, splat checked.*

- **Links:** Matterport [K573sVtzbkN](https://my.matterport.com/show/?m=K573sVtzbkN), [portal](https://beta-app.matterport.com/2026-princeton-hackathon/models/K573sVtzbkN). CoStar listing `236580557` (property `500019915431`).
- **Listing:** Active sale, $249,000; 4 bd / 2 ba, 171.5 m2 (1846 sqft), 1 storey(s), built 1972; tax $5,015/yr; schools: Smith Elementary School (Houston).
- **Exterior:** splat tour **OUTSIDE**, splat 315 MB, 476 drone photos (`splat-models.tsv`). Vexcel: parcel 614.1 m2, yard 337.0 m2, pool False, roof 2.98 m (`vexcel_verify.jsonl`).
- **Interior:** 1 floor(s), 17 labelled rooms, scanned living 139.0 m2; defurnish: bundle `unlocked`, defurnished mesh **yes**; wall/door vectors yes; DXF yes (`mp_derived.jsonl`). Interior score 30.0/30.
- **Best persona:** New parents with an infant, buying under $450k (20.0/20): $249,000 <= $450k; one floor; 10.8 m2 bedroom 2 step(s) from primary; no pool listed; fenced yard; elementary school named. Strong: infant, movie.
- **Visual check:** layout yes; drone: whole house from the front, above; furnished: unclear (plan looks sparse; 5 alt texts mention furniture); defurnished mesh available (`visual_check.jsonl`).
- **Splat file checked:** 4,638,609 Gaussians, 315 MB, Z up; near field (p5-p95 within 150 m) [-106.57, -99.55, -0.52] .. [91.31, 91.75, 12.86] m; density over the house 13.3x the surroundings -> footprint inside: True. 50k OBJ: 62248 triangles, Z up.
- **Contradictions:** area_vs_scan (warn): listed 1846 sqft = 171.5 m2 (property.sqftMin), observed 139.0 m2 scanned living area (ratio 0.81)

### 3010 Bonnebridge Way Boulevard, Houston 77082

*Nerf / pool / big family showcase (strategy B lot map).*

- **Links:** Matterport [CFwU7UiF7XN](https://my.matterport.com/show/?m=CFwU7UiF7XN), [portal](https://beta-app.matterport.com/2026-princeton-hackathon/models/CFwU7UiF7XN). CoStar listing `237433210` (property `500020511321`).
- **Listing:** Active sale, $1,469,000; 4 bd / 4.5 ba, 418.3 m2 (4503 sqft), 2 storey(s), built 2000; tax $29,217/yr; schools: Outley Elementary School.
- **Exterior:** splat tour **OUTSIDE**, splat 247 MB, 373 drone photos (`splat-models.tsv`). Vexcel: parcel 936.1 m2, yard 501.5 m2, pool True, roof 8.32 m (`vexcel_verify.jsonl`).
- **Interior:** 2 floor(s), 34 labelled rooms, scanned living 334.3 m2; defurnish: bundle `unlocked`, defurnished mesh **yes**; wall/door vectors yes; DXF yes (`mp_derived.jsonl`). Interior score 28.5/30.
- **Best persona:** Big or multi-generational family (20.0/20): 5 bedrooms (scan 5, listed 4); 2 floors; Staircase room; 4.5 baths. Strong: big_family, movie, nerf, pool.
- **Visual check:** layout yes; drone: wide oblique of two large houses; target house front visible; furnished: furnished (furniture in main living area); defurnished mesh available (`visual_check.jsonl`).
- **Splat file checked:** 3,634,306 Gaussians, 247 MB, Z up; near field (p5-p95 within 150 m) [-51.12, -62.75, -6.65] .. [71.13, 63.84, 6.61] m; density over the house 30.3x the surroundings -> footprint inside: True. 50k OBJ: 126710 triangles, Z up.
- **Contradictions:** beds_vs_scan (warn): listed 4 beds (property.bedsMin), observed 5 Bedroom rooms [24.5, 17.1, 15.1, 14.5, 12.9]; area_vs_scan (warn): listed 4503 sqft = 418.3 m2 (property.sqftMin), observed 334.3 m2 scanned living area (ratio 0.80)

### 11709 Lakeside Place Drive Unit 232, Houston 77077

*Renter: active $2,500/mo lease, vacant scan.*

- **Links:** Matterport [8xcWv3r7MUN](https://my.matterport.com/show/?m=8xcWv3r7MUN), [portal](https://beta-app.matterport.com/2026-princeton-hackathon/models/8xcWv3r7MUN). CoStar listing `233072090` (property `18352743`).
- **Listing:** Active rent, $2,500/mo; 3 bd / 2.5 ba, 175.8 m2 (1892 sqft), 2 storey(s), built 1998; tax n/a; schools: Askew Elementary School.
- **Exterior:** splat tour **OUTSIDE**, splat 230 MB, 352 drone photos (`splat-models.tsv`). Vexcel: parcel 152.7 m2, yard 59.3 m2, pool False, roof 6.16 m (`vexcel_verify.jsonl`).
- **Interior:** 2 floor(s), 15 labelled rooms, scanned living 156.2 m2; defurnish: bundle `unlocked`, defurnished mesh **yes**; wall/door vectors yes; DXF yes (`mp_derived.jsonl`). Interior score 28.5/30.
- **Best persona:** Renter (20.0/20): rent listing; Active rent; $2,500/mo. Strong: renter.
- **Visual check:** layout yes; drone: attached townhome building, several units; furnished: vacant (rooms empty in plan textures) (`visual_check.jsonl`).
- **Splat file checked:** 3,382,941 Gaussians, 230 MB, Z up; near field (p5-p95 within 150 m) [-40.54, -60.48, -0.54] .. [58.63, 36.74, 13.83] m; density over the house 50.3x the surroundings -> footprint inside: True. 50k OBJ: 26450 triangles, Z up.

### 1429 Godwin Street, Houston 77023

*Contradiction gem with a splat; 1411 Lawson St (stairs gem) is 95 m away.*

- **Links:** Matterport [w8C7hLWnZ8k](https://my.matterport.com/show/?m=w8C7hLWnZ8k), [portal](https://beta-app.matterport.com/2026-princeton-hackathon/models/w8C7hLWnZ8k). CoStar listing `238932781` (property `500019798760`).
- **Listing:** Active sale, $469,888; 3 bd / 2 ba, 171.1 m2 (1842 sqft), 2 storey(s), built 1928; tax $10,138/yr; schools: Lantrip Elementary School.
- **Exterior:** splat tour **OUTSIDE**, splat 508 MB, 779 drone photos (`splat-models.tsv`). Vexcel: parcel 590.6 m2, yard 344.6 m2, pool False, roof 3.99 m (`vexcel_verify.jsonl`).
- **Interior:** 2 floor(s), 22 labelled rooms, scanned living 240.5 m2; defurnish: bundle `unlocked`, defurnished mesh **no**; wall/door vectors yes; DXF yes (`mp_derived.jsonl`). Interior score 22.0/30.
- **Best persona:** Contradiction (20/20): beds_vs_scan; area_vs_scan; stories_vs_vexcel_height. Strong: big_family, contradiction, movie.
- **Visual check:** layout yes; drone: front of a 1.5-storey craftsman, detached garage behind; furnished: unclear (3 alt texts say empty) (`visual_check.jsonl`).
- **Contradictions:** beds_vs_scan (conflict): listed 3 beds (property.bedsMin), observed 5 Bedroom rooms [16.1, 14.4, 14.3, 9.6, 9.5]; area_vs_scan (conflict): listed 1842 sqft = 171.1 m2 (property.sqftMin), observed 240.5 m2 scanned living area (ratio 1.41); stories_vs_vexcel_height (warn): listed 2 stories (resoValues.structure.stories), observed roof 3.99 m (DSM - DTM), ~1 stories

## 3. Persona table

Strong = persona score >= 14/20 (`scores.jsonl:personas.<persona>`). A = in the recommended block; B = needs its own lot map.

| Persona | House | Strategy | Score | Fields that justify it |
|---|---|---|---|---|
| New parents with an infant, buying under $450k | 6915 Navidad Road | A | 17 | $230,000 <= $450k; one floor; 10.5 m2 bedroom 2 step(s) from primary; no pool listed; elementary school named |
| New parents with an infant, buying under $450k | 5507 Bertellis Lane | B | 20 | $249,000 <= $450k; one floor; 10.8 m2 bedroom 2 step(s) from primary; no pool listed; fenced yard; elementary school named |
| Family that watches movies together | 6915 Navidad Road | A | 15 | Kitchen 46.8 m2 (open_plan_kitchen); clear wall 3.38 m |
| Family that watches movies together | 8819 Kolbe Bend Lane | B | 20 | Living Room 45.4 m2 (label); clear wall 5.64 m; min dimension 5.54 m |
| Nerf war / playroom | 7014 Navidad Road | A | 16 | open living 44.3 m2; 2 loop(s) on entry floor; splat with yard (vexcel yard 471.3 m2) |
| Nerf war / playroom | 3010 Bonnebridge Way Boulevard | B | 18 | open living 72.2 m2; 3 loop(s) on entry floor; splat with yard (vexcel yard 501.5 m2) |
| Big or multi-generational family | 3010 Bonnebridge Way Boulevard | B | 20 | 5 bedrooms (scan 5, listed 4); 2 floors; Staircase room; 4.5 baths |
| Big or multi-generational family | 404 Avondale Street | B | 20 | 6 bedrooms (scan 6, listed 5); 3 floors; Staircase room; 5 baths |
| Pool / outdoor | 16346 Havenpark Court | B | 16 | CoStar private pool; Vexcel pool; 1 pool photos (scene 19) |
| Pool / outdoor | 3010 Bonnebridge Way Boulevard | B | 16 | CoStar private pool; Vexcel pool; 2 pool photos (scene 19) |
| Renter | 11709 Lakeside Place Drive | B | 20 | rent listing; Active rent; $2,500/mo |
| Contradiction | 1429 Godwin Street | B | 20 | beds_vs_scan; area_vs_scan; stories_vs_vexcel_height |
| Contradiction | 1411 Lawson Street | interior only | 20 | stories_vs_scan; beds_vs_scan; area_vs_scan |

## 4. Contradiction gems

Context: scanned living area is a median 0.874 of the listed area (scans measure inside the walls), so small area gaps are normal; the gems below are large or explained.

| House | Check | Listed | Observed | Evidence / explanation |
|---|---|---|---|---|
| 1411 Lawson Street (`r5UE5zy6pU3`) | stories_vs_scan (conflict) | 1 story (resoValues.structure.stories) | 2 floors, 2 Staircase room(s) | Listing remarks: 'efficiency garage apt … located upstairs in back'; features list 'Garage Apartment'. The scanned second floor is that apartment (floor plan, visual_check.jsonl). Active, $349,900, defurnished mesh. |
| 1429 Godwin Street (`w8C7hLWnZ8k`) | beds_vs_scan (conflict) | 3 beds (property.bedsMin) | 5 Bedroom rooms [16.1, 14.4, 14.3, 9.6, 9.5] | Remarks: 'garage apartment offering a 3/2 layout'. The scan counts the apartment's rooms and area; the listing does not. Splat home (OUTSIDE), Active $469,888. |
|  (`w8C7hLWnZ8k`) | area_vs_scan (conflict) | 1842 sqft = 171.1 m2 (property.sqftMin) | 240.5 m2 scanned living area (ratio 1.41) | model w8C7hLWnZ8k sum of area excl. Void/Garage/outdoor |
| 10418 Brinwood Drive (`8VF48HG3wMH`) | beds_vs_scan (conflict) | 5 beds (property.bedsMin) | 7 Bedroom rooms [26.8, 16.0, 14.4, 12.9, 12.9, 12.7, 12.2] | 2026 new build (yearBuilt 2026). The drone thumbnail shows 2 storeys, so the low Vexcel roof height comes from imagery captured before construction (Dec 2025-Jan 2026). Listed 1,954 sqft vs 348 m2 scanned. Sold. |
|  (`8VF48HG3wMH`) | area_vs_scan (conflict) | 1954 sqft = 181.5 m2 (property.sqftMin) | 348.4 m2 scanned living area (ratio 1.92) | model 8VF48HG3wMH sum of area excl. Void/Garage/outdoor |
|  (`8VF48HG3wMH`) | stories_vs_vexcel_height (warn) | 2 stories (resoValues.structure.stories) | roof 3.36 m (DSM - DTM), ~1 stories | vexcel dsm_elevation / dtm_elevation inside the primary footprint |
| 8435 Erasmus Landing Court (`NcVxKRkmsNn`) | pool_vs_vexcel (conflict) | private pool True (resoValues.poolType / features Pool) | Vexcel pool False | CoStar lists a private pool; Vexcel property/extract detects none. Not checked visually (drone thumbnail not downloaded). Sold. |
| 204 Dipping Lane (`WLPynEfvv2e`) | stories_vs_vexcel_height (warn) | 1 stories (resoValues.structure.stories) | roof 8.64 m (DSM - DTM), ~2 stories | Listed 1 story; Vexcel roof height 8.6 m. Matterport geocoordinates are 211 m from CoStar, so the parcel may be the wrong one: treat as unverified. |

All homes: 31 'listed one storey but the scan has 2+ floors or a Staircase' (none on a splat home), 90 bed-count conflicts (>= 2), 103 area gaps > 30%, 5 pool conflicts with Vexcel (`phase3_score_summary.json:contradiction_checks`).

## 5. Runner-up areas

Brief's rank: splat homes, then homes with interior >= 20, then persona coverage, then contradictions (`clusters.json`). Disjoint clusters, greedy.

| Market | Radius | # | Splat homes (heroes) | Interior >= 20 | Personas | Parcels | Centre | Splat homes |
|---|---|---|---|---|---|---|---|---|
| houston | 300 m | 1 | 4 (4) | 7 | big_family, contradiction, movie | 162 | 29.7448, -95.487255 | 2111 Nantucket Drive; 2215 Nantucket Drive; 2222 Nantucket Drive |
| houston | 300 m | 2 | 3 (3) | 8 | infant, movie | 218 | 29.850212, -95.374656 | 204 Dipping Lane; 210 Dipping Lane; 9904 Dipping Brook Street |
| houston | 300 m | 3 | 2 (2) | 7 | big_family, movie, pool, renter | 426 | 29.765525, -95.414663 | 5217 Blossom Street; 5342 Chandler Street |
| houston | 300 m | 4 | 2 (2) | 4 | big_family, movie | 80 | 29.984181, -95.435362 | 14954 Eagle Feather Drive; 14963 Eagle Feather Drive |
| houston | 300 m | 5 | 2 (1) | 4 | movie | 472 | 29.800466, -95.405068 | 1519 Waverly Street; 455 W 18th Street |
| houston | 600 m | 1 | 4 (4) | 10 | big_family, contradiction, movie | 162 (same area) | 29.7448, -95.487255 | 2111 Nantucket Drive; 2215 Nantucket Drive; 2222 Nantucket Drive |
| houston | 600 m | 2 | 3 (3) | 11 | big_family, movie, nerf, renter | 1287 | 29.783575, -95.427752 | 2520 Sherwin St; 6027 Matthew Oaks Place; 6223 Linton Road |
| houston | 600 m | 3 | 3 (1) | 8 | big_family, contradiction, infant, movie, pool | 1520 | 29.852809, -95.600731 | 12730 Melvern Court; 12731 Melvern Court; 6006 Ballina Canyon Lane |
| los_angeles | 1000 m | 1 | 2 (2) | 5 | big_family, contradiction, movie | 2191 | 34.07713, -118.183692 | 4510 Tourmaline Street; 5065 La Calandria Drive |
| los_angeles | 1000 m | 2 | 2 (1) | 2 |  | 2460 | 34.024587, -118.155342 | 5512 Eagle Street; 709 Fraser Avenue |

- **Eagle Feather Dr** (Houston 300 m #4): 14954 and 14963 Eagle Feather Dr face each other, 32 m apart; new subdivision, Vexcel has no parcel at 14954 (imagery predates it).
- **Dipping Ln** (Houston 300 m #2): cheap new builds (210/204 Dipping Ln $193k, infant), but Matterport geocoordinates are 112-211 m from CoStar for both: check before building.
- **Best in LA:** at most 2 splat homes within 1 km (5065 La Calandria Dr + 4510 Tourmaline St, 1.5 km apart). LA works only as strategy B; best LA lot map: 5065 La Calandria Dr (3 floors, Active $895k, big family + movie).
- **Single houses that answer all three scripted prompts** (strategy B fallback): 5611 Pebble Springs Drive (big_family, infant, movie, nerf); 19302 Dawn Canyon Road (big_family, infant, movie, nerf); 2035 Briarlee Drive (big_family, infant, movie, nerf); 8302 Country Wind Lane (infant, movie, nerf); 13319 Hartland Lake Lane (infant, movie, nerf). Defurnished mesh among them: 8302 Country Wind Lane (`scores.jsonl:defurnished_mesh`).

## 6. Coverage and Vexcel spend

| What | Count | Source |
|---|---|---|
| Homes (CoStar propertyId) | 1867 (1588 Houston, 279 LA) | `phase1_summary.json` |
| Homes with a drone splat | 211 (202 OUTSIDE, 9 Inside only) | `listings.jsonl:splat` |
| Splat homes that are MLS 800000 placeholders (price $1) | 37 | `listings.jsonl:placeholder_listing` |
| Hero-eligible (splat, interior >= 20, data >= 10, real listing) | 168 | `scores.jsonl:hero_eligible` |
| Matterport models fetched | 1959 / 1959 (0 failed) | `phase2_summary.json` |
| Wall + door vectors (`boundary.edges`) | 1952/1959 (splat 211/211) | `mp_derived:vectors` |
| `mp:defurnish` unlocked | 1841/1959 (splat 210/211) | `bundles` |
| Defurnished mesh asset (`defurnished_50k.obj`) | 1405/1959 (splat 96/211) | `assets.meshes` |
| DXF floor plan | 1959/1959 | `assets.floorplans` |
| Geocoordinates (source `processing`) | 1942; median 5.0 m from CoStar, 38 over 100 m | `mp_derived:geo_vs_costar_m` |
| `mp:exteriorsplat` | unavailable on all | `bundles` |
| Homes checked with Vexcel `property/extract` | 119 | `vexcel_verify.jsonl` |

**Vexcel calls (our ledger; the account's unit counter is not visible to us):** 213 total = 147 REST (123 `property/extract`, 20 `oriented/extract` crops, 4 usage probes) + 66 MCP tool calls. Budget was about 300 calls and 40 crops. Ledger: `worldgen/out/scout/ledger/vexcel_calls.jsonl`.

## 7. Questions for sponsor engineers

1. **Vexcel units:** REST `/v2/usage/query` returns 403 and MCP `usage_query` says 'Forbidden by role permissions'. How do we see our unit balance? (We logged every call ourselves: see section 6.)
2. **Vexcel 3D Cities:** the profile lists `3d-cities` and `/3d/tile`, `/3d/tileset`, `/3d/collections`. What is the request for a glTF/3D Tiles download of a 200 m block? (Not tried, to save units.)
3. **Vexcel stories and roof height:** `property/extract` returns null for `number-of-stories-below-eave` and `maximum-roof-height` on all 116 homes with a structure (119 queried). Is that a product we lack? We used DSM minus DTM instead.
4. **Vexcel imagery age:** Houston urban imagery is Dec 2025-Jan 2026; the drone splats are Oct-Nov 2026. New builds (10418 Brinwood Dr) and some new subdivisions (14954 Eagle Feather Dr: no parcel at the point) are missing. Is newer coverage coming?
5. **Matterport door positions:** `ModelRoom.boundary.edges[].openings[]` gives `type`, `label`, `width`, `height` but not where the opening sits along its edge. Is there a field for that, or should we read the DXF?
6. **Splat frame:** for OUTSIDE tours the splat and the 50k mesh both centre near the origin, Z up, and the splat is densest over the house. Is the splat guaranteed to be in the model's local frame (same origin, scale, heading)? How does that frame map to `geocoordinates` (always `source: processing`)?
7. **Smaller splat for the web:** splats are 230-420 MB of DC-colour Gaussians with background out to +-9 km. Is there a cropped or compressed (SPZ / quantized) version, or a recommended crop radius?
8. **`mp:exteriorsplat` bundle:** `unavailable` on all 1,959 models, including the 211 splat homes. Is the portal download the only route, and is the portal splat licensed for a public demo?
9. **`mp:defurnish`:** unlocked on 1841/1959 models but a `defurnished_50k.obj` exists on only 1405/1959 (96/211 splat homes). Will the rest be processed during the event?
10. **Placeholder listings:** 37 of the 211 splat homes are MLS 800000 photo-shoot placeholders (price $1, no features record). Can CoStar share the real listing (if any) for those homes?

## Summary

Build the demo as strategy A on **Navidad Rd, Houston** (`hou_navidad_01`, centre 29.70113, -95.670841, radius 200 m): two Active, one-storey splat homes 194 m apart that together answer the three scripted prompts. 6915 Navidad Rd (`xpspAtb5H59`, defurnished mesh) is the hero for the new-parents search and the movie room; 7014 Navidad Rd (`y1cVPR1HsnL`) has the loops for the nerf war. Keep 5507 Bertellis Ln, 3010 Bonnebridge Way Blvd, 11709 Lakeside Place Dr #232 and 1429 Godwin St as strategy-B lot maps if the team approves per-house maps; together they cover all seven personas. The three biggest risks: (1) **splat size** - the two Navidad splats are 420 + 419 MB of DC-colour Gaussians with background out to +-9 km, so they must be cropped to the ~100 m near field and compressed before they can load in a browser; (2) **frame alignment** - the splat, the Matterport mesh and the Vexcel block must line up; the shared splat/mesh frame is inferred from point density, not confirmed, and Vexcel imagery is ~10 months older than the drone capture; (3) **interior fidelity for play** - door positions along walls are not exposed (only widths), 7014 Navidad Rd has no defurnished mesh, and both houses are small (85-95 m2 scanned), so collision walls and the nerf arena need hand checks.
