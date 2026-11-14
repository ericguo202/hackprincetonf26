// Lane C: how the world reaches the local player's physics body. Writing
// me.pos in the store does not move anything: the ecctrl body owns the
// position and overwrites me.pos 20 times a second. Code that has to move the
// player (push-out after a re-furnish, respawn) calls teleport() here, which
// moves the Rapier body itself.
//
// One local player, so a module-level slot; Player.tsx fills it on mount and
// clears it on unmount (it remounts on every zone change).

export type Vec3 = [number, number, number];

export type PlayerControl = {
  /** Live body centre in the current zone's frame (the raw Rapier translation). */
  getPos: () => Vec3;
  /** Move the body to `pos` (body centre) and stop it. */
  teleport: (pos: Vec3) => void;
};

let current: PlayerControl | null = null;

export function registerPlayer(control: PlayerControl): () => void {
  current = control;
  return () => {
    if (current === control) current = null;
  };
}

export function localPlayer(): PlayerControl | null {
  return current;
}
