One folder per skill with a SKILL.md (AGENTS.md section 10). Lane B owns this directory. A new folder is
found by itself (graph.py routes /skills/ here), but the concierge and the room scout name skills and rooms
by hand in `agent/prompts.py`: add a new furnishing skill to the concierge's list and its room rule to
`ROOM_SCOUT`, and give it a case in `evals/cases/`.

| Skill | Use |
| --- | --- |
| `furnish-living-room` | Living room for everyday life (first: it is in the demo) |
| `furnish-for-play` | Nerf-war arena or playroom (in the demo) |
| `furnish-nursery` | A baby's room |
| `furnish-bedroom` | Adults' bedroom: primary or guest |
| `furnish-home-office` | Home office in a spare bedroom |
| `furnish-dining-room` | Dining room for family dinners |
| `furnish-kids-room` | Children's bedroom, one child or two |
| `home-search` | Home search criteria and the infant profile |
| `listing-audit` | The auditors' checks |
