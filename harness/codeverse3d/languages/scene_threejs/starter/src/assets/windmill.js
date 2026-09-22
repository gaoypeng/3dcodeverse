// src/assets/windmill.js — an asset with a moving part: the returned group
// carries userData.update(t, dt) so the owning zone can fan animation out.
import * as THREE from 'three';

export function buildWindmill(T = THREE) {
  const g = new T.Group();
  g.name = 'Windmill';
  const stone = new T.MeshStandardMaterial({ color: 0xb9a58a, roughness: 0.9 });
  const wood = new T.MeshStandardMaterial({ color: 0x6b4a2b, roughness: 0.8 });
  const tower = new T.Mesh(new T.CylinderGeometry(1.1, 1.6, 5.0, 12), stone);
  tower.position.y = 2.5;
  tower.castShadow = tower.receiveShadow = true;
  const cap = new T.Mesh(new T.ConeGeometry(1.4, 1.2, 12), wood);
  cap.position.y = 5.6;
  cap.castShadow = true;
  const hub = new T.Group();
  hub.position.set(0, 4.6, 1.35);
  for (let i = 0; i < 4; i++) {
    const blade = new T.Mesh(new T.BoxGeometry(0.25, 3.2, 0.06), wood);
    blade.position.y = 1.6;
    blade.castShadow = true;
    const arm = new T.Group();
    arm.rotation.z = (i * Math.PI) / 2;
    arm.add(blade);
    hub.add(arm);
  }
  g.add(tower, cap, hub);
  g.userData.update = (t) => { hub.rotation.z = t * 0.9; };
  return g;
}
