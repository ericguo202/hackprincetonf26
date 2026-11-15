import assert from "node:assert";
import { boot, type ColyseusTestServer } from "@colyseus/testing";
import appConfig from "../src/app.config.ts";
import { TELEPORT_GRACE_MS } from "../src/rooms/NeighborhoodRoom.ts";

const BLOCK = "hou_navidad_01";
const SECRET = "test-secret";

describe("NeighborhoodRoom", () => {
  let colyseus: ColyseusTestServer<typeof appConfig>;

  before(async () => {
    process.env.GAME_INTERNAL_SECRET = SECRET;
    colyseus = await boot(appConfig);
  });
  after(async () => colyseus.shutdown());
  beforeEach(async () => colyseus.cleanup());

  const move = (x: number) => ({ x, y: 0, z: 0, yaw: 0, anim: "run" });
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

  /** Join, then wait out the join grace window so the step check applies. */
  async function joinSettled(name = "ana") {
    const room = await colyseus.createRoom("neighborhood", { block_id: BLOCK });
    const client = await colyseus.connectTo(room, { name });
    await sleep(TELEPORT_GRACE_MS + 100);
    return { room, client, x: () => room.state.players.get(client.sessionId)?.x };
  }

  it("adds a player in the block zone and relays a short move", async () => {
    const room = await colyseus.createRoom("neighborhood", { block_id: BLOCK });
    const client = await colyseus.connectTo(room, { name: "ana" });
    assert.strictEqual(room.state.players.get(client.sessionId)?.zone, "block");

    client.send("move", { x: 1, y: 0, z: 1, yaw: 0, anim: "run" });
    await room.waitForNextPatch();
    assert.strictEqual(room.state.players.get(client.sessionId)?.x, 1);
  });

  it("accepts the spawn jump after join, even after small moves", async () => {
    const room = await colyseus.createRoom("neighborhood", { block_id: BLOCK });
    const client = await colyseus.connectTo(room, { name: "ana" });
    client.send("move", move(0));
    client.send("move", move(0.1));
    client.send("move", move(40));
    await room.waitForNextPatch();
    assert.strictEqual(room.state.players.get(client.sessionId)?.x, 40);
  });

  it("rejects a move over 3 m once the join window has passed", async () => {
    const { room, client, x } = await joinSettled();
    client.send("move", move(10));
    await room.waitForNextPatch();
    assert.strictEqual(x(), 0);
  });

  it("rejects non-finite positions even in the window", async () => {
    const room = await colyseus.createRoom("neighborhood", { block_id: BLOCK });
    const client = await colyseus.connectTo(room, { name: "ana" });
    client.send("move", { x: Number.NaN, y: 0, z: 0, yaw: 0, anim: "run" });
    await room.waitForNextPatch();
    assert.strictEqual(room.state.players.get(client.sessionId)?.x, 0);
  });

  it("accepts the frame jump after enter_zone", async () => {
    const { room, client, x } = await joinSettled();
    client.send("enter_zone", { zone: "hou_236595179" });
    client.send("move", move(25));
    await room.waitForNextPatch();
    assert.strictEqual(room.state.players.get(client.sessionId)?.zone, "hou_236595179");
    assert.strictEqual(x(), 25);
  });

  it("does not open a window when enter_zone repeats the current zone", async () => {
    const { room, client, x } = await joinSettled();
    client.send("enter_zone", { zone: "block" });
    client.send("move", move(25));
    await room.waitForNextPatch();
    assert.strictEqual(x(), 0);
  });

  it("accepts the door teleport after an agent focus, even after small moves", async () => {
    const { room, client, x } = await joinSettled();
    const res = await colyseus.http.post(`/internal/rooms/${BLOCK}/focus`, {
      headers: { "Content-Type": "application/json", "X-Internal-Secret": SECRET },
      body: { player_id: client.sessionId, listing_id: "hou_236595179" },
    });
    assert.strictEqual(res.statusCode, 200);
    client.send("move", move(0.1));
    client.send("move", move(30));
    await room.waitForNextPatch();
    assert.strictEqual(x(), 30);
  });

  it("closes the window again after TELEPORT_GRACE_MS", async () => {
    const { room, client, x } = await joinSettled();
    client.send("enter_zone", { zone: "hou_236595179" });
    client.send("move", move(25));
    await room.waitForNextPatch();
    await sleep(TELEPORT_GRACE_MS + 100);
    client.send("move", move(50));
    await room.waitForNextPatch();
    assert.strictEqual(x(), 25);
  });
});
