// Lane D: one home-search result, written for a buyer. Wording lives in listingCopy.ts;
// the raw evidence and audit values stay one click away under "Show the data".
import type { Audit } from "../contracts.gen";
import type { ListingMatch } from "../store";
import { findingLine, matchLabel, needLine, priceText, type Mark } from "./listingCopy";

const ICON: Record<Mark, string> = { yes: "✓", partial: "~", no: "✗", unknown: "?" };

function raw(value: unknown): string {
  if (value == null) return "n/a";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return "complex";
    }
  }
  return String(value);
}

export function ListingCard({ match, rank, audit }: { match: ListingMatch; rank: number; audit?: Audit }) {
  const lines = match.evidence.map(needLine).filter((l) => l !== null);
  const flagged = (audit?.findings ?? []).filter((f) => f.severity !== "info");
  const price = priceText(match);
  return (
    <article className="listing-card">
      <h3>
        <span>
          {rank === 1 ? "Top pick" : `Option ${rank}`}
          {price ? ` · ${price}` : ""}
        </span>
        <span className="match-label">{matchLabel(match.score)}</span>
      </h3>
      <ul>
        {lines.map((l) => (
          <li key={l.criterion} data-mark={l.mark}>
            <span className="mark" aria-hidden>
              {ICON[l.mark]}
            </span>
            {l.text}
          </li>
        ))}
      </ul>
      {flagged.length ? (
        <div className="worth-checking">
          <p className="worth-title">Worth checking</p>
          {flagged.map((f) => (
            <p key={f.check} className={`finding ${f.severity}`}>
              {findingLine(f)}
            </p>
          ))}
        </div>
      ) : null}
      <details className="raw-data">
        <summary>Show the data</summary>
        <p>{match.listing_id}, fit score {match.score}</p>
        <ul>
          {match.evidence.map((e) => (
            <li key={e.criterion}>
              {e.criterion}: {raw(e.value)} <span>({e.source})</span>
            </li>
          ))}
          {(audit?.findings ?? []).map((f) => (
            <li key={`audit-${f.check}`}>
              {f.check} [{f.severity}]: listed {raw(f.listed)}, observed {raw(f.observed)}
            </li>
          ))}
        </ul>
      </details>
    </article>
  );
}
