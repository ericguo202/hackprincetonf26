// The client seam (AGENTS.md section 6). Lane D writes network and agent state here;
// Lane C reads it to render. C never opens a socket; D never touches three.js objects.
import { create } from "zustand";
import type { AnimState, GameMode } from "../../../contracts/game-state";
import type { Audit, PlacementPlan } from "./contracts.gen";

export type Vec3 = [number, number, number];
/** "block" outdoors, or the listing_id of the house the player is in. */
export type Zone = string;

export type NetPlayer = {
  id: string;
  name: string;
  zone: Zone;
  pos: Vec3;
  yaw: number;
  anim: AnimState;
  hp: number;
  score: number;
  alive: boolean;
};
export type PlacedObject = PlacementPlan["objects"][number] & { by_player: string };
export type HouseFurniture = { plan_id: string; objects: PlacedObject[] };

export type Todo = { content: string; status: "pending" | "in_progress" | "completed" };
export type SubagentActivity = { name: string; status: "started" | "finished"; summary: string };
export type ToolLine = { agent: string; tool: string; status: string; summary: string };
export type ChatMessage = { role: "user" | "agent"; text: string; final: boolean };
export type ListingMatch = {
  listing_id: string;
  score: number;
  evidence: { criterion: string; passed: boolean; value: unknown; source: string }[];
};

/** Game broadcasts (net/broadcasts.ts). `at` is Date.now() on receipt. */
export type Toast = { id: number; text: string; at: number };
export type KillFeedEntry = { id: number; target: string; by: string; targetName: string; byName: string; at: number };
export type HitOnMe = { shooter: string; shooterName: string; hp: number; at: number };
export type FxEvent = { id: number; kind: string; origin: Vec3; dir: Vec3; at: number };

export type LocalPlayer = { id: string | null; zone: Zone; pos: Vec3; yaw: number; anim: AnimState; roomId: string | null };

export type ClientState = {
  // game service (Colyseus state, written by net/)
  connected: boolean;
  blockId: string;
  mode: GameMode;
  players: Record<string, NetPlayer>;
  houses: Record<string, HouseFurniture>;
  focus: Record<string, string>;
  // game broadcasts (written by net/broadcasts.ts): toasts expire after 4 s, kill feed after 8 s
  toasts: Toast[];
  killFeed: KillFeedEntry[];
  lastHitOnMe: HitOnMe | null;
  /** Increments on every `hit` whose target is me; key a damage flash on it. */
  hitsOnMe: number;
  /** Latest few `fx` broadcasts (other players' shots), for Lane C to render. Newest last. */
  fx: FxEvent[];
  // agent service (SSE, written by net/)
  agentSessionId: string | null;
  agentStatus: "connecting" | "live" | "offline";
  waiting: boolean;
  todos: Todo[];
  subagents: SubagentActivity[];
  tools: ToolLine[];
  messages: ChatMessage[];
  listings: ListingMatch[];
  audits: Record<string, Audit>;
  // local player (written by player/, read by net/ to send `move`)
  me: LocalPlayer;
  setMe: (me: Partial<LocalPlayer>) => void;
  set: (patch: Partial<Omit<ClientState, "set" | "setMe">>) => void;
};

export const useStore = create<ClientState>()((set) => ({
  connected: false,
  blockId: "hou_navidad_01",
  mode: "explore",
  players: {},
  houses: {},
  focus: {},
  toasts: [],
  killFeed: [],
  lastHitOnMe: null,
  hitsOnMe: 0,
  fx: [],
  agentSessionId: null,
  agentStatus: "connecting",
  waiting: false,
  todos: [],
  subagents: [],
  tools: [],
  messages: [],
  listings: [],
  audits: {},
  me: { id: null, zone: "block", pos: [0, 0, 0], yaw: 0, anim: "idle", roomId: null },
  setMe: (me) => set((s) => ({ me: { ...s.me, ...me } })),
  set: (patch) => set(patch),
}));
