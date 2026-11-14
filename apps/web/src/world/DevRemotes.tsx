// Lane C, dev only: VITE_DEV_REMOTES=1. Writes two fake players into
// store.players at 20 Hz, like Lane D's net code would, with a little arrival
// jitter, so RemotePlayers' interpolation is exercised the way the network
// will exercise it. Paths and hit rules: fakeRemotes.ts. Writes through the
// store's own set(); a connected game server replaces store.players on its
// next patch. store.ts is not changed.

import { useEffect } from "react";

import type { Point2, Room } from "../contracts.gen";
import { useStore, type NetPlayer } from "../store";
import { FOOT_BELOW_CENTRE_M } from "../player/characterAsset";
import { DEV_REMOTE_IDS, fakeAlive, fakeAnim, fakePose } from "./fakeRemotes";
import { minDistToPolygon } from "./geometry";
import { blockSpawn, polygonCentroid, type LoadedPack } from "./pack";

const START_ROOM = import.meta.env.VITE_START_ROOM as string | undefined;
const SEND_MS = 50; // 20 Hz, like the server's patches
const JITTER_MS = 25;

/** Where the fakes play: the dev start room, the living room, the largest room; the sidewalk on the block. */
function arena(pack: LoadedPack, zone: string): { centre: Point2; radius: number; floorY: number } {
  const rooms: Room[] = pack.roomsByHouse[zone] ?? [];
  const room =
    rooms.find((r) => r.id === START_ROOM) ??
    rooms.find((r) => r.label === "living_room") ??
    [...rooms].sort((a, b) => b.area_m2 - a.area_m2)[0];
  if (zone === "block" || !room) {
    const s = blockSpawn(pack.manifest);
    return { centre: [s[0], s[2] - 3], radius: 2, floorY: s[1] };
  }
  const centre = polygonCentroid(room.polygon);
  const radius = Math.max(0.6, Math.min(1.8, minDistToPolygon(centre, room.polygon) - 0.5));
  return { centre, radius, floorY: room.floor_y };
}

export function DevRemotes({ pack, zone }: { pack: LoadedPack; zone: string }) {
  useEffect(() => {
    const { centre, radius, floorY } = arena(pack, zone);
    const timers = new Set<ReturnType<typeof setTimeout>>();
    const send = () => {
      const t = performance.now() / 1000;
      const fakes: Record<string, NetPlayer> = {};
      DEV_REMOTE_IDS.forEach((id, i) => {
        const pose = fakePose(id, t, centre, radius);
        fakes[id] = {
          id,
          name: `Dev ${i + 1}`,
          zone,
          pos: [pose.pos2[0], floorY + FOOT_BELOW_CENTRE_M, pose.pos2[1]],
          yaw: pose.yaw,
          anim: fakeAnim(id, t, pose),
          hp: 3,
          score: 0,
          alive: fakeAlive(id, t),
        };
      });
      // Deliver a little late and unevenly, like a network.
      const timer = setTimeout(() => {
        timers.delete(timer);
        const s = useStore.getState();
        s.set({ players: { ...withoutFakes(s.players), ...fakes } });
      }, Math.random() * JITTER_MS);
      timers.add(timer);
    };
    send();
    const interval = setInterval(send, SEND_MS);
    console.info(`[dev remotes] ${DEV_REMOTE_IDS.length} fake players in ${zone}, radius ${radius.toFixed(2)} m`);
    return () => {
      clearInterval(interval);
      timers.forEach(clearTimeout);
      const s = useStore.getState();
      s.set({ players: withoutFakes(s.players) });
    };
  }, [pack, zone]);
  return null;
}

function withoutFakes(players: Record<string, NetPlayer>): Record<string, NetPlayer> {
  const out = { ...players };
  for (const id of DEV_REMOTE_IDS) delete out[id];
  return out;
}
