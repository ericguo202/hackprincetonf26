// Lane D (in Lane C's world/, agreed in HANDOFF): moves the local player to a
// spawn point when the server brings them back after an elimination. Renders
// nothing. Indoors: the free-floor spawn farthest from other players
// (respawn.ts). On the block: the block start point, if we have one.
// Player.tsx freezes movement while store.players[me].alive is false.

import { useEffect, useRef } from "react";

import { localPlayer } from "../player/playerControl";
import { useStore, type Vec3 } from "../store";
import { useIndoorSpawns } from "./indoorSpawns";
import { type LifeState, pickIndoorRespawn, stepLife } from "./respawn";

export function RespawnWatcher({ zone, blockSpawn }: { zone: string; blockSpawn: Vec3 | null }) {
  const life = useRef<LifeState>({ sawDeath: false });

  useEffect(
    () =>
      useStore.subscribe((s) => {
        const id = s.me.id;
        if (!id) return;
        const { state, respawned } = stepLife(life.current, s.players[id]?.alive);
        life.current = state;
        if (!respawned) return;

        const others = Object.values(s.players).filter((p) => p.id !== id && p.zone === zone);
        const target =
          zone === "block" ? blockSpawn : pickIndoorRespawn(useIndoorSpawns.getState().byHouse[zone] ?? [], others);
        // The server opened its teleport window when it revived us; teleport()
        // writes me.pos at once, so the next 20 Hz move lands inside it.
        if (target) localPlayer()?.teleport(target);
      }),
    [zone, blockSpawn],
  );

  return null;
}
