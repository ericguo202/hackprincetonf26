// Lane C: my hits on remote players, by player id, for the hit flash
// (Character tints for HIT_FLASH_S) and for crediting an elimination to a hit
// of mine (a big foam burst). A small module store, not store.ts: it holds
// only local effects state. Times are seconds (performance.now based).

/** How long a hit player's model stays tinted. */
export const HIT_FLASH_S = 0.12;
/**
 * A remote going down within this long of my hit on them counts as my
 * elimination (covers the server round trip and the 100 ms interpolation).
 */
export const KILL_CREDIT_S = 1.0;

const lastHit = new Map<string, number>();
const popAt = new Map<string, number>();

export const nowS = (): number => performance.now() / 1000;

/** I hit `id` (blaster dart or noodle swing with target_id). */
export function markHit(id: string, now = nowS()): void {
  lastHit.set(id, now);
}

/** Flash strength for `id`: 1 at the hit, falling to 0 at HIT_FLASH_S, 0 after (and with no hit). */
export function flashLevel(id: string, now = nowS()): number {
  const t = lastHit.get(id);
  if (t === undefined) return 0;
  const age = now - t;
  if (age < 0 || age >= HIT_FLASH_S) return 0;
  return 1 - age / HIT_FLASH_S;
}

/** I hit `id` within the last `within` seconds. */
export function hitRecently(id: string, now = nowS(), within = KILL_CREDIT_S): boolean {
  const t = lastHit.get(id);
  return t !== undefined && now - t >= 0 && now - t <= within;
}

/** Forget a credited hit (one elimination burst per hit). */
export function clearHit(id: string): void {
  lastHit.delete(id);
}

/** Tests only. */
export function resetHits(): void {
  lastHit.clear();
  popAt.clear();
}

// ---------------------------------------------------------------------------
// Elimination foam pop: the model's quick scale-up, and when to play the pop.
// ---------------------------------------------------------------------------

/** The pop: the model scales up to POP_PEAK and back over POP_SCALE_S. */
export const POP_SCALE_S = 0.15;
export const POP_PEAK = 1.15;

/** `id` was eliminated in my view: start their pop. */
export function markPop(id: string, now = nowS()): void {
  popAt.set(id, now);
}

/** Model scale for `id`: 1, rising to POP_PEAK and back to exactly 1 over POP_SCALE_S after markPop. */
export function popScale(id: string, now = nowS()): number {
  const t = popAt.get(id);
  if (t === undefined) return 1;
  const age = now - t;
  if (age <= 0 || age >= POP_SCALE_S) return 1;
  return 1 + (POP_PEAK - 1) * Math.sin((Math.PI * age) / POP_SCALE_S);
}

type PlayerLike = { id: string; zone: string; alive: boolean };

/**
 * Who pops on this players patch: every remote in my zone whose alive went
 * true -> false. `mine` when I hit them within KILL_CREDIT_S (my elimination).
 * Pure apart from reading my hits; the caller clears the credited hit.
 */
export function eliminationPops(
  players: Record<string, PlayerLike>,
  prev: Record<string, PlayerLike>,
  meId: string | null,
  myZone: string,
  now = nowS(),
): { id: string; mine: boolean }[] {
  const out: { id: string; mine: boolean }[] = [];
  for (const p of Object.values(players)) {
    if (p.id === meId || p.alive || p.zone !== myZone || prev[p.id]?.alive !== true) continue;
    out.push({ id: p.id, mine: hitRecently(p.id, now) });
  }
  return out;
}
