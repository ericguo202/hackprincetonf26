// Lane D: fold the turn's agent.subagent / agent.tool events into rows for the plan feed.
import type { SubagentActivity, ToolLine } from "../store";

export type SubagentRow = {
  name: string;
  label: string;
  status: "running" | "finished" | "error";
  /** The task the concierge handed over (first line of its description). */
  task: string;
  /** The subagent's report, once it returns. */
  result: string;
  /** Latest tool line this subagent ran, for the running row. */
  lastTool: ToolLine | null;
};

/** "listing-auditor" -> "Listing auditor". */
export function agentLabel(name: string): string {
  const s = name.replace(/[-_]+/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : "Subagent";
}

/**
 * One row per `task` call. A finished event closes the oldest still-running row with the same name,
 * so parallel listing auditors pair up in order. Tool lines attach to the running row of their agent.
 */
export function subagentRows(events: SubagentActivity[], tools: ToolLine[]): SubagentRow[] {
  const rows: SubagentRow[] = [];
  for (const e of events) {
    if (e.status === "started") {
      rows.push({ name: e.name, label: agentLabel(e.name), status: "running", task: e.summary, result: "", lastTool: null });
      continue;
    }
    const row = rows.find((r) => r.name === e.name && r.status === "running");
    const status = e.status === "error" ? "error" : "finished";
    if (row) Object.assign(row, { status, result: e.summary });
    else rows.push({ name: e.name, label: agentLabel(e.name), status, task: "", result: e.summary, lastTool: null });
  }
  for (const t of tools) {
    const running = rows.filter((r) => r.name === t.agent && r.status === "running");
    // Parallel subagents with one name share tool lines; show it on all of them rather than guess.
    for (const r of running) r.lastTool = t;
  }
  return rows;
}

/** Short status for the busy dock: the running subagents, else the latest tool, else null. */
export function busyLine(rows: SubagentRow[], tools: ToolLine[]): string | null {
  const running = rows.filter((r) => r.status === "running");
  if (running.length > 1 && running.every((r) => r.name === running[0].name)) {
    return `${running.length} ${running[0].label.toLowerCase()}s working`;
  }
  if (running.length) return running.map((r) => r.label).join(", ") + " working";
  const t = tools.at(-1);
  return t ? `${t.tool.replace(/_/g, " ")}` : null;
}
