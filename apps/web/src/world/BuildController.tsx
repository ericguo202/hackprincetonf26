// Lane C: manual furnishing in the 3D scene ("build mode", buildState.ts).
// Mounted by HouseScene, so it only exists inside a house; leaving the house
// turns build mode off.
//
// The cursor stays free in build mode (no pointer lock: browsers refuse a
// re-lock for a while after Esc, which froze the camera). Every frame while
// build mode is on and the cursor is over the scene:
//   - the ray through the cursor meets the zone collision (walls, floors:
//     colliders that are ZONE and nothing else, so the player's own capsule
//     is never hit), the floor plane of the floor I stand on, and the placed
//     pieces (placement.pickSolid)
//   - holding a piece: placement.planGhost turns that point into a pose (wall
//     snap, on-surface, mount height) and a verdict; the ghost is moved
//     imperatively (no React render per frame) and tinted green or red
//   - holding nothing: the piece under the cursor gets an outline
// If the pointer is locked anyway, the ray goes through the screen centre.
//
// Input (only while build mode is on):
//   click        place the held piece (a catalog piece stays held for the next
//                one), or pick up the outlined piece to move it
//   drag         look around (CameraArm); WASD walks as usual
//   right click / Q / Esc  put the held piece back (Esc with nothing held: done)
//   R / Shift+R  rotate 45 degrees; mouse wheel rotates 15 degrees (the
//                camera's wheel zoom is off while holding)
//   X / Delete   remove the carried or the outlined piece
//   Alt (hold)   no wall snap
//   G            build mode on or off (also the HUD's Furnish button)
// A press counts as a click only if the pointer moved under DRAG_PX, so
// turning the camera never places anything. The blaster does not fire.
//
// Edits go out through net/game.ts sendFurnitureEdit (messages.md v2.6.0);
// the result comes back as furniture in the store like any agent plan.

