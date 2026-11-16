// Lane D: the plan feed. The concierge's todo list and each subagent handoff for the current turn.
import { useStore } from "../store";
import { subagentRows } from "./handoffs";

const MARK = { pending: "○", in_progress: "◐", completed: "●" } as const;

export function AgentFeed() {
  const todos = useStore((s) => s.todos);
  const subagents = useStore((s) => s.subagents);
  const tools = useStore((s) => s.tools);
  const rows = subagentRows(subagents, tools);
  if (!todos.length && !rows.length) return null;

  return (
    <div className="agent-feed">
      {todos.length ? (
        <ul className="plan">
          {todos.map((t, i) => (
            <li key={i} data-status={t.status}>
              <span className="mark" aria-hidden>
                {MARK[t.status] ?? "○"}
              </span>
              {t.content}
            </li>
          ))}
        </ul>
      ) : null}
      {rows.length ? (
        <ul className="handoffs">
          {rows.map((r, i) => (
            <li key={i} data-status={r.status}>
              <div className="handoff-head">
                {r.status === "running" ? <span className="spin small" aria-hidden /> : null}
                <strong>{r.label}</strong>
                <span className="handoff-state">
                  {r.status === "running" ? "working" : r.status === "error" ? "failed" : "done"}
                </span>
              </div>
              {r.task ? <p className="handoff-task">{r.task}</p> : null}
              {r.status === "running" && r.lastTool ? (
                <p className="handoff-tool">
                  {r.lastTool.tool.replace(/_/g, " ")}: {r.lastTool.summary}
                </p>
              ) : null}
              {r.result ? <p className="handoff-result">{r.result}</p> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
