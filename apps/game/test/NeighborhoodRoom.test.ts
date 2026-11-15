import assert from "node:assert";
import { boot, type ColyseusTestServer } from "@colyseus/testing";
import appConfig from "../src/app.config.ts";
import {
  FURNITURE_EDIT_INTERVAL_MS,
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

  describe("per-room furniture", () => {
    const HOUSE = "hou_236595179";
    const obj = (instance_id: string, asset_id: string) => ({ instance_id, asset_id, x: 1, y: 0, z: 1, rot_deg: 0, reason: "t" });

    /** POST one room's plan; resolves with the HTTP status (httpie throws on non-2xx). */
    async function furnish(roomId: string, objects: ReturnType<typeof obj>[], planId = `p_${roomId}`, listing = HOUSE) {
      const plan = { plan_id: planId, listing_id: listing, room_id: roomId, status: "placed", objects, conflicts: [] };
      try {
        const res = await colyseus.http.post(`/internal/rooms/${BLOCK}/houses/${listing}/plan`, {
          headers: { "Content-Type": "application/json", "X-Internal-Secret": SECRET },
          body: { player_id: "agent", plan },
        });
        return res.statusCode;
      } catch (e) {
        return (e as { statusCode: number }).statusCode;
      }
    }

    async function setup() {
      const room = await colyseus.createRoom("neighborhood", { block_id: BLOCK });
      await colyseus.connectTo(room, { name: "ana" });
      const ids = () => room.state.houses.get(HOUSE)?.objects.map((o) => o.instance_id) ?? [];
      return { room, ids };
    }

    it("keeps the first room when a second room is furnished", async () => {
      const { room, ids } = await setup();
      assert.strictEqual(await furnish(`${HOUSE}.r1`, [obj("o1", "sofa"), obj("o2", "tv")]), 200);
      assert.strictEqual(await furnish(`${HOUSE}.r2`, [obj("o1", "crib")], "p_nursery"), 200);
      assert.deepStrictEqual(ids(), [`${HOUSE}.r1/o1`, `${HOUSE}.r1/o2`, `${HOUSE}.r2/o1`]);
      assert.strictEqual(room.state.houses.get(HOUSE)?.plan_id, "p_nursery");
    });

    it("re-furnishing a room replaces only that room and keeps the order of rooms", async () => {
      const { room, ids } = await setup();
      await furnish(`${HOUSE}.r1`, [obj("o1", "sofa")]);
      await furnish(`${HOUSE}.r2`, [obj("o1", "crib")]);
      await furnish(`${HOUSE}.r1`, [obj("o1", "beanbag"), obj("o2", "play_mat")], "p_playroom");
      assert.deepStrictEqual(ids(), [`${HOUSE}.r1/o1`, `${HOUSE}.r1/o2`, `${HOUSE}.r2/o1`]);
      const assets = room.state.houses.get(HOUSE)?.objects.map((o) => o.asset_id);
      assert.deepStrictEqual(assets, ["beanbag", "play_mat", "crib"]);
    });

    it("an empty plan clears that room only", async () => {
      const { ids } = await setup();
      await furnish(`${HOUSE}.r1`, [obj("o1", "sofa")]);
      await furnish(`${HOUSE}.r2`, [obj("o1", "crib")]);
      await furnish(`${HOUSE}.r1`, []);
      assert.deepStrictEqual(ids(), [`${HOUSE}.r2/o1`]);
    });

    it("does not double-prefix an already prefixed instance_id", async () => {
      const { ids } = await setup();
      await furnish(`${HOUSE}.r1`, [obj(`${HOUSE}.r1/o1`, "sofa")]);
      assert.deepStrictEqual(ids(), [`${HOUSE}.r1/o1`]);
    });

    it("rejects a room of another house or a missing room_id", async () => {
      const { ids } = await setup();
      assert.strictEqual(await furnish("hou_999.r1", [obj("o1", "sofa")]), 400);
      assert.strictEqual(await furnish("", [obj("o1", "sofa")]), 400);
      assert.deepStrictEqual(ids(), []);
    });
  });

  describe("manual furnishing", () => {
    const HOUSE = "hou_236595179";
    const R1 = `${HOUSE}.r1`;
    const R2 = `${HOUSE}.r2`;
    const GAP = FURNITURE_EDIT_INTERVAL_MS + 30;
    const place = (over: Record<string, unknown> = {}) => ({
      listing_id: HOUSE,
      room_id: R1,
      asset_id: "sofa_abo_B072C8Q9RF",
      x: 1,
      y: 0,
      z: 2,
      rot_deg: 90,
      ...over,
    });

    /** One player inside HOUSE (zone set), and helpers over that house's pieces. */
    async function inside() {
      const room = await colyseus.createRoom("neighborhood", { block_id: BLOCK });
      const client = await colyseus.connectTo(room, { name: "ana" });
      client.send("enter_zone", { zone: HOUSE });
      await room.waitForNextPatch();
      const objects = () => room.state.houses.get(HOUSE)?.objects.map((o) => ({ ...o.toJSON() })) ?? [];
      const toasts: string[] = [];
      client.onMessage("toast", (m: { text: string }) => toasts.push(m.text));
      const send = async (type: "furniture_place" | "furniture_move" | "furniture_remove", payload: Record<string, unknown>) => {
        client.send(type, payload);
        await room.waitForNextPatch();
        await sleep(GAP);
      };
      return { room, client, objects, toasts, send };
    }

    it("places a piece with a game-made id in the room, by the sender", async () => {
      const { client, objects, send, room } = await inside();
      await send("furniture_place", place({ rot_deg: -90 }));
      const [o] = objects();
      assert.match(o.instance_id, new RegExp(`^${R1.replace(".", "\\.")}/m\\d+$`));
      assert.strictEqual(o.asset_id, "sofa_abo_B072C8Q9RF");
      assert.strictEqual(o.rot_deg, 270);
      assert.strictEqual(o.by_player, client.sessionId);
      assert.strictEqual(o.reason, "");
      assert.strictEqual(room.state.houses.get(HOUSE)?.plan_id, "manual");
    });

    it("moves and rotates an agent-placed piece in place, clearing its reason", async () => {
      const { objects, send, room } = await inside();
      room.applyPlan(HOUSE, R1, "p_agent", [{ instance_id: "o1", asset_id: "tv", x: 0, y: 0, z: 0, rot_deg: 0, reason: "faces couch" }], "agent");
      await send("furniture_move", { listing_id: HOUSE, instance_id: `${R1}/o1`, room_id: R1, x: 2, y: 0, z: 3, rot_deg: 45 });
      const [o] = objects();
      assert.deepStrictEqual([o.instance_id, o.x, o.z, o.rot_deg, o.reason], [`${R1}/o1`, 2, 3, 45, ""]);
      assert.strictEqual(room.state.houses.get(HOUSE)?.plan_id, "p_agent");
    });

    it("re-files a piece moved into another room under that room", async () => {
      const { objects, send } = await inside();
      await send("furniture_place", place());
      const id = objects()[0].instance_id;
      await send("furniture_move", { listing_id: HOUSE, instance_id: id, room_id: R2, x: 5, y: 0, z: 5, rot_deg: 0 });
      const all = objects();
      assert.strictEqual(all.length, 1);
      assert.ok(all[0].instance_id.startsWith(`${R2}/m`));
    });

    it("removes a piece; an agent re-furnish of the room replaces hand-placed pieces", async () => {
      const { objects, send, room } = await inside();
      await send("furniture_place", place());
      await send("furniture_place", place({ room_id: R2 }));
      await send("furniture_remove", { listing_id: HOUSE, instance_id: objects()[1].instance_id });
      assert.strictEqual(objects().length, 1);
      room.applyPlan(HOUSE, R1, "p_play", [{ instance_id: "o1", asset_id: "beanbag", x: 0, y: 0, z: 0, rot_deg: 0, reason: "" }], "agent");
      assert.deepStrictEqual(objects().map((o) => o.instance_id), [`${R1}/o1`]);
    });

    it("rejects edits from outside the house, bad rooms, bad poses and bad asset ids, with a toast", async () => {
      const { objects, client, send, toasts, room } = await inside();
      await send("furniture_place", place({ listing_id: "hou_237901269", room_id: "hou_237901269.r1" }));
      await send("furniture_place", place({ room_id: "hou_999.r1" }));
      await send("furniture_place", place({ x: Number.NaN }));
      await send("furniture_place", place({ x: 500 }));
      await send("furniture_place", place({ asset_id: "../../etc" }));
      await send("furniture_move", { listing_id: HOUSE, instance_id: "nope", room_id: R1, x: 0, y: 0, z: 0, rot_deg: 0 });
      assert.deepStrictEqual(objects(), []);
      assert.strictEqual(toasts.length, 6);
      client.send("enter_zone", { zone: "block" });
      await room.waitForNextPatch();
      await send("furniture_place", place());
      assert.deepStrictEqual(objects(), []);
    });

    it("rate-limits edits to one per FURNITURE_EDIT_INTERVAL_MS", async () => {
      const { objects, client, room } = await inside();
      client.send("furniture_place", place());
      client.send("furniture_place", place({ x: 3 }));
      await room.waitForNextPatch();
      await sleep(50);
      assert.strictEqual(objects().length, 1);
    });
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
