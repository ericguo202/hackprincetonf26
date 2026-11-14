import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { ANIM_STATES_FOR_TEST } from "./animStateList";
import { CLIPS, CROSSFADE_S, MOVING_SHOOT, ONE_SHOT, PACK_CLIPS, clipFor } from "./clips";

const MODEL = join(dirname(fileURLToPath(import.meta.url)), "../../public/characters/Character_Soldier.gltf");
const fileClips = (JSON.parse(readFileSync(MODEL, "utf8")).animations as { name: string }[]).map((a) => a.name);

describe("clips.ts matches the shipped model", () => {
  it("PACK_CLIPS is exactly the model's animations[].name, in order", () => {
    expect([...PACK_CLIPS]).toEqual(fileClips);
  });

  it("maps every wire state to a clip that exists", () => {
    for (const s of ANIM_STATES_FOR_TEST) expect(fileClips, s).toContain(CLIPS[s]);
    expect(fileClips).toContain(MOVING_SHOOT);
    for (const [clip, v] of Object.entries(ONE_SHOT)) {
      expect(fileClips).toContain(clip);
      if (v?.then) expect(fileClips).toContain(v.then);
    }
  });

  it("runs with the gun raised and uses no strafe clips", () => {
    expect(CLIPS.run).toBe("Run_Gun");
    expect(Object.values(CLIPS).some((c) => /strafe/i.test(c))).toBe(false);
  });

  it("crossfades over 0.2 s", () => {
    expect(CROSSFADE_S).toBe(0.2);
  });
});

describe("clipFor", () => {
  it("loops locomotion", () => {
    expect(clipFor("idle")).toEqual({ clip: "Idle", once: false });
    expect(clipFor("run")).toEqual({ clip: "Run_Gun", once: false });
  });

  it("jumps once, then hangs in the air loop", () => {
    expect(clipFor("jump")).toEqual({ clip: "Jump", once: true, then: "Jump_Idle" });
  });

  it("hit and eliminated play once and hold", () => {
    expect(clipFor("hit")).toEqual({ clip: "HitReact", once: true });
    expect(clipFor("eliminated")).toEqual({ clip: "Death", once: true });
  });

  it("shoots standing, or running when moving", () => {
    expect(clipFor("shoot").clip).toBe("Idle_Shoot");
    expect(clipFor("shoot", true).clip).toBe("Run_Shoot");
    // moving only changes shoot
    expect(clipFor("run", true).clip).toBe("Run_Gun");
  });
});
