// Lane D: chat overlay. Play with a tiny dock; T opens the concierge.
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { sendAgentMessage } from "../net/agent";
import { useStore } from "../store";
import { startLooking, stopLooking, useLooking } from "./pointerLock";
import { useBuild } from "../world/buildState";
import { parseInlineMarkdown } from "./inlineMarkdown";
import { AgentFeed } from "./AgentFeed";
import { busyLine, subagentRows } from "./handoffs";

function InlineMarkdown({ text }: { text: string }) {
  return (
    <>
      {parseInlineMarkdown(text).map((s, i) => {
        let node: ReactNode = s.text;
        if (s.italic) node = <em>{node}</em>;
        if (s.bold) node = <strong>{node}</strong>;
        return <span key={i}>{node}</span>;
      })}
    </>
  );
}

const SUGGESTIONS = [
  {
    label: "Movie night in the living room",
    text: "Furnish the living room for a family that watches movies together",
  },
  { label: "Set up a nerf war", text: "Set up the living room for a nerf war" },
  { label: "Turn it into a playroom", text: "Make it a playroom instead" },
  {
    label: "Find a home under $450k",
    text: "We're new parents with an infant. Find us a home to buy under 450k.",
  },
];

function inField(el: Element | null): boolean {
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
}

export function ChatPanel() {
  const sessionId = useStore((s) => s.agentSessionId);
  const agentStatus = useStore((s) => s.agentStatus);
  const waiting = useStore((s) => s.waiting);
  const messages = useStore((s) => s.messages);
  const subagents = useStore((s) => s.subagents);
  const tools = useStore((s) => s.tools);
  const todos = useStore((s) => s.todos);
  const looking = useLooking();
  const [open, setOpen] = useState(true);
  const [draft, setDraft] = useState("");
  const log = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const canSend = Boolean(sessionId) && !waiting;
  const started = messages.length > 0;
  const expanded = open && !looking;
  // The feed belongs to the latest turn: it sits right after the player's last message.
  const lastUser = messages.map((m) => m.role).lastIndexOf("user");
  const busy = waiting ? busyLine(subagentRows(subagents, tools), tools) : null;

  // Build mode frees the cursor, which would expand this panel over the scene:
  // fold it to the dock when building starts (T opens it again).
  useEffect(
    () =>
      useBuild.subscribe((s, prev) => {
        if (s.active && !prev.active) setOpen(false);
      }),
    [],
  );

  useEffect(() => {
    log.current?.scrollTo(0, log.current.scrollHeight);
  }, [messages, expanded, subagents, tools, todos]);

  function enterTalk() {
    stopLooking();
    setOpen(true);
    requestAnimationFrame(() => input.current?.focus());
  }

  function backToGame() {
    setOpen(false);
    input.current?.blur();
    startLooking();
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (inField(document.activeElement)) {
        if (e.key === "Escape") {
          e.preventDefault();
          backToGame();
        }
        return;
      }
      if (e.repeat) return;
      if (e.code === "KeyT" || e.key === "/") {
        e.preventDefault();
        enterTalk();
      }
      if (e.key === "Escape" && expanded) {
        e.preventDefault();
        backToGame();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  async function send(text: string) {
    if (!sessionId || waiting) return;
    setDraft("");
    backToGame();
    await sendAgentMessage(sessionId, text);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void send(draft);
  }

  function onComposerKey(e: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(draft);
    }
  }

  const suggestions =
    started ? null : (
      <div className="suggest">
        <p className="suggest-label">Suggestions</p>
        <ul>
          {SUGGESTIONS.map((s) => (
            <li key={s.label}>
              <button type="button" disabled={!canSend} onClick={() => void send(s.text)}>
                {s.label}
              </button>
            </li>
          ))}
        </ul>
      </div>
    );

  const composer = (
    <form className="composer" onSubmit={onSubmit}>
      <textarea
        ref={input}
        rows={2}
        value={draft}
        placeholder={
          agentStatus === "live"
            ? "Message the concierge…"
            : agentStatus === "offline"
              ? "Agent offline, retrying…"
              : "Connecting…"
        }
        disabled={!canSend}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onComposerKey}
      />
      <button type="submit" className="send" disabled={!canSend || !draft.trim()} aria-label="Send">
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden>
          <path
            fill="currentColor"
            d="M3.4 20.6 22 12 3.4 3.4 3 10.1 15.2 12 3 13.9z"
          />
        </svg>
      </button>
    </form>
  );

  if (!expanded) {
    return (
      <button type="button" className={`chat-dock${waiting ? " busy" : ""}`} onClick={enterTalk}>
        {waiting ? (
          <>
            <span className="spin" aria-hidden />
            <span className="dock-copy">
              <strong>Concierge is working</strong>
              <em>{busy ?? "Keep playing"} · T to read</em>
            </span>
          </>
        ) : (
          <>
            <span className="dock-key">T</span>
            <span className="dock-copy">
              <strong>Talk to the concierge</strong>
              <em>Ask to furnish, search, or change a room</em>
            </span>
          </>
        )}
      </button>
    );
  }

  return (
    <aside className="chat-panel" onMouseDown={() => stopLooking()}>
      <header className="chat-head">
        <strong>Concierge</strong>
        <button type="button" className="chat-close" onClick={backToGame}>
          Back to game · Esc
        </button>
      </header>

      <div className="chat-log" ref={log}>
        {!started ? <p className="hint">Pick a suggestion or type what you want this room to feel like.</p> : null}
        {lastUser < 0 ? <AgentFeed /> : null}
        {messages.map((m, i) => [
          m.text ? (
            <p key={i} className={m.role}>
              {m.role === "agent" ? <InlineMarkdown text={m.text} /> : m.text}
              {m.trace ? (
                <a className="trace-link" href={m.trace} target="_blank" rel="noreferrer">
                  View LangSmith trace
                </a>
              ) : null}
            </p>
          ) : null,
          i === lastUser ? <AgentFeed key="feed" /> : null,
        ])}
        {waiting ? <p className="hint thinking">Thinking…</p> : null}
      </div>

      {suggestions}
      {composer}
    </aside>
  );
}
