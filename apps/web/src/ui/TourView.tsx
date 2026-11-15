// "View in Matterport": the house the player is in, as its real Matterport tour,
// with this house's current furniture (store.houses[zone]) placed in it.
//
// Runs the self-hosted Showcase bundle (Bundle SDK) in an iframe and uses its
// Scene API to load each piece. Local only: nothing is sent to the game, and a
// new plan from the agent or another player re-places the furniture live.
//
// Local setup: apps/web/scripts/setup-matterport.sh (bundle + meshopt-free
// furniture copies into public/matterport/) and VITE_MATTERPORT_SDK_KEY in the
// repo-root .env. The key's allow list must include the page's domain.

import { useEffect, useMemo, useRef, useState } from "react";

import type { Asset, Room } from "../contracts.gen";
import { useStore } from "../store";
import type { LoadedPack } from "../world/pack";
import { useStableFurniture } from "../world/useFurniturePlan";
import { stopLooking } from "./pointerLock";
import { furnishedRoom, furnitureCentre, showcaseUrl, tourFurnitureUrl, tourRoot, viewpointSweep } from "./tourPlan";
import { sweepsOf, useTour, type SceneObject, type ShowcaseSdk, type ShowcaseWindow } from "./tourSdk";

const SDK_KEY = import.meta.env.VITE_MATTERPORT_SDK_KEY ?? "";
const ROOT = tourRoot(import.meta.env.BASE_URL);

export function TourView({ pack }: { pack: LoadedPack }) {
  const open = useTour((s) => s.open);
  const zone = useStore((s) => s.me.zone);
  const setOpen = useTour((s) => s.setOpen);
  // Leaving the house closes it.
  useEffect(() => {
    if (zone === "block") setOpen(false);
  }, [zone, setOpen]);
  if (!open || zone === "block") return null;
  return <TourOverlay pack={pack} listingId={zone} onClose={() => setOpen(false)} />;
}

type Status = { kind: "loading" | "ready" | "error"; text: string };

/** Fly to the sweep in the furnished room that sees the whole layout, and look at it. */
async function frameFurniture(s: ShowcaseSdk, objects: { asset_id: string; x: number; y: number; z: number }[], rooms: Room[]) {
  const centre = furnitureCentre(objects);
  if (!centre) return;
  const roomId = furnishedRoom(objects, rooms);
  const sweep = roomId ? viewpointSweep(await sweepsOf(s), rooms, roomId, centre) : null;
  if (sweep) await s.Sweep.moveTo(sweep, { transition: s.Sweep.Transition.FLY, transitionTime: 800 });
  await s.Camera.lookAt(centre, sweep ? { transitionTime: 600 } : { moveToClosestSweep: true, transitionTime: 600 });
}

function TourOverlay({ pack, listingId, onClose }: { pack: LoadedPack; listingId: string; onClose: () => void }) {
  const listing = pack.listings.find((l) => l.id === listingId);
  const modelId = listing?.source.matterport_model_id ?? null;
  const rooms = useMemo(() => pack.roomsByHouse[listingId] ?? [], [pack, listingId]);
  const house = useStore((s) => s.houses[listingId]);
  const { key, objects } = useStableFurniture(house);

  const iframe = useRef<HTMLIFrameElement>(null);
  const [sdk, setSdk] = useState<ShowcaseSdk | null>(null);
  const [bundleOk, setBundleOk] = useState<boolean | null>(null);
  const [status, setStatus] = useState<Status>({ kind: "loading", text: "Loading the Matterport tour…" });

  // Free the cursor; Esc closes (when focus is on the page, not inside the tour).
  useEffect(() => {
    stopLooking();
    const onKey = (e: KeyboardEvent) => {
      if (e.code === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Is the bundle installed? Vite's dev server answers a missing file with index.html.
  useEffect(() => {
    let live = true;
    fetch(`${ROOT}bundle/version.txt`)
      .then((r) => live && setBundleOk(r.ok && !(r.headers.get("content-type") ?? "").includes("text/html")))
      .catch(() => live && setBundleOk(false));
    return () => {
      live = false;
    };
  }, []);

  const problem = !modelId
    ? "This house has no Matterport model."
    : !SDK_KEY
      ? "VITE_MATTERPORT_SDK_KEY is not set in the repo-root .env."
      : bundleOk === false
        ? "The Showcase bundle is not installed: run apps/web/scripts/setup-matterport.sh."
        : null;

  const onLoad = async () => {
    const w = iframe.current?.contentWindow as ShowcaseWindow | null;
    if (!w?.MP_SDK) {
      setStatus({ kind: "error", text: "The tour loaded without the SDK (is the bundle installed?)." });
      return;
    }
    try {
      const s = await w.MP_SDK.connect(w);
      await s.App.state.waitUntil((st) => st.phase === s.App.Phase.PLAYING);
      setSdk(s);
    } catch (e) {
      setStatus({ kind: "error", text: `Matterport SDK: ${e instanceof Error ? e.message : String(e)}` });
    }
  };

  // Place this house's furniture; again whenever it changes. Then view it.
  useEffect(() => {
    if (!sdk) return;
    let cancelled = false;
    let placed: SceneObject | null = null;
    (async () => {
      const [obj] = await sdk.Scene.createObjects(1);
      if (cancelled) return;
      obj.addNode().addComponent("mp.lights");
      let missing = 0;
      for (const o of objects) {
        const asset: Asset | undefined = pack.catalog[o.asset_id];
        if (!asset) {
          missing++;
          continue;
        }
        const node = obj.addNode();
        node.addComponent(sdk.Scene.Component.GLTF_LOADER, { url: tourFurnitureUrl(ROOT, asset.file) });
        node.position.set(o.x, o.y, o.z);
        // Same convention as world/FurnitureLayer: rotation about +Y, counterclockwise from above.
        node.quaternion.setFromAxisAngle({ x: 0, y: 1, z: 0 }, (o.rot_deg * Math.PI) / 180);
      }
      obj.start();
      placed = obj;
      const n = objects.length - missing;
      setStatus({
        kind: "ready",
        text: n === 0 ? "No furniture in this house yet." : `${n} piece${n === 1 ? "" : "s"} placed in the real tour.`,
      });
      await frameFurniture(sdk, objects, rooms);
    })().catch((e) => {
      if (!cancelled) setStatus({ kind: "error", text: `Placing furniture: ${e instanceof Error ? e.message : String(e)}` });
    });
    return () => {
      cancelled = true;
      placed?.stop();
    };
    // Keyed on the furniture's content (key), not on array identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sdk, key]);

  return (
    <div className="tour-overlay">
      <div className="tour-bar">
        <strong>Matterport tour</strong>
        <span>{listing?.address ?? listingId}</span>
        <span className={`tour-status ${status.kind}`}>{problem ?? status.text}</span>
        <button type="button" onClick={onClose}>
          Back to game (Esc)
        </button>
      </div>
      {modelId && SDK_KEY && bundleOk ? (
        <iframe
          ref={iframe}
          className="tour-frame"
          title="Matterport tour"
          src={showcaseUrl(ROOT, modelId, SDK_KEY)}
          allow="xr-spatial-tracking; fullscreen"
          onLoad={onLoad}
        />
      ) : null}
    </div>
  );
}
