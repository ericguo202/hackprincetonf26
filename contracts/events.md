# WebSocket events (contracts v1)

One socket per session at `/ws/{session_id}`. Every message is an envelope:

```json
{"type": "...", "data": {...}}
```

Full transcript fixture: `fixtures/events.session.jsonl`.

| Direction     | Type               | Data                                                                                     |
| ------------- | ------------------ | ---------------------------------------------------------------------------------------- |
| web -> server | `user.message`     | `{text, context: {listing_id, room_id, player_pos}}` (context fields null when outdoors) |
| web -> server | `user.decision`    | `{decision: "approve" or "reject", note}` answer to `agent.approval`                     |
| server -> web | `agent.todos`      | `{todos: [{content, status}]}` the agent's current plan                                  |
| server -> web | `agent.subagent`   | `{name, status: "started" or "finished", summary}`                                       |
| server -> web | `agent.tool`       | `{agent, tool, status, summary}` one line per tool call                                  |
| server -> web | `agent.message`    | `{text, final}` streamed assistant text                                                  |
| server -> web | `agent.approval`   | `{plan_id, question}` human-in-the-loop pause before placing                             |
| server -> web | `listings.results` | `{matches: [{listing_id, score, evidence: [{criterion, passed, value, source}]}]}`       |
| server -> web | `world.focus`      | `{listing_id}` show a beacon on that house                                               |
| server -> web | `placement.plan`   | a placement plan (`placement_plan.schema.json`) with `status: "placed"`                  |
| server -> web | `error`            | `{message}`                                                                              |

Changing anything here needs agreement from all four lanes, a bump of `VERSION`,
and updated fixtures in the same commit.
