// Lane D: Colyseus server definition. One room type, keyed by block_id.
import { defineRoom, defineServer, monitor } from "colyseus";
import type { NextFunction, Request, Response } from "express";
import { NeighborhoodRoom } from "./rooms/NeighborhoodRoom.ts";
import { internalRouter } from "./internal.ts";

/** Browser client is a different origin (static site). Matchmake is HTTP, then WebSocket. */
function cors(req: Request, res: Response, next: NextFunction) {
  const origin = req.headers.origin;
  if (origin) res.setHeader("Access-Control-Allow-Origin", origin);
  else res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Vary", "Origin");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Internal-Secret");
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS");
  if (req.method === "OPTIONS") {
    res.status(204).end();
    return;
  }
  next();
}

const server = defineServer({
  rooms: {
    // Clients join with { block_id, name }; filterBy puts everyone on the same block together.
    neighborhood: defineRoom(NeighborhoodRoom).filterBy(["block_id"]),
  },
  express: (app) => {
    app.set("trust proxy", 1);
    app.use(cors);
    app.get("/health", (_req, res) => {
      res.json({ ok: true });
    });
    app.use("/internal", internalRouter());
    if (process.env.NODE_ENV !== "production") {
      app.use("/monitor", monitor());
    }
  },
});

export default server;
