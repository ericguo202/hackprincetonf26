// Lane D: agent -> game internal API (contracts/messages.md). Every call needs X-Internal-Secret.
import express, { type NextFunction, type Request, type Response, Router } from "express";
import { type PlanObject, roomsByBlock } from "./rooms/NeighborhoodRoom.ts";

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
    const { player_id, plan } = req.body as {
      player_id: string;
      plan: { plan_id: string; listing_id: string; room_id: string; objects: PlanObject[]; conflicts: unknown[] };
    };
    if (!plan || plan.listing_id !== req.params.listing_id) {
      return void res.status(400).json({ error: "plan.listing_id must match the path" });
    }
    if (plan.conflicts?.length) return void res.status(409).json({ error: "plan has conflicts" });
    room.applyPlan(plan.listing_id, plan.plan_id, plan.objects, player_id);
    const name = room.state.players.get(player_id)?.name ?? "Someone";
    room.broadcast("toast", { text: `${name}'s agent furnished the ${plan.room_id}` });
    res.json({ placed: plan.objects.length });
  });

  r.post("/rooms/:block_id/focus", (req, res) => {
    const room = roomsByBlock.get(req.params.block_id);
    if (!room) return void res.status(404).json({ error: "no room for block" });
    const { player_id, listing_id } = req.body as { player_id: string; listing_id: string };
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
