// The shared seam of the web app (AGENTS.md section 4).
// Lane C writes to it from socket events; Lane A reads from it to render.
import { create } from "zustand";
import type { Listing, PlacementPlan } from "./contracts.gen";

export type Todo = { content: string; status: "pending" | "in_progress" | "completed" };
export type SubagentActivity = { name: string; status: "started" | "finished"; summary: string };
export type ChatMessage = { role: "user" | "agent"; text: string; final: boolean };
export type PlayerState = {
  pos: [number, number, number];
  listingId: string | null;
  roomId: string | null;
};

export type BridgeState = {
  listings: Listing[];
  focus: string | null;
  todos: Todo[];
  subagents: SubagentActivity[];
  messages: ChatMessage[];
  placement: PlacementPlan | null;
  player: PlayerState;
  setPlayer: (player: Partial<PlayerState>) => void;
};

export const useStore = create<BridgeState>()((set) => ({
  listings: [],
  focus: null,
  todos: [],
  subagents: [],
  messages: [],
  placement: null,
  player: { pos: [0, 0, 0], listingId: null, roomId: null },
  setPlayer: (player) => set((s) => ({ player: { ...s.player, ...player } })),
}));
