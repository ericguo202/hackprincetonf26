// Lane C: name tags over remote players (RemotePlayers.tsx). Pure, tested in
// nameTag.test.ts.

import type { LabelRange } from "./labelFade";

/** Names longer than this are cut, with an ellipsis. */
export const NAME_TAG_MAX_CHARS = 16;
/** Above the model's top (CHARACTER.heightM), metres. */
export const NAME_TAG_ABOVE_M = 0.35;
/** Fades like other labels; gone by 25 m. */
export const NAME_TAG_RANGE: LabelRange = { hideBelow: 0.6, fadeIn: 0.3, fadeFrom: 21, fadeTo: 25 };

/** The tag text: trimmed, cut to NAME_TAG_MAX_CHARS (15 characters plus "…" when longer). */
export function nameTagText(name: string | null | undefined): string {
  const chars = [...(name ?? "").trim()]; // by code point, so emoji aren't split
  if (chars.length <= NAME_TAG_MAX_CHARS) return chars.join("");
  return `${chars.slice(0, NAME_TAG_MAX_CHARS - 1).join("")}…`;
}

/** A tag is drawn for other players only, while they're alive, and only if they have a name. */
export function showNameTag(id: string, meId: string | null, alive: boolean, name: string | null | undefined): boolean {
  return id !== meId && alive && nameTagText(name) !== "";
}
