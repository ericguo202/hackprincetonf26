import { describe, expect, it } from "vitest";
import * as THREE from "three";

import { SCREEN_MATERIAL, findScreen, showVideo } from "./tvVideo";

function tv() {
  const named = (name: string) => Object.assign(new THREE.MeshStandardMaterial(), { name });
  const root = new THREE.Group();
  const frame = new THREE.Mesh(new THREE.BoxGeometry(), named("tv_frame"));
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(), named(SCREEN_MATERIAL));
  root.add(frame);
  frame.add(screen); // nested, like a glTF node tree
  return { root, screen };
}

describe("TV screen (furniture v3 team TVs)", () => {
  it("finds the mesh whose material is tv_screen, at any depth", () => {
    const { root, screen } = tv();
    expect(findScreen(root)).toBe(screen);
  });

  it("finds nothing on furniture without a screen", () => {
    const sofa = new THREE.Group().add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial()));
    expect(findScreen(sofa)).toBeUndefined();
  });

  it("shows the video unlit and right side up, then restores the off glass", () => {
    const { screen } = tv();
    const off = screen.material;
    const tex = new THREE.VideoTexture(document.createElement("video"));
    const undo = showVideo(screen, tex);
    const on = screen.material as THREE.Material as THREE.MeshBasicMaterial;
    expect(on).toBeInstanceOf(THREE.MeshBasicMaterial);
    expect(on.map).toBe(tex);
    expect(on.toneMapped).toBe(false);
    expect(tex.flipY).toBe(false);
    undo();
    expect(screen.material).toBe(off);
  });
});
