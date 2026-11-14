// Lane C: the scene. Ground, light, physics, the local player, and a few
// obstacles to walk around until the block and house packs land.
//
// WASD / arrows to move, Shift to run, Space to jump. Drag to orbit the
// camera, wheel to zoom.
import { Canvas } from "@react-three/fiber";
import { Physics, RigidBody } from "@react-three/rapier";
import { Sky } from "@react-three/drei";

import { Player } from "../player/Player";

/** Placeholder obstacles: [position, size], meters. Replaced by block/collision.glb. */
const TEST_BOXES: { pos: [number, number, number]; size: [number, number, number] }[] = [
  { pos: [3, 0.5, -3], size: [1, 1, 1] },
  { pos: [-4, 0.75, -1], size: [1.5, 1.5, 1.5] },
  { pos: [0, 0.4, -7], size: [3, 0.8, 1] },
];

export function World() {
  return (
    <Canvas shadows camera={{ position: [0, 3.4, 5], fov: 50 }}>
      <Sky sunPosition={[100, 40, 100]} />
      <ambientLight intensity={0.4} />
      <directionalLight position={[20, 30, 10]} intensity={1.2} castShadow />
      <Physics>
        <RigidBody type="fixed" colliders="cuboid">
          <mesh position={[0, -0.05, 0]} receiveShadow>
            <boxGeometry args={[200, 0.1, 200]} />
            <meshStandardMaterial color="#7a8b6a" />
          </mesh>
        </RigidBody>

        {TEST_BOXES.map((b, i) => (
          <RigidBody key={i} type="fixed" colliders="cuboid" position={b.pos}>
            <mesh castShadow receiveShadow>
              <boxGeometry args={b.size} />
              <meshStandardMaterial color="#9a9a9a" />
            </mesh>
          </RigidBody>
        ))}

        <Player spawn={[0, 1, 0]} />
      </Physics>
    </Canvas>
  );
}
