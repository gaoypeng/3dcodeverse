// src/zones/pondside.js — pond with the custom water shader + a bobbing lantern (animated).
import * as THREE from 'three';
import { makeWaterMaterial } from '../shaders/water.js';

export function build(ctx) {
  const { heightAt } = ctx;
  const zone = new THREE.Group();
  zone.name = 'Pondside';

  // water plane slightly below the meadow surface; pond centre (-10, 9)
  const waterMat = makeWaterMaterial(THREE);
  if (ctx.env && ctx.env.sunDir) waterMat.uniforms.uSunDir.value.copy(ctx.env.sunDir);
  const water = new THREE.Mesh(new THREE.CircleGeometry(7.5, 48), waterMat);
  water.rotation.x = -Math.PI / 2;
  water.position.set(-10, -0.05, 9);
  water.name = 'PondWater';
  zone.add(water);

  // dark basin under the water so the pond reads as depth, not a disc on grass
  const basin = new THREE.Mesh(
    new THREE.CylinderGeometry(7.8, 5.5, 1.2, 48, 1, false),
    new THREE.MeshStandardMaterial({ color: 0x23321f, roughness: 1.0 }),
  );
  basin.position.set(-10, -0.66, 9);
  basin.receiveShadow = true;
  basin.name = 'PondBasin';
  zone.add(basin);

  // a wooden jetty
  const wood = new THREE.MeshStandardMaterial({ color: 0x7a5a35, roughness: 0.85 });
  const jetty = new THREE.Group();
  jetty.name = 'Jetty';
  const deck = new THREE.Mesh(new THREE.BoxGeometry(1.4, 0.12, 5.0), wood);
  deck.position.set(0, 0.35, 0);
  deck.castShadow = deck.receiveShadow = true;
  jetty.add(deck);
  for (const [px, pz] of [[-0.55, -2.2], [0.55, -2.2], [-0.55, 0], [0.55, 0], [-0.55, 2.2], [0.55, 2.2]]) {
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 1.1, 8), wood);
    post.position.set(px, -0.15, pz);
    post.castShadow = true;
    jetty.add(post);
  }
  jetty.position.set(-3.6, heightAt(-3.6, 9) - 0.25, 9);
  jetty.rotation.y = Math.PI / 2;
  zone.add(jetty);

  // bobbing lantern: emissive + a small point light = the "glow" element
  const lantern = new THREE.Group();
  lantern.name = 'Lantern';
  const glass = new THREE.Mesh(
    new THREE.SphereGeometry(0.22, 16, 12),
    new THREE.MeshStandardMaterial({ color: 0xffc070, emissive: 0xff9a30, emissiveIntensity: 2.2, roughness: 0.4 }),
  );
  const light = new THREE.PointLight(0xffa040, 6, 12, 2);
  lantern.add(glass, light);
  lantern.position.set(-9, 0.25, 7);
  zone.add(lantern);

  zone.userData.update = (t) => {
    waterMat.uniforms.uTime.value = t;
    lantern.position.y = 0.25 + Math.sin(t * 1.4) * 0.08;
    lantern.position.x = -9 + Math.sin(t * 0.3) * 0.6;
  };
  return zone;
}
