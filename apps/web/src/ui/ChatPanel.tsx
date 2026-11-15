// Lane D: chat overlay. Play with a tiny dock; T opens the concierge.
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { sendAgentMessage } from "../net/agent";
import { useStore } from "../store";
import { startLooking, stopLooking, useLooking } from "./pointerLock";

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

function fmt(value: unknown): string {
  if (typeof value === "number") {
    if (value >= 1000 && Number.isInteger(value)) return value.toLocaleString("en-US");
    return String(value);
  }
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (value == null) return "n/a";
  if (typeof value === "object") {
    const vals = Object.values(value as Record<string, unknown>);
    if (vals.length === 1) return fmt(vals[0]);
    try {
      return JSON.stringify(value);
    } catch {
      return "complex";
    }
  }
  return String(value);
}

function inField(el: Element | null): boolean {
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
}

export function ChatPanel() {
  const sessionId = useStore((s) => s.agentSessionId);
  const agentStatus = useStore((s) => s.agentStatus);
  const waiting = useStore((s) => s.waiting);
  const messages = useStore((s) => s.messages);
  const listings = useStore((s) => s.listings);
  const audits = useStore((s) => s.audits);
  const looking = useLooking();
  const [open, setOpen] = useState(true);
  const [draft, setDraft] = useState("");
  const log = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const canSend = Boolean(sessionId) && !waiting;
  const started = messages.length > 0;
  const expanded = open && !looking;

  useEffect(() => {
    log.current?.scrollTo(0, log.current.scrollHeight);
  }, [messages, listings, expanded]);

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
              <em>Keep playing · T to read</em>
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
        {messages.map((m, i) =>
          m.text ? (
            <p key={i} className={m.role}>
              {m.text}
              {m.trace ? (
                <a className="trace-link" href={m.trace} target="_blank" rel="noreferrer">
                  View LangSmith trace
                </a>
              ) : null}
            </p>
          ) : null,
        )}
        {waiting ? <p className="hint thinking">Thinking…</p> : null}

        {listings.map((m) => {
          const audit = audits[m.listing_id];
          return (
            <article key={m.listing_id} className="listing-card">
              <h3>
                {m.listing_id} <span>{Math.round(m.score * 100)} fit</span>
              </h3>
              <ul>
                {m.evidence.map((e) => (
                  <li key={e.criterion} data-ok={e.passed}>
                    {e.criterion}: {fmt(e.value)}
                  </li>
                ))}
              </ul>
              {audit?.findings
                .filter((f) => f.severity !== "info")
                .map((f) => (
                  <p key={f.check} className={`finding ${f.severity}`}>
                    {f.check}: listed {fmt(f.listed)} vs scan {fmt(f.observed)}
                  </p>
                ))}
            </article>
          );
        })}
      </div>

      {suggestions}
      {composer}
    </aside>
  );
}
