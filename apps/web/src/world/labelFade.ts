// Lane C: how visible a world label is at a given camera distance. Pure, so
// it is unit tested (labelFade.test.ts).

export type LabelRange = {
  /** Hidden closer than this (it would sit on top of the camera). */
  hideBelow: number;
  /** Fades in over this distance above hideBelow. */
  fadeIn: number;
  /** Fully visible up to here... */
  fadeFrom: number;
  /** ...and gone by here. */
  fadeTo: number;
};

/** Furniture and room labels: readable from across a room, not across a house. */
export const DEFAULT_RANGE: LabelRange = { hideBelow: 1.5, fadeIn: 0.3, fadeFrom: 8, fadeTo: 10 };

const smooth = (t: number) => {
  const c = Math.min(1, Math.max(0, t));
  return c * c * (3 - 2 * c);
};

export function labelOpacity(distance: number, range: Partial<LabelRange> = {}): number {
  const r = { ...DEFAULT_RANGE, ...range };
  const near = smooth((distance - r.hideBelow) / Math.max(1e-6, r.fadeIn));
  const far = 1 - smooth((distance - r.fadeFrom) / Math.max(1e-6, r.fadeTo - r.fadeFrom));
  return near * far;
}
