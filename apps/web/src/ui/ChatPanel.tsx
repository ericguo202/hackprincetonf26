// Lane C: chat panel, plan view, listing cards, HUD.
import { useStore } from "../bridge/store";

export function ChatPanel() {
  const messages = useStore((s) => s.messages);
  return (
    <aside className="chat-panel">
      {messages.length === 0 ? <p>Tell the agent what you need.</p> : null}
      {messages.map((m, i) => (
        <p key={i}>{m.text}</p>
      ))}
    </aside>
  );
}
