// Lane C: the block's sky and clouds. Mounted next to BlockScene only (World
// Zones), never inside a house: v6 houses bring their own backdrop dome.

import { Suspense } from "react";
import { Cloud, Clouds, Sky } from "@react-three/drei";
import { MeshBasicMaterial } from "three";

import { BLOCK_CLOUDS, CLOUD_OPACITY, CLOUD_SPEED, cloudPuffTexture } from "./blockClouds";
import { BLOCK_SKY } from "./sun";

export function BlockSky() {
  const texture = cloudPuffTexture();
  return (
    <>
      <Sky {...BLOCK_SKY} />
      {texture ? (
        <Suspense fallback={null}>
          {/* Unlit white puffs: they read as clouds from every side, whatever the sun angle. */}
          <Clouds texture={texture} material={MeshBasicMaterial} limit={BLOCK_CLOUDS.length * BLOCK_CLOUDS[0].segments}>
            {BLOCK_CLOUDS.map((c) => (
              <Cloud
                key={c.seed}
                seed={c.seed}
                position={c.position}
                bounds={c.bounds}
                volume={c.volume}
                segments={c.segments}
                opacity={CLOUD_OPACITY}
                speed={CLOUD_SPEED}
                color="white"
              />
            ))}
          </Clouds>
        </Suspense>
      ) : null}
    </>
  );
}
