// Lane D: agent -> game internal API (contracts/messages.md). Every call needs X-Internal-Secret.
import express, { type NextFunction, type Request, type Response, Router } from "express";
import { type PlanObject, roomsByBlock } from "./rooms/NeighborhoodRoom.ts";

/** "living_room" -> "living room"; never the raw room id. Absent or blank -> "a room". */
export function roomPhrase(label: unknown): string {
  const words = typeof label === "string" ? label.replace(/[_-]+/g, " ").trim().replace(/\s+/g, " ") : "";
  return words ? `the ${words.toLowerCase()}` : "a room";
}

function requireSecret(req: Request, res: Response, next: NextFunction) {
  const secret = process.env.GAME_INTERNAL_SECRET;
  if (!secret || req.header("X-Internal-Secret") !== secret) {
    res.status(401).json({ error: "unauthorized" });
    return;
  }
  next();
}

export function internalRouter(): Router {
  const r = Router();
  r.use(express.json({ limit: "1mb" }));
  r.use(requireSecret);

  r.post("/rooms/:block_id/houses/:listing_id/plan", (req, res) => {
    const room = roomsByBlock.get(req.params.block_id);
    if (!room) return void res.status(404).json({ error: "no room for block" });
    const { player_id, plan, room_label } = req.body as {
      player_id: string;
      room_label?: string | null;
      plan: { plan_id: string; listing_id: string; room_id: string; objects: PlanObject[]; conflicts: unknown[] };
    };
    if (!plan || plan.listing_id !== req.params.listing_id) {
      return void res.status(400).json({ error: "plan.listing_id must match the path" });
    }
    // Room ids are "<listing_id>.<n>" (contracts/room.schema.json); a room of another house is a bug.
    if (typeof plan.room_id !== "string" || !plan.room_id.startsWith(`${plan.listing_id}.`)) {
      return void res.status(400).json({ error: "plan.room_id must be a room of this listing" });
    }
    if (!Array.isArray(plan.objects)) return void res.status(400).json({ error: "plan.objects must be an array" });
    if (plan.conflicts?.length) return void res.status(409).json({ error: "plan has conflicts" });
    room.applyPlan(plan.listing_id, plan.room_id, plan.plan_id, plan.objects, player_id);
    const name = room.state.players.get(player_id)?.name ?? "Someone";
    room.broadcast("toast", { text: `${name}'s agent furnished ${roomPhrase(room_label)}` });
    res.json({ placed: plan.objects.length });
  });

  // v2.6.1: empty one room (room_id) or the whole house (no room_id), hand-placed pieces included.
  r.post("/rooms/:block_id/houses/:listing_id/clear", (req, res) => {
    const room = roomsByBlock.get(req.params.block_id);
    if (!room) return void res.status(404).json({ error: "no room for block" });
    const listingId = req.params.listing_id;
    const { player_id, room_id, room_label } = req.body as {
      player_id: string;
      room_id?: string | null;
      room_label?: string | null;
    };
    const oneRoom = room_id !== undefined && room_id !== null;
    if (oneRoom && (typeof room_id !== "string" || !room_id.startsWith(`${listingId}.`))) {
      return void res.status(400).json({ error: "room_id must be a room of this listing" });
    }
    const rooms = room.clearFurniture(listingId, oneRoom ? room_id : undefined);
    const removed = rooms.reduce((n, r) => n + r.removed, 0);
    if (removed > 0) {
      const name = room.state.players.get(player_id)?.name ?? "Someone";
      room.broadcast("toast", { text: `${name}'s agent cleared ${oneRoom ? roomPhrase(room_label) : "the house"}` });
    }
    res.json({ removed, rooms });
  });

  r.post("/rooms/:block_id/focus", (req, res) => {
    const room = roomsByBlock.get(req.params.block_id);
    if (!room) return void res.status(404).json({ error: "no room for block" });
    const { player_id, listing_id } = req.body as { player_id: string; listing_id: string };
    // The client walks the player to this house's door on a focus change (HANDOFF 16:45), a jump.
    if (room.state.focus.get(player_id) !== listing_id) room.allowTeleport(player_id);
    room.state.focus.set(player_id, listing_id);
    res.json({ ok: true });
  });

  r.get("/rooms/:block_id/players/:player_id", (req, res) => {
    const p = roomsByBlock.get(req.params.block_id)?.state.players.get(req.params.player_id);
    if (!p) return void res.status(404).json({ error: "no such player" });
    res.json({ zone: p.zone, x: p.x, y: p.y, z: p.z });
  });

  return r;
}
