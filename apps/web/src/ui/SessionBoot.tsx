// Lane D: join the game room (unless SOLO) and open the per-player agent SSE stream.
import { useEffect } from "react";
import { joinBlock } from "../net/game";
import { startAgentSession } from "../net/agent";
import { useStore } from "../store";

function playerName(): string {
  const n = sessionStorage.getItem("bw-name");
  if (n) return n;
  const made = `Player ${Math.floor(10 + Math.random() * 89)}`;
  sessionStorage.setItem("bw-name", made);
  return made;
}

const RETRY_MS = 2000;
let joined = false;

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export function SessionBoot() {
  const sessionId = useStore((s) => s.agentSessionId);

  useEffect(() => {
    if (sessionId) return;
    let stop = false;
    void (async () => {
      const { blockId } = useStore.getState();
      if (!joined) {
        try {
          await joinBlock(blockId, playerName());
          joined = true;
        } catch (err) {
          console.warn("game join failed; chat still starts", err);
        }
      }
      let playerId = useStore.getState().me.id;
      if (!playerId) {
        playerId = crypto.randomUUID();
        useStore.getState().setMe({ id: playerId });
      }
      while (!stop && !useStore.getState().agentSessionId) {
        useStore.getState().set({ agentStatus: "connecting" });
        try {
          await startAgentSession(playerId, blockId);
          useStore.getState().set({ agentStatus: "live" });
          return;
        } catch (err) {
          console.warn("agent session not ready, retrying", err);
          useStore.getState().set({ agentStatus: "offline", agentSessionId: null });
          await sleep(RETRY_MS);
        }
      }
    })();
    return () => {
      stop = true;
    };
  }, [sessionId]);

  return null;
}