import { Suspense, useEffect, useMemo, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { Line } from "@react-three/drei";
import { useRapier } from "@react-three/rapier";
import {
  BoxGeometry,
  Color,
  EdgesGeometry,
  type Group,
  type Material,
  type Mesh,
  Raycaster,
  Vector2,
  Vector3,
} from "three";

import type { Asset, Room } from "../contracts.gen";
import { sendFurnitureEdit } from "../net/game";
import { localPlayer } from "../player/playerControl";
import { CENTRE_ABOVE_FLOOR_M } from "../player/movement";
import { useStore } from "../store";
import { DRAG_PX, FINE_ROTATE_STEP_DEG, ROTATE_STEP_DEG, useBuild } from "./buildState";
import { CAMERA_QUERY, ZONE_COLLIDER } from "./collisionGroups";
import { pieceKey } from "./furnitureKey";
import { rotationY } from "./geometry";
import { LoadBoundary } from "./Glb";
import { usePackGLTF } from "./gltf";
import { useMeshState } from "./meshProbe";
import { floorBelow, pickSolid, planGhost, rayPlaneT, solidsOf, type Ghost, type V3 } from "./placement";
import { useStableFurniture } from "./useFurniturePlan";

export const BUILD_KEY = "KeyG";
/** Furthest the crosshair reaches for placing or picking, metres. */
const MAX_REACH_M = 12;
/** A zone hit this much before the floor plane is a wall (or a step), not the floor. */
const WALL_EPS_M = 0.06;
/** A wall hit higher than this above the floor (the ceiling, the top of a wall) places nothing. */
const MAX_AIM_HEIGHT_M = 2.1;
const OK_COLOR = new Color("#4ade80");
const BAD_COLOR = new Color("#f87171");
const HOVER_COLOR = "#fcd34d";

type Props = {
  listingId: string;
  rooms: Room[];
  catalog: Record<string, Asset>;
  /** Published root for asset files, or null (no meshes: boxes). */
  packBaseUrl: string | null;
};

type RapierRay = InstanceType<ReturnType<typeof useRapier>["rapier"]["Ray"]>;

function typing(): boolean {
  const el = document.activeElement;
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
}

const r3 = (v: number) => Math.round(v * 1000) / 1000;

function setCursor(el: HTMLElement, cursor: string): void {
  el.style.cursor = cursor;
}

export function BuildController({ listingId, rooms, catalog, packBaseUrl }: Props) {
  const canvas = useThree((s) => s.gl.domElement);
  const { world, rapier } = useRapier();
  const active = useBuild((s) => s.active);
  const held = useBuild((s) => s.held);
  const hover = useBuild((s) => s.hover);

  const house = useStore((s) => s.houses[listingId]);
  const { objects } = useStableFurniture(house);
  const objectsRef = useRef(objects);
  useEffect(() => {
    objectsRef.current = objects;
  }, [objects]);

  const ghostGroup = useRef<Group>(null);
  const lastGhost = useRef<Ghost | null>(null);
  const alt = useRef(false);
  const ray = useRef<RapierRay | null>(null);
  const scratch = useRef(new Vector3());
  const raycaster = useRef(new Raycaster());
  /** Cursor in normalized device coordinates, and whether it is over the scene (not a panel). */
  const cursor = useRef({ ndc: new Vector2(), over: false });

  const heldAsset = held ? catalog[held.asset_id] : undefined;
  // Everything the ghost can hit or stand on: every placed piece but the one I carry.
  const solids = useMemo(
    () => solidsOf(objects, catalog, held?.kind === "move" ? held.instance_id : null),
    [objects, catalog, held],
  );
  const solidsRef = useRef(solids);
  useEffect(() => {
    solidsRef.current = solids;
  }, [solids]);

  // Leaving the house (unmount) ends build mode.
  useEffect(() => () => useBuild.getState().setActive(false), []);

  // Build mode frees the cursor; mouse look comes back with a click once it is off.
  useEffect(() => {
    if (active && document.pointerLockElement) document.exitPointerLock();
  }, [active]);

  // The cursor says what a click does: grab a piece, place one, or nothing.
  useEffect(() => {
    setCursor(canvas, !active ? "" : hover ? "grab" : held ? "crosshair" : "default");
    return () => setCursor(canvas, "");
  }, [canvas, active, held, hover]);

  useEffect(() => {
    const build = useBuild.getState;

    const commit = () => {
      const s = build();
      if (s.held) {
        const g = lastGhost.current;
        if (!g || !g.valid || !g.room_id) return;
        const pose = { x: r3(g.x), y: r3(g.y), z: r3(g.z), rot_deg: Math.round(g.rot_deg * 10) / 10 };
        if (s.held.kind === "new") {
          sendFurnitureEdit({
            type: "furniture_place",
            payload: { listing_id: listingId, room_id: g.room_id, asset_id: s.held.asset_id, ...pose },
          });
          return; // keep holding it: the next click places another
        }
        const id = s.held.instance_id;
        const old = objectsRef.current.find((o) => o.instance_id === id);
        sendFurnitureEdit({
          type: "furniture_move",
          payload: { listing_id: listingId, room_id: g.room_id, instance_id: id, ...pose },
        });
        if (old) s.hideUntilMoved(old.instance_id, pieceKey(old));
        s.drop();
        return;
      }
      const piece = s.hover ? objectsRef.current.find((o) => o.instance_id === s.hover) : undefined;
      if (piece) s.pickUp(piece);
    };

    const remove = () => {
      const s = build();
      const id = s.held?.kind === "move" ? s.held.instance_id : s.held ? null : s.hover;
      if (!id) return;
      sendFurnitureEdit({ type: "furniture_remove", payload: { listing_id: listingId, instance_id: id } });
      if (s.held) s.drop();
      s.setHover(null);
    };

    // A click = press and release on the scene, moving under DRAG_PX (else it was a camera drag).
    let press: { x: number; y: number; button: number } | null = null;
    const onDown = (e: PointerEvent) => {
      press = build().active ? { x: e.clientX, y: e.clientY, button: e.button } : null;
    };
    const onUp = (e: PointerEvent) => {
      const p = press;
      press = null;
      if (!p || p.button !== e.button || !build().active) return;
      if (Math.hypot(e.clientX - p.x, e.clientY - p.y) >= DRAG_PX) return;
      if (e.button === 0) commit();
      else if (e.button === 2) build().drop(true);
    };
    const onPointerMove = (e: PointerEvent) => {
      const c = cursor.current;
      c.over = e.target === canvas;
      if (!c.over) return;
      const r = canvas.getBoundingClientRect();
      c.ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    };
    const onLeave = () => {
      cursor.current.over = false;
    };
    const onContext = (e: MouseEvent) => {
      if (build().active) e.preventDefault();
    };
    const onWheel = (e: WheelEvent) => {
      const s = build();
      if (!s.active || !s.held) return;
      e.preventDefault();
      s.rotate(e.deltaY > 0 ? -FINE_ROTATE_STEP_DEG : FINE_ROTATE_STEP_DEG);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Alt") {
        alt.current = true;
        e.preventDefault();
      }
      if (typing() || e.repeat) return;
      const s = build();
      if (e.code === BUILD_KEY) return s.toggle();
      if (!s.active) return;
      if (e.key === "Escape") {
        if (s.held) s.drop(true);
        else s.setActive(false);
      } else if (e.code === "KeyR" && s.held) s.rotate(e.shiftKey ? -ROTATE_STEP_DEG : ROTATE_STEP_DEG);
      else if (e.code === "KeyQ" && s.held) s.drop(true);
      else if (e.code === "KeyX" || e.code === "Delete" || e.code === "Backspace") remove();
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key === "Alt") alt.current = false;
    };
    const onBlur = () => {
      alt.current = false;
    };
    canvas.addEventListener("pointerdown", onDown);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("contextmenu", onContext);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      canvas.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("contextmenu", onContext);
      canvas.removeEventListener("wheel", onWheel);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
    };
  }, [canvas, listingId]);

  useFrame((state) => {
    const s = useBuild.getState();
    const g = ghostGroup.current;
    const locked = document.pointerLockElement === canvas;
    if (!s.active || (!locked && !cursor.current.over)) {
      if (g) g.visible = false;
      lastGhost.current = null;
      if (s.active) {
        s.setHover(null);
        s.setStatus(null);
      }
      return;
    }

    // The ray through the cursor (or the screen centre if the pointer is locked).
    const cam = state.camera;
    let origin: V3;
    let dir: V3;
    if (locked) {
      const d = cam.getWorldDirection(scratch.current);
      origin = [cam.position.x, cam.position.y, cam.position.z];
      dir = [d.x, d.y, d.z];
    } else {
      raycaster.current.setFromCamera(cursor.current.ndc, cam);
      const r = raycaster.current.ray;
      origin = [r.origin.x, r.origin.y, r.origin.z];
      dir = [r.direction.x, r.direction.y, r.direction.z];
    }

    // Zone collision only (exactly ZONE: the player's body is in every group).
    ray.current ??= new rapier.Ray({ x: 0, y: 0, z: 0 }, { x: 0, y: 0, z: 1 });
    ray.current.origin = { x: origin[0], y: origin[1], z: origin[2] };
    ray.current.dir = { x: dir[0], y: dir[1], z: dir[2] };
    const hit = world.castRay(
      ray.current,
      MAX_REACH_M,
      true,
      undefined,
      CAMERA_QUERY,
      undefined,
      undefined,
      (c) => c.collisionGroups() === ZONE_COLLIDER,
    );
    const tZone = hit ? hit.timeOfImpact : Infinity;

    const me = useStore.getState().me;
    const myRoom = rooms.find((r) => r.id === me.roomId);
    const feetY = (localPlayer()?.getPos()[1] ?? me.pos[1]) - CENTRE_ABOVE_FLOOR_M.standing;
    const floorY = myRoom?.floor_y ?? floorBelow(feetY, rooms);

    const asset = s.held ? catalog[s.held.asset_id] : undefined;
    if (!s.held || !asset) {
      if (g) g.visible = false;
      lastGhost.current = null;
      s.setStatus(null);
      s.setHover(pickSolid(origin, dir, solidsRef.current, Math.min(tZone, MAX_REACH_M))?.id ?? null);
      return;
    }
    s.setHover(null);

    // Where the crosshair lands: the first of floor plane, wall, or a placed piece.
    const tFloor = rayPlaneT(origin, dir, floorY);
    const tPiece = pickSolid(origin, dir, solidsRef.current, MAX_REACH_M)?.t ?? Infinity;
    let t = Math.min(tFloor ?? Infinity, tPiece, MAX_REACH_M);
    let tooHigh = false;
    if (tZone < t - WALL_EPS_M) {
      t = Math.max(0, tZone - 0.05); // a wall: just in front of it
      tooHigh = origin[1] + dir[1] * tZone > floorY + MAX_AIM_HEIGHT_M;
    }
    if (tooHigh || !Number.isFinite(t) || t >= MAX_REACH_M) {
      if (g) g.visible = false;
      lastGhost.current = null;
      s.setStatus(null);
      return;
    }
    const ghost = planGhost({
      point: [origin[0] + dir[0] * t, origin[2] + dir[2] * t],
      floorY,
      asset,
      rotDeg: s.rotDeg,
      rooms,
      solids: solidsRef.current,
      snap: !alt.current,
    });
    lastGhost.current = ghost;
    s.setStatus({ valid: ghost.valid, why: ghost.why, snapped: ghost.snapped });
    if (g) {
      g.visible = true;
      g.position.set(ghost.x, ghost.y, ghost.z);
      g.rotation.set(0, rotationY(ghost.rot_deg), 0);
    }
  });

  const hovered = !held && hover ? objects.find((o) => o.instance_id === hover) : undefined;
  const hoveredAsset = hovered ? catalog[hovered.asset_id] : undefined;
  return (
    <>
      {active && heldAsset ? (
        <group ref={ghostGroup} visible={false}>
          <GhostPiece
            key={heldAsset.id}
            asset={heldAsset}
            url={packBaseUrl === null ? null : `${packBaseUrl}/${heldAsset.file}`}
          />
        </group>
      ) : null}
      {active && hovered && hoveredAsset ? (
        <group position={[hovered.x, hovered.y, hovered.z]} rotation={[0, rotationY(hovered.rot_deg), 0]}>
          <BoxEdges size={hoveredAsset.size_m} color={HOVER_COLOR} />
        </group>
      ) : null}
    </>
  );
}

