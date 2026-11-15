// Lane D: home-search results in their own panel, apart from the chat. The chat
// keeps the conversation; this keeps the ranked homes (ListingCard). It opens by
// itself when a new search lands; H (or the button) hides it to a small pill.
import { useEffect, useState } from "react";
import { type ListingMatch, useStore } from "../store";
import { ListingCard } from "./ListingCard";
import { stopLooking } from "./pointerLock";

function inField(el: Element | null): boolean {
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
}

export function ListingsPanel() {
  const listings = useStore((s) => s.listings);
  const audits = useStore((s) => s.audits);
  // The result list the player hid. A new search is a new array (net/agent.ts),
  // so it no longer matches and the panel shows again by itself.
  const [hiddenFor, setHiddenFor] = useState<ListingMatch[] | null>(null);
  const open = hiddenFor !== listings;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (inField(document.activeElement) || e.repeat || e.code !== "KeyH") return;
      const current = useStore.getState().listings;
      if (!current.length) return;
      e.preventDefault();
      setHiddenFor((h) => (h === current ? null : current));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!listings.length) return null;
  const count = `${listings.length} ${listings.length === 1 ? "home" : "homes"}`;

  if (!open) {
    return (
      <button type="button" className="listings-pill" onClick={() => setHiddenFor(null)}>
        <span className="dock-key">H</span>
        <span>Homes for you · {count}</span>
      </button>
    );
  }

  return (
    <aside className="listings-panel" aria-label="Homes for you" onMouseDown={() => stopLooking()}>
      <header className="listings-head">
        <strong>
          Homes for you <span className="listings-count">{count}</span>
        </strong>
        <button type="button" className="chat-close" onClick={() => setHiddenFor(listings)}>
          Hide · H
        </button>
      </header>
      <div className="listings-list">
        {listings.map((m, i) => (
          <ListingCard key={m.listing_id} match={m} rank={i + 1} audit={audits[m.listing_id]} />
        ))}
      </div>
    </aside>
  );
}
