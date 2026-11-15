// Lane D: game broadcasts on screen. Toasts (top centre), kill feed (top right), damage flash when I'm hit.
import type { CSSProperties } from "react";
import { FEED_MS, TOAST_MS } from "../net/broadcasts";
import { useStore } from "../store";

export function Broadcasts() {
  const toasts = useStore((s) => s.toasts);
  const killFeed = useStore((s) => s.killFeed);
  const hitsOnMe = useStore((s) => s.hitsOnMe);
  const meId = useStore((s) => s.me.id);

  return (
    <>
      {toasts.length > 0 ? (
        <div className="toasts" role="status" aria-live="polite">
          {toasts.map((t) => (
            <p key={t.id} className="toast" style={{ "--life": `${TOAST_MS}ms` } as CSSProperties}>
              {t.text}
            </p>
          ))}
        </div>
      ) : null}
      {killFeed.length > 0 ? (
        <ul className="kill-feed">
          {killFeed.map((k) => (
            <li key={k.id} style={{ "--life": `${FEED_MS}ms` } as CSSProperties}>
              <strong className={k.by === meId ? "me" : ""}>{k.by === meId ? "You" : k.byName}</strong>
              <span className="kill-verb">tagged</span>
              <strong className={k.target === meId ? "me" : ""}>{k.target === meId ? "you" : k.targetName}</strong>
            </li>
          ))}
        </ul>
      ) : null}
      {/* Remounting on each hit restarts the one-shot animation. */}
      {hitsOnMe > 0 ? <div key={hitsOnMe} className="damage-flash" aria-hidden="true" /> : null}
    </>
  );
}