/** The held piece: its real mesh, see-through and tinted, over its footprint and a facing arrow. */
function GhostPiece({ asset, url }: { asset: Asset; url: string | null }) {
  const valid = useBuild((s) => s.status?.valid ?? true);
  const mesh = useMeshState(url, asset.file);
  const color = valid ? OK_COLOR : BAD_COLOR;
  const box = <GhostBox asset={asset} color={color} />;
  return (
    <>
      {url && mesh === "ok" ? (
        <LoadBoundary url={url} label={asset.file} fallback={box}>
          <Suspense fallback={box}>
            <GhostModel url={url} color={color} />
          </Suspense>
        </LoadBoundary>
      ) : (
        box
      )}
      <Footprint asset={asset} color={color} />
    </>
  );
}

type TintableMaterial = Material & { opacity: number; emissive?: Color; emissiveIntensity?: number };

function GhostModel({ url, color }: { url: string; color: Color }) {
  const { scene } = usePackGLTF(url);
  // Own clone and own materials: FurnitureLayer's pieces share the cached ones.
  const { model, materials } = useMemo(() => {
    const model = scene.clone(true);
    const materials: TintableMaterial[] = [];
    model.traverse((o) => {
      const m = o as Mesh;
      if (!m.isMesh) return;
      m.castShadow = false;
      m.receiveShadow = false;
      m.raycast = () => {};
      const ghost = (mat: Material) => {
        const c = mat.clone() as TintableMaterial;
        c.transparent = true;
        c.opacity = 0.55;
        c.depthWrite = false;
        materials.push(c);
        return c;
      };
      m.material = Array.isArray(m.material) ? m.material.map(ghost) : ghost(m.material);
    });
    return { model, materials };
  }, [scene]);
  useEffect(() => tint(materials, color), [materials, color]);
  useEffect(() => () => materials.forEach((m) => m.dispose()), [materials]);
  return <primitive object={model} />;
}

