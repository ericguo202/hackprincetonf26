// Lane D: per-player agent service client. POST messages, read SSE (contracts/agent-events.md).
import type { Audit } from "../contracts.gen";
import {
  type ChatMessage,
  type ListingMatch,
  type SubagentActivity,
  type Todo,
  type ToolLine,
  useStore,
} from "../store";

const AGENT_URL = import.meta.env.VITE_AGENT_URL ?? "/agent";
const EVENTS = [
  "agent.todos",
  "agent.subagent",
  "agent.tool",
  "agent.message",
  "listings.results",
  "audit.findings",
  "error",
] as const;

type AgentEvents = {
  "agent.todos": { todos: Todo[] };
  "agent.subagent": SubagentActivity;
  "agent.tool": ToolLine;
  "agent.message": { text: string; final: boolean };
  "listings.results": { matches: ListingMatch[] };
  "audit.findings": Audit;
  error: { message: string };
};

/** Fold one agent event into the store. Exported for tests and the mock replay. */
export function applyAgentEvent<K extends keyof AgentEvents>(type: K, data: AgentEvents[K]): void;
export function applyAgentEvent(type: string, data: unknown): void;
export function applyAgentEvent(type: string, raw: unknown) {
  const s = useStore.getState();
  switch (type) {
    case "agent.todos":
      return s.set({ todos: (raw as AgentEvents["agent.todos"]).todos });
    case "agent.subagent":
      return s.set({ subagents: [...s.subagents, raw as SubagentActivity] });
    case "agent.tool":
      return s.set({ tools: [...s.tools, raw as ToolLine] });
    case "agent.message": {
      const data = raw as AgentEvents["agent.message"];
      const last = s.messages.at(-1);
      // Streamed text: append to the open agent message until one arrives with final: true.
      if (last && last.role === "agent" && !last.final) {
        const merged: ChatMessage = { role: "agent", text: last.text + data.text, final: data.final };
        return s.set({ messages: [...s.messages.slice(0, -1), merged] });
      }
      return s.set({ messages: [...s.messages, { role: "agent", text: data.text, final: data.final }] });
    }
    case "listings.results":
      return s.set({ listings: (raw as AgentEvents["listings.results"]).matches });
    case "audit.findings": {
      const audit = raw as Audit;
      return s.set({ audits: { ...s.audits, [audit.listing_id]: audit } });
    }
    case "error": {
      const text = `Error: ${(raw as AgentEvents["error"]).message}`;
      return s.set({ messages: [...s.messages, { role: "agent", text, final: true }] });
    }
  }
}

/** Attach the turn's LangSmith link to the latest agent reply (it closes with final: true). */
export async function fetchTrace(sessionId: string): Promise<void> {
  try {
    const r = await fetch(`${AGENT_URL}/sessions/${sessionId}/trace`);
    const { url } = (await r.json()) as { url: string | null };
    const s = useStore.getState();
    let i = s.messages.length - 1;
    while (i >= 0 && s.messages[i].role !== "agent") i--;
    if (!url || i < 0) return;
    s.set({ messages: s.messages.map((m, j) => (j === i ? { ...m, trace: url } : m)) });
  } catch {
    // tracing off or agent unreachable: the reply just has no link
  }
}

export async function startAgentSession(playerId: string, blockId: string): Promise<string> {
  const r = await fetch(`${AGENT_URL}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ player_id: playerId, block_id: blockId }),
  });
  const { session_id } = (await r.json()) as { session_id: string };
  const es = new EventSource(`${AGENT_URL}/sessions/${session_id}/stream`);
  for (const type of EVENTS) {
    es.addEventListener(type, (e) => {
      const data = JSON.parse((e as MessageEvent).data);
      applyAgentEvent(type, data);
      if (type === "agent.message" && data.final) void fetchTrace(session_id);
    });
  }
  return session_id;
}

export async function sendAgentMessage(sessionId: string, text: string) {
  const s = useStore.getState();
  s.set({ messages: [...s.messages, { role: "user", text, final: true }] });
  await fetch(`${AGENT_URL}/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
}
