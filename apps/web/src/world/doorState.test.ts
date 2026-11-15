import { beforeEach, describe, expect, it } from "vitest";

import { useStore } from "../store";
import type { OpenedDoor } from "./doorSplit";
import { doorsFor, useDoorState } from "./doorState";

const A = "hou_236595179";
const B = "hou_237901269";
const door = (id: string): OpenedDoor => ({ id, a: [2.337, -7.745], b: [3.086, -7.747], floorY: -0.003, width: 0.75 });
const R3_R9 = door(`${A}.r3->${A}.r9`);
const B_DOOR = door(`${B}.r1->${B}.r2`);
const SRC = { collision: "glb" as const, wallBoxes: 64 };

function resetAll() {
  useDoorState.setState({ openDoors: {}, byListing: {}, sources: {} });
  useStore.getState().setMe({ zone: "block", pos: [0, 0, 0], roomId: null });
}

describe("doorState: doors survive zone writes", () => {
  beforeEach(resetAll);

  it("a zone write after HouseDoors registered does not wipe the doors (start-room race)", () => {
    // HouseDoors' effect can run before the start-room setMe moves the store
    // from "block" into the house. The old zone subscription reset here.
    useDoorState.getState().setDoors(A, [R3_R9], SRC);
    useStore.getState().setMe({ zone: A, pos: [2.7, 0, -7.0], roomId: `${A}.r3` });
    expect(doorsFor(useDoorState.getState(), A)).toEqual([R3_R9]);
  });

  it("zone flapping (block and back, a respawn write) keeps doors and open flags", () => {
    useStore.getState().setMe({ zone: A });
    useDoorState.getState().setDoors(A, [R3_R9], SRC);
    useDoorState.getState().toggle(R3_R9.id);
    useStore.getState().setMe({ zone: "block" });
    useStore.getState().setMe({ zone: A, pos: [1, 0, -5] });
    expect(doorsFor(useDoorState.getState(), A)).toEqual([R3_R9]);
    expect(useDoorState.getState().isOpen(R3_R9.id)).toBe(true);
  });

  it("doorsFor returns only the doors of the zone's house, none on the block", () => {
    useDoorState.getState().setDoors(A, [R3_R9], SRC);
    useDoorState.getState().setDoors(B, [B_DOOR], SRC);
    expect(doorsFor(useDoorState.getState(), A)).toEqual([R3_R9]);
    expect(doorsFor(useDoorState.getState(), B)).toEqual([B_DOOR]);
    expect(doorsFor(useDoorState.getState(), "block")).toEqual([]);
    expect(doorsFor(useDoorState.getState(), "hou_unknown")).toEqual([]);
  });

  it("clearDoors forgets one listing's doors and open flags, not another's", () => {
    const s = useDoorState.getState();
    s.setDoors(A, [R3_R9], SRC);
    s.setDoors(B, [B_DOOR], SRC);
    s.toggle(R3_R9.id);
    s.toggle(B_DOOR.id);
    useDoorState.getState().clearDoors(A);
    const after = useDoorState.getState();
    expect(doorsFor(after, A)).toEqual([]);
    expect(after.sources[A]).toBeUndefined();
    expect(after.isOpen(R3_R9.id)).toBe(false);
    expect(doorsFor(after, B)).toEqual([B_DOOR]);
    expect(after.isOpen(B_DOOR.id)).toBe(true);
  });

  it("a new split for the same house replaces its doors and keeps open flags", () => {
    const s = useDoorState.getState();
    s.setDoors(A, [R3_R9], SRC);
    s.toggle(R3_R9.id);
    useDoorState.getState().setDoors(A, [{ ...R3_R9 }], SRC);
    expect(doorsFor(useDoorState.getState(), A)).toHaveLength(1);
    expect(useDoorState.getState().isOpen(R3_R9.id)).toBe(true);
  });
});
