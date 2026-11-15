import assert from "node:assert";
import { boot, type ColyseusTestServer } from "@colyseus/testing";
import appConfig from "../src/app.config.ts";
import {
  FULL_HP,
  MAX_RANGE_M,
  MIN_FIRE_INTERVAL_MS,
  RESPAWN_MS,
  TELEPORT_GRACE_MS,
} from "../src/rooms/NeighborhoodRoom.ts";

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
  async function postPlan(playerId: string, roomLabel?: string) {
    const plan = {
      plan_id: "p_toast",
      listing_id: "hou_236595179",
      room_id: "hou_236595179.r3",
      status: "placed",
      objects: [{ instance_id: "o1", asset_id: "sofa_01", x: 1, y: 0, z: 1, rot_deg: 0, reason: "test" }],
      conflicts: [],
    };
    return colyseus.http.post(`/internal/rooms/${BLOCK}/houses/hou_236595179/plan`, {
      headers: { "Content-Type": "application/json", "X-Internal-Secret": SECRET },
      body: roomLabel === undefined ? { player_id: playerId, plan } : { player_id: playerId, plan, room_label: roomLabel },
    });
  }

  async function furnishToast(roomLabel?: string): Promise<string> {
    const room = await colyseus.createRoom("neighborhood", { block_id: BLOCK });
    const client = await colyseus.connectTo(room, { name: "ana" });
    const toast = new Promise<string>((resolve) => client.onMessage("toast", (m: { text: string }) => resolve(m.text)));
    const res = await postPlan(client.sessionId, roomLabel);
    assert.strictEqual(res.statusCode, 200);
    return toast;
  }

  it("names the room by its label in the furnish toast", async () => {
    assert.strictEqual(await furnishToast("living_room"), "ana's agent furnished the living room");
  });

  it("falls back to 'a room', never the raw room id, without room_label", async () => {
    const text = await furnishToast();
    assert.strictEqual(text, "ana's agent furnished a room");
    assert.ok(!text.includes("hou_236595179"));
  });

  describe("fire", () => {
    const HEAD = 0.5; // the client fires from its head, above the body centre that move reports
    const BLAST_GAP = MIN_FIRE_INTERVAL_MS.blaster + 60;
    const NOODLE_GAP = MIN_FIRE_INTERVAL_MS.noodle + 60;

    /** Shooter `a` at the origin, target `b` at (bx, 0, 0), room in the given mode. */
    async function arena(bx = 5, mode = "foam") {
      const room = await colyseus.createRoom("neighborhood", { block_id: BLOCK });
      const a = await colyseus.connectTo(room, { name: "ana" });
      const b = await colyseus.connectTo(room, { name: "ben" });
      for (const c of [a, b]) for (const type of ["hit", "eliminated", "fx"]) c.onMessage(type, () => {}); // quiet SDK warnings
      b.send("move", { x: bx, y: 0, z: 0, yaw: 0, anim: "idle" }); // inside the join grace window
      a.send("set_mode", { mode });
      await room.waitForNextPatch();
      const pa = () => room.state.players.get(a.sessionId)!;
      const pb = () => room.state.players.get(b.sessionId)!;
      assert.strictEqual(pb().x, bx);
      return { room, a, b, pa, pb };
    }
    type Arena = Awaited<ReturnType<typeof arena>>;

    /** A shot from (0, HEAD, 0) aimed at (tx, 0, 0). */
    const shot = (target_id?: string, extra: Record<string, unknown> = {}, tx = 5) => ({
      origin: [0, HEAD, 0],
      dir: [tx, -HEAD, 0],
      ...(target_id === undefined ? {} : { target_id }),
      ...extra,
    });

    /** Collect every payload of one broadcast type on one client. */
    function collect<T>(client: Arena["a"], type: string) {
      const got: T[] = [];
      client.onMessage(type, (m: T) => got.push(m));
      return got;
    }

    /** Fire once and assert the target took no damage. */
    async function noDamage(ctx: Arena, payload: object) {
      ctx.a.send("fire", payload);
      await ctx.room.waitForNextPatch();
      await sleep(50);
      assert.strictEqual(ctx.pb().hp, FULL_HP);
    }

    async function blastTimes(ctx: Arena, n: number) {
      for (let i = 0; i < n; i++) {
        ctx.a.send("fire", shot(ctx.b.sessionId));
        await sleep(BLAST_GAP);
      }
      await ctx.room.waitForNextPatch();
    }

    it("applies 1 blaster damage and broadcasts hit", async () => {
      const ctx = await arena();
      const hits = collect<{ shooter: string; target: string; hp: number }>(ctx.b, "hit");
      ctx.a.send("fire", shot(ctx.b.sessionId));
      await ctx.room.waitForNextPatch();
      await sleep(50);
      assert.strictEqual(ctx.pb().hp, FULL_HP - 1);
      assert.strictEqual(ctx.pb().anim, "hit");
      assert.deepStrictEqual(hits, [{ shooter: ctx.a.sessionId, target: ctx.b.sessionId, hp: FULL_HP - 1 }]);
    });

    it("eliminates after 3 blaster hits, scores, and respawns after RESPAWN_MS", async function () {
      this.timeout(RESPAWN_MS + 8000);
      const ctx = await arena();
      const elims = collect<{ target: string; by: string }>(ctx.a, "eliminated");
      await blastTimes(ctx, 3);
      assert.strictEqual(ctx.pb().hp, 0);
      assert.strictEqual(ctx.pb().alive, false);
      assert.strictEqual(ctx.pb().anim, "eliminated");
      assert.strictEqual(ctx.pa().score, 1);
      assert.deepStrictEqual(elims, [{ target: ctx.b.sessionId, by: ctx.a.sessionId }]);
      await sleep(RESPAWN_MS + 200);
      assert.strictEqual(ctx.pb().hp, FULL_HP);
      assert.strictEqual(ctx.pb().alive, true);
      assert.strictEqual(ctx.pb().anim, "idle");
      ctx.b.send("move", { x: 30, y: 0, z: 0, yaw: 0, anim: "idle" }); // respawn opens a teleport window
      await ctx.room.waitForNextPatch();
      assert.strictEqual(ctx.pb().x, 30);
    });

    it("eliminates after 2 noodle hits", async () => {
      const ctx = await arena(2);
      ctx.a.send("fire", shot(ctx.b.sessionId, { weapon: "noodle" }, 2));
      await sleep(NOODLE_GAP);
      assert.strictEqual(ctx.pb().hp, FULL_HP - 2);
      ctx.a.send("fire", shot(ctx.b.sessionId, { weapon: "noodle" }, 2));
      await ctx.room.waitForNextPatch();
      await sleep(50);
      assert.strictEqual(ctx.pb().alive, false);
      assert.strictEqual(ctx.pa().score, 1);
    });

    it("ignores fire in explore mode", async () => {
      const ctx = await arena(5, "explore");
      await noDamage(ctx, shot(ctx.b.sessionId));
    });

    it("rejects an origin more than 1.5 m from the shooter", async () => {
      const ctx = await arena();
      await noDamage(ctx, { ...shot(ctx.b.sessionId), origin: [0, 2.5, 0] });
    });

    it("rejects malformed payloads and unknown weapons", async () => {
      const ctx = await arena();
      await noDamage(ctx, { ...shot(ctx.b.sessionId), dir: [0, 0, 0] });
      await noDamage(ctx, { ...shot(ctx.b.sessionId), origin: [0, null, 0] });
      await noDamage(ctx, { ...shot(ctx.b.sessionId), dir: [1, 0] });
      await noDamage(ctx, shot(ctx.b.sessionId, { weapon: "bazooka" }));
    });

    it("rate-limits: a second shot within the interval does no damage", async () => {
      const ctx = await arena();
      ctx.a.send("fire", shot(ctx.b.sessionId));
      ctx.a.send("fire", shot(ctx.b.sessionId));
      await ctx.room.waitForNextPatch();
      await sleep(50);
      assert.strictEqual(ctx.pb().hp, FULL_HP - 1);
    });

    it("rejects a target in another zone", async () => {
      const ctx = await arena();
      ctx.b.send("enter_zone", { zone: "hou_236595179" });
      await ctx.room.waitForNextPatch();
      await noDamage(ctx, shot(ctx.b.sessionId));
    });

    it("rejects a blaster target beyond 40 m", async () => {
      const far = MAX_RANGE_M.blaster + 5;
      const ctx = await arena(far);
      await noDamage(ctx, shot(ctx.b.sessionId, {}, far));
    });

    it("rejects a noodle target beyond 2.9 m", async () => {
      const ctx = await arena(3.5);
      await noDamage(ctx, shot(ctx.b.sessionId, { weapon: "noodle" }, 3.5));
    });

    it("rejects a blaster ray pointing away from the target", async () => {
      const ctx = await arena();
      await noDamage(ctx, { ...shot(ctx.b.sessionId), dir: [-1, 0, 0] });
    });

    it("rejects self-targeting", async () => {
      const ctx = await arena();
      ctx.a.send("fire", shot(ctx.a.sessionId));
      await ctx.room.waitForNextPatch();
      await sleep(50);
      assert.strictEqual(ctx.pa().hp, FULL_HP);
    });

    it("does no damage to a dead target", async () => {
      const ctx = await arena();
      await blastTimes(ctx, 4);
      assert.strictEqual(ctx.pb().hp, 0);
      assert.strictEqual(ctx.pa().score, 1); // the fourth shot did not score again
    });

    it("sends fx for a miss to the other players but not the shooter", async () => {
      const ctx = await arena();
      const mine = collect(ctx.a, "fx");
      const theirs = collect<{ kind: string; origin: number[]; dir: number[] }>(ctx.b, "fx");
      ctx.a.send("fire", { origin: [0, HEAD, 0], dir: [0, 0, 2] });
      await ctx.room.waitForNextPatch();
      await sleep(100);
      assert.strictEqual(mine.length, 0);
      assert.deepStrictEqual(theirs, [{ kind: "blaster", origin: [0, HEAD, 0], dir: [0, 0, 1] }]);
    });

    it("set_mode explore revives everyone; back to foam resets scores", async () => {
      const ctx = await arena();
      await blastTimes(ctx, 3);
      assert.strictEqual(ctx.pb().alive, false);
      ctx.a.send("set_mode", { mode: "explore" });
      await ctx.room.waitForNextPatch();
      assert.strictEqual(ctx.pb().alive, true);
      assert.strictEqual(ctx.pb().hp, FULL_HP);
      assert.strictEqual(ctx.pb().anim, "idle");
      assert.strictEqual(ctx.pa().score, 1);
      ctx.a.send("set_mode", { mode: "foam" });
      await ctx.room.waitForNextPatch();
      assert.strictEqual(ctx.pa().score, 0);
    });
  });
});
