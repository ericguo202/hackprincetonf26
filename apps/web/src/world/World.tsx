// Lane A: scene, player, houses, physics, furniture rendering.
import { Canvas } from "@react-three/fiber";
import { Physics, RigidBody } from "@react-three/rapier";
import { Sky } from "@react-three/drei";

export function World() {
  return (
    <Canvas shadows camera={{ position: [0, 8, 14], fov: 50 }}>
      <Sky sunPosition={[100, 40, 100]} />
      <ambientLight intensity={0.4} />
      <directionalLight position={[20, 30, 10]} intensity={1.2} castShadow />
      <Physics>
        <RigidBody type="fixed">
          <mesh rotation-x={-Math.PI / 2} receiveShadow>
            <planeGeometry args={[200, 200]} />
            <meshStandardMaterial color="#7a8b6a" />
          </mesh>
        </RigidBody>
      </Physics>
    </Canvas>
  );
}
