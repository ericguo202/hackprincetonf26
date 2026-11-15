// Lane D: foam-mode toggle, local score, Tab scoreboard, game broadcasts (toasts, kill feed, damage flash).
import { useEffect, useState } from "react";
import { setMode } from "../net/game";
import { useStore } from "../store";
import { Broadcasts } from "./Broadcasts";

export function Hud() {
  const mode = useStore((s) => s.mode);
  const me = useStore((s) => s.me);
  const players = useStore((s) => s.players);
  const [board, setBoard] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      if (e.code !== "Tab") return;
      e.preventDefault();
      setBoard((v) => !v);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const mine = me.id ? players[me.id] : undefined;
  const ranked = Object.values(players).sort((a, b) => b.score - a.score);

  return (
    <>
      <div className="hud">
        <button
          type="button"
          className={mode === "foam" ? "on" : ""}
          onClick={() => setMode(mode === "foam" ? "explore" : "foam")}
        >
          {mode === "foam" ? "Foam on" : "Foam fight"}
        </button>
        <span>
          {mine ? `${mine.score} pts · ${mine.hp} hp` : me.zone === "block" ? "On the block" : me.zone}
        </span>
        <span className="hud-keys">T concierge · Esc play · Tab scores</span>
      </div>
      {board ? (
        <div className="scoreboard">
          <h2>Scores</h2>
          {ranked.length === 0 ? <p>No other players yet.</p> : null}
          <ol>
            {ranked.map((p) => (
              <li key={p.id}>
                {p.name} {p.score}
                {p.id === me.id ? " (you)" : ""}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      <Broadcasts />
    </>
  );
}