/** Standard (PBR) materials, which the pack's glTF files use, glow in the verdict colour; others only go see-through. */
function tint(materials: TintableMaterial[], color: Color): void {
  for (const m of materials) {
    if (!m.emissive) continue;
    m.emissive.copy(color);
    m.emissiveIntensity = 0.45;
  }
}

function GhostBox({ asset, color }: { asset: Asset; color: Color }) {
  const { w, d, h } = asset.size_m;
  return (
    <mesh position={[0, h / 2, 0]} raycast={() => {}}>
      <boxGeometry args={[w, h, d]} />
      <meshStandardMaterial color={color} transparent opacity={0.4} depthWrite={false} />
    </mesh>
  );
}

/** Footprint on the floor under the piece, and an arrow at its front (+Z). */
function Footprint({ asset, color }: { asset: Asset; color: Color }) {
  const { w, d } = asset.size_m;
  const y = 0.015;
  const pts = useMemo(
    () =>
      [
        [-w / 2, y, -d / 2],
        [w / 2, y, -d / 2],
        [w / 2, y, d / 2],
        [-w / 2, y, d / 2],
        [-w / 2, y, -d / 2],
      ] as [number, number, number][],
    [w, d],
  );
  const arrow = Math.min(0.35, Math.max(0.15, w * 0.2));
  return (
    <group>
      <mesh rotation-x={-Math.PI / 2} position-y={y - 0.005} raycast={() => {}}>
        <planeGeometry args={[w, d]} />
        <meshBasicMaterial color={color} transparent opacity={0.18} depthWrite={false} />
      </mesh>
      <Line points={pts} color={color} lineWidth={2.5} />
      <mesh rotation-x={-Math.PI / 2} position={[0, y, d / 2 + arrow * 0.6]} raycast={() => {}}>
        <circleGeometry args={[arrow, 3, -Math.PI / 2]} />
        <meshBasicMaterial color={color} transparent opacity={0.85} depthWrite={false} />
      </mesh>
    </group>
  );
}

function BoxEdges({ size, color }: { size: { w: number; d: number; h: number }; color: string }) {
  const h = Math.max(size.h, 0.05);
  const geom = useMemo(() => new EdgesGeometry(new BoxGeometry(size.w + 0.04, h + 0.04, size.d + 0.04)), [size.w, size.d, h]);
  useEffect(() => () => geom.dispose(), [geom]);
  return (
    <lineSegments geometry={geom} position-y={h / 2} raycast={() => {}}>
      <lineBasicMaterial color={color} depthTest={false} transparent opacity={0.95} />
    </lineSegments>
  );
}
