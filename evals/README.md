# Agent evals

Deterministic checks of the concierge agent on the real packs. Lane B owns the runner (`run.py`), Lane A
the cases (`cases/*.json`). The runner drives the same `runner.run_turn` the API uses, fakes the game
service (commits and beacons are captured, not sent) and scores each turn with code, never with the model:
the committed plan is re-validated with `layout.validate_layout`, categories and cover come from the
catalog, room labels from `rooms.json`, beacons from the captured `focus_listing` calls.

```
cd apps/agent
uv run python ../../evals/run.py --list
uv run python ../../evals/run.py                         # all cases (needs MODEL + provider key in .env)
uv run python ../../evals/run.py play_refurnish -j 2     # some cases, two at a time
uv run python ../../evals/run.py --feedback              # also write each check as LangSmith feedback
```

Each turn prints its LangSmith trace link. Results go to `evals/out/<timestamp>.json` (gitignored); the
exit code is 1 if any case failed. The check list and the case format are in the `run.py` docstring.

| Case | Demo step |
| --- | --- |
| `furnish_movie_night` | 0:45 "Furnish the living room for a family that watches movies together" |
| `play_refurnish` | 1:30 nerf-war layout with cover, then 2:15 "Make it a playroom instead" replaces it |
| `edit_move_couch` | an edit changes the existing plan |
| `furnish_primary_bedroom` | `furnish-bedroom`: primary bedroom for a couple (7014) |
| `furnish_home_office` | `furnish-home-office`: desk and chair in the spare bedroom (7014) |
| `furnish_dining_room` | `furnish-dining-room`: table and chairs in the dining room (7014) |
| `furnish_kids_room` | `furnish-kids-room`: two single beds in the spare bedroom (6915) |
| `furnish_open_plan` | `furnish-open-plan`: lounge and dining groups in one living room (6915) |
| `furnish_game_room` | `furnish-game-room`: screen, sofa and beanbags in the living room (6915) |
| `home_search_infant` | 0:25 home search: parallel auditors, beacon on 7014 Navidad |
