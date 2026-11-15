# Agent service events (contracts v2)

Per-player agent service (AGENTS.md section 9):

| Method | Path                      | Body / result                             |
| ------ | ------------------------- | ----------------------------------------- |
| `POST` | `/sessions`               | `{player_id, block_id}` -> `{session_id}` |
| `POST` | `/sessions/{id}/messages` | `{text}` -> `202`                         |
| `GET`  | `/sessions/{id}/stream`   | Server-sent events, one per agent event   |
| `GET`  | `/sessions/{id}/trace`    | `{url}` (or `{url: null}`): the latest turn's LangSmith trace link |

Each SSE message uses the event type as the SSE `event:` name and the payload as JSON in `data:`:

```
event: agent.todos
data: {"todos": [{"content": "...", "status": "in_progress"}]}
```

The transcript fixture `fixtures/agent.session.jsonl` stores the same events one per line as
`{"type": "<event>", "data": {...}}`.

| Type               | Data                                                                                       |
| ------------------ | ------------------------------------------------------------------------------------------ |
| `agent.todos`      | `{todos: [{content, status: "pending" or "in_progress" or "completed"}]}` the current plan |
| `agent.subagent`   | `{name, status: "started" or "finished", summary}`                                         |
| `agent.tool`       | `{agent, tool, status: "started" or "finished" or "error", summary}` one line per call     |
| `agent.message`    | `{text, final}` streamed assistant text                                                    |
| `listings.results` | `{matches: [{listing_id, address, price_usd, score, evidence: [{criterion, passed, value, source}]}]}` |
| `audit.findings`   | an audit (`audit.schema.json`: `{listing_id, findings[]}`)                                 |
| `error`            | `{message}`                                                                                |

`listings.results` (v2.4.4): each match also carries `address` and `price_usd`, copied from the
listing (`null` when the listing has none), so the chat card can name the home. Clients must treat
both as optional: older payloads do not have them.

Furniture and beacons are **not** agent events. The agent sends them to the game service
(internal API in `messages.md`) and every client receives them through the Colyseus state.

`MOCK_AGENT=1`: the agent replays this transcript. When it replays the `commit_placement`
tool line it POSTs `fixtures/placement_plan.json` to the game's plan endpoint; on the
`focus_listing` line it POSTs the focus beacon.

Changing anything here needs all four humans, a bump of `VERSION`, and updated fixtures in the same commit.
