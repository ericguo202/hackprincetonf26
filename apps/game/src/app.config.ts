// Lane D: Colyseus server definition. One room type, keyed by block_id.
import { defineRoom, defineServer, monitor } from "colyseus";
import { NeighborhoodRoom } from "./rooms/NeighborhoodRoom.ts";
import { internalRouter } from "./internal.ts";

const server = defineServer({
  rooms: {
    // Clients join with { block_id, name }; filterBy puts everyone on the same block together.
    neighborhood: defineRoom(NeighborhoodRoom).filterBy(["block_id"]),
  },
  express: (app) => {
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
