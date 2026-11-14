// Lane C: load a pack .glb, or show a placeholder when there is none.
//
// Two kinds, kept apart on purpose (AGENTS.md: collide against collision.glb,
// never the render mesh):
//   render    -> the scene is drawn, no colliders
//   collision -> the scene is never drawn; its triangles become one fixed trimesh
//
// A missing file (404, or Vite's dev server answering with index.html) is
// caught by a HEAD probe before the loader ever sees it (meshProbe.ts): one
// warning per URL, the placeholder, no thrown error. The placeholder also
// covers the probe and loading moments, so the player always has a floor.
// LoadBoundary remains for a file that exists but fails to parse.

import { Component, Suspense, useEffect, useMemo, type ReactNode } from "react";
import { RigidBody, TrimeshCollider } from "@react-three/rapier";

import { extractTrimesh } from "./collision";
import { ZONE_COLLIDER } from "./collisionGroups";
import { clearPackGLTF, usePackGLTF } from "./gltf";
import { useMeshState } from "./meshProbe";
import { warnOnce } from "./pack";

type BoundaryProps = {
  /** Identifies the failure in the warning; also resets the boundary when it changes. */
  url: string;
  label: string;
  fallback: ReactNode;
  children: ReactNode;
};

/** Error boundary for anything that loads: logs once per URL, then shows `fallback`. */
export class LoadBoundary extends Component<BoundaryProps, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(err: Error) {
    warnOnce(
      `glb:${this.props.url}`,
      `[pack] could not load ${this.props.label} from ${this.props.url} (${err.message}); rendering placeholder geometry`,
    );
  }

  componentDidUpdate(prev: BoundaryProps) {
    if (prev.url !== this.props.url && this.state.failed) this.setState({ failed: false });
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

type Props = {
  kind: "render" | "collision";
  /** Absolute URL, or null when the pack has no base URL (fixture mode). */
  url: string | null;
  /** Pack-relative name for the warning, e.g. "block/render.glb". */
  label: string;
  placeholder: ReactNode;
};

export function GlbOrPlaceholder({ kind, url, label, placeholder }: Props) {
  // Probe first (meshProbe.ts): a missing file never reaches the loader, so it
  // never throws. The boundary below is only for files that exist but are bad.
  const mesh = useMeshState(url, label);
  if (!url || mesh !== "ok") return <>{placeholder}</>;
  return (
    <LoadBoundary url={url} label={label} fallback={placeholder}>
      <Suspense fallback={placeholder}>
        {kind === "render" ? (
          <RenderGlb url={url} />
        ) : (
          <CollisionGlb url={url} fallback={placeholder} />
        )}
      </Suspense>
    </LoadBoundary>
  );
}

/** Drop the cached scene when the zone unloads, so a block's meshes leave memory indoors. */
function useUnloadOnUnmount(url: string) {
  useEffect(() => () => clearPackGLTF(url), [url]);
}

function RenderGlb({ url }: { url: string }) {
  const { scene } = usePackGLTF(url);
  useUnloadOnUnmount(url);
  return <primitive object={scene} />;
}

function CollisionGlb({ url, fallback }: { url: string; fallback: ReactNode }) {
  const { scene } = usePackGLTF(url);
  const mesh = useMemo(() => extractTrimesh(scene), [scene]);
  useUnloadOnUnmount(url);
  if (mesh.indices.length === 0) {
    warnOnce(`empty:${url}`, `[pack] ${url} has no triangles; using placeholder collision`);
    return <>{fallback}</>;
  }
  return (
    <RigidBody type="fixed" colliders={false}>
      <TrimeshCollider collisionGroups={ZONE_COLLIDER} args={[mesh.vertices, mesh.indices]} />
    </RigidBody>
  );
}
