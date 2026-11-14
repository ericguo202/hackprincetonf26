// Lane C: a text label pinned to a point in the world, at a fixed screen size
// (no distanceFactor, so it never grows to fill the screen up close). Hidden
// when the camera is very close, faded out when far, so it is readable at
// every range it shows at. Opacity is set on the element every frame, with no
// React re-render.

import { useRef, type CSSProperties, type ReactNode } from "react";
import { useFrame } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import { Vector3, type Group } from "three";

import { labelOpacity, type LabelRange } from "./labelFade";

type Props = {
  position: [number, number, number];
  children: ReactNode;
  style?: CSSProperties;
  range?: Partial<LabelRange>;
};

export function WorldLabel({ position, children, style, range }: Props) {
  const anchor = useRef<Group>(null);
  const el = useRef<HTMLDivElement>(null);
  const here = useRef(new Vector3());

  useFrame((state) => {
    if (!anchor.current || !el.current) return;
    const d = anchor.current.getWorldPosition(here.current).distanceTo(state.camera.position);
    const a = labelOpacity(d, range);
    el.current.style.opacity = a.toFixed(3);
    el.current.style.visibility = a > 0.01 ? "visible" : "hidden";
  });

  return (
    <group ref={anchor} position={position}>
      <Html center zIndexRange={[10, 0]}>
        <div ref={el} style={{ ...base, ...style, opacity: 0, visibility: "hidden" }}>
          {children}
        </div>
      </Html>
    </group>
  );
}

const base: CSSProperties = {
  whiteSpace: "nowrap",
  pointerEvents: "none",
  userSelect: "none",
  transition: "none",
};
