import assert from "node:assert";
import { boot, type ColyseusTestServer } from "@colyseus/testing";
import appConfig from "../src/app.config.ts";

describe("NeighborhoodRoom", () => {
  let colyseus: ColyseusTestServer<typeof appConfig>;

  before(async () => {
    colyseus = await boot(appConfig);
  });
  after(async () => colyseus.shutdown());
  beforeEach(async () => colyseus.cleanup());

  it("adds a player in the block zone and relays a short move", async () => {
    const room = await colyseus.createRoom("neighborhood", { block_id: "hou_heights_01" });
    const client = await colyseus.connectTo(room, { name: "ana" });
    assert.strictEqual(room.state.players.get(client.sessionId)?.zone, "block");

    client.send("move", { x: 1, y: 0, z: 1, yaw: 0, anim: "run" });
    await room.waitForNextPatch();
    assert.strictEqual(room.state.players.get(client.sessionId)?.x, 1);
  });

  it("rejects a move over 3 m", async () => {
    const room = await colyseus.createRoom("neighborhood", { block_id: "hou_heights_01" });
    const client = await colyseus.connectTo(room, { name: "ana" });
    client.send("move", { x: 10, y: 0, z: 0, yaw: 0, anim: "run" });
    await room.waitForNextPatch();
    assert.strictEqual(room.state.players.get(client.sessionId)?.x, 0);
  });
});
