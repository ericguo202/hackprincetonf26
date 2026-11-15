// Lane D: chat panel, plan feed, listing cards, HUD, scoreboard.
import { useStore } from "../store";

export function ChatPanel() {
  const messages = useStore((s) => s.messages);
  const todos = useStore((s) => s.todos);
  return (
    <aside className="chat-panel">
      {todos.length > 0 ? (
        <ul className="plan">
          {todos.map((t, i) => (
            <li key={i} data-status={t.status}>
              {t.content}
            </li>
          ))}
        </ul>
      ) : null}
      {messages.length === 0 ? <p>Tell the agent how you want this room.</p> : null}
      {messages.map((m, i) => (
        <p key={i} className={m.role}>
          {m.text}
          {m.trace ? (
            <a className="trace-link" href={m.trace} target="_blank" rel="noreferrer">
              View LangSmith trace
            </a>
          ) : null}
        </p>
      ))}
    </aside>
  );
}
