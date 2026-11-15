import { describe, expect, it } from "vitest";

import { furnitureKey, type HouseLike } from "./furnitureKey";

const house = (): HouseLike => ({
  plan_id: "p_01",
  objects: [
    { instance_id: "o1", asset_id: "sofa_abo_B072C8Q9RF", x: 3.0, y: 0, z: 0.49, rot_deg: 0 },
    { instance_id: "o2", asset_id: "tv_stand_abo_B07GFFPKBG", x: 3.0, y: 0, z: 3.99, rot_deg: 180 },
  ],
});

describe("furnitureKey", () => {
  const base = furnitureKey(house());

  it("same content in a new array gives the same key", () => {
    expect(furnitureKey(house())).toBe(base);
    expect(furnitureKey(JSON.parse(JSON.stringify(house())))).toBe(base); // what toJSON() hands back
  });

  it("reordered objects give the same key", () => {
    const h = house();
    h.objects.reverse();
    expect(furnitureKey(h)).toBe(base);
  });

  it("ignores float noise below 1 mm and 0.1 degree", () => {
    const h = house();
    h.objects[0].x += 0.0004;
    h.objects[1].rot_deg += 0.04;
    expect(furnitureKey(h)).toBe(base);
    const z = house();
    z.objects[0].y = -0.0001; // must not read "-0.000"
    expect(furnitureKey(z)).toBe(base);
  });

  it("a moved object changes the key", () => {
    const h = house();
    h.objects[0].x += 0.01;
    expect(furnitureKey(h)).not.toBe(base);
  });

  it("a rotated object changes the key", () => {
    const h = house();
    h.objects[0].rot_deg = 90;
    expect(furnitureKey(h)).not.toBe(base);
  });

  it("a swapped asset changes the key", () => {
    const h = house();
    h.objects[0].asset_id = "sectional_dev_01";
    expect(furnitureKey(h)).not.toBe(base);
  });

  it("an added or removed object changes the key", () => {
    const added = house();
    added.objects.push({ instance_id: "o3", asset_id: "plant_dev_01", x: 1, y: 0, z: 1, rot_deg: 0 });
    expect(furnitureKey(added)).not.toBe(base);
    const removed = house();
    removed.objects.pop();
    expect(furnitureKey(removed)).not.toBe(base);
  });

  it("the same plan_id with different objects gives a different key", () => {
    const refurnished = { plan_id: "p_01", objects: [{ instance_id: "o1", asset_id: "beanbag_dev_01", x: 1, y: 0, z: 1, rot_deg: 0 }] };
    expect(furnitureKey(refurnished)).not.toBe(base);
  });

  it("a different plan_id with the same objects gives a different key", () => {
    expect(furnitureKey({ ...house(), plan_id: "p_02" })).not.toBe(base);
  });

  it("no house is 'none'", () => {
    expect(furnitureKey(undefined)).toBe("none");
    expect(furnitureKey(null)).toBe("none");
  });
});
