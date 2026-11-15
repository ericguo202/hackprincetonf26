import { describe, expect, it } from "vitest";
import {
  BoxGeometry,
  Group,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  PointLight,
  type Material,
} from "three";

import { disableToneMapping } from "./interiorMaterials";

/** An interior.glb-like scene: unlit photo materials, one multi-material mesh, a masked wall. */
function interiorScene() {
  const root = new Group();
  const floor = new Mesh(new BoxGeometry(), new MeshBasicMaterial()); // KHR_materials_unlit -> MeshBasicMaterial
  const wall = new MeshBasicMaterial({ alphaTest: 0.5 }); // doorway cut-outs (alphaMode MASK)
  const walls = new Mesh(new BoxGeometry(), wall);
  const fixtures = new Mesh(new BoxGeometry(), [new MeshBasicMaterial(), new MeshBasicMaterial()]);
  const nested = new Group();
  nested.add(fixtures);
  root.add(floor, walls, nested, new PointLight());
  return { root, wall, materials: [floor.material, wall, ...fixtures.material] as Material[] };
}

/** Everything that is not interior.glb: furniture, character, effects. */
function otherScene() {
  const root = new Group();
  const couch = new MeshStandardMaterial();
  const character = new MeshStandardMaterial();
  const tracer = new MeshBasicMaterial({ color: "#ff8a1f" });
  root.add(new Mesh(new BoxGeometry(), couch), new Mesh(new BoxGeometry(), character), new Mesh(new BoxGeometry(), tracer));
  return { root, materials: [couch, character, tracer] };
}

describe("interior.glb materials: tone mapping off", () => {
  it("turns toneMapped off on every interior mesh material, including multi-material and nested meshes", () => {
    const interior = interiorScene();
    expect(interior.materials.every((m) => m.toneMapped)).toBe(true);
    const versions = interior.materials.map((m) => m.version);
    expect(disableToneMapping(interior.root)).toBe(4);
    expect(interior.materials.every((m) => m.toneMapped === false)).toBe(true);
    // needsUpdate bumped each material's version, so the shader recompiles without tone mapping.
    interior.materials.forEach((m, i) => expect(m.version).toBeGreaterThan(versions[i]));
  });

  it("leaves everything else alone: furniture, characters and effects keep tone mapping", () => {
    const interior = interiorScene();
    const other = otherScene();
    disableToneMapping(interior.root);
    expect(other.materials.every((m) => m.toneMapped === true)).toBe(true);
  });

  it("keeps the wall's alpha test (doorways stay cut out)", () => {
    const interior = interiorScene();
    disableToneMapping(interior.root);
    expect(interior.wall.alphaTest).toBe(0.5);
  });

  it("is a one-time pass: running it again changes nothing", () => {
    const interior = interiorScene();
    disableToneMapping(interior.root);
    const versions = interior.materials.map((m) => m.version);
    expect(disableToneMapping(interior.root)).toBe(0);
    interior.materials.forEach((m, i) => expect(m.version).toBe(versions[i]));
  });
});
