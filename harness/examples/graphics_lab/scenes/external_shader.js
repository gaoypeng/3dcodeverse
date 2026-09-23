/**
 * External shader files: a controlled alloy-material sculpture study.
 * The animated colour phase is artistic, not a thermal or optical simulation.
 * Scene creation awaits all local files; update only changes cached uniforms.
 */
import * as THREE from 'three';
import { attachDisposal, snapshotResources } from '../lib/lifecycle.js';

export async function createScene({ loaders }) {
  const text = new THREE.FileLoader(loaders.manager);
  const [vertexShader, fragmentSource, commonSource] = await Promise.all([
    text.loadAsync(new URL('../shaders/external_shader.vert', import.meta.url).href),
    text.loadAsync(new URL('../shaders/external_shader.frag', import.meta.url).href),
    text.loadAsync(new URL('../shaders/external_shader_common.glsl', import.meta.url).href),
  ]);
  const scene = new THREE.Scene();
  scene.name = 'ChromaticAlloyStudy';
  scene.background = new THREE.Color(0x252c33);
  const group = new THREE.Group();
  group.name = 'AlloyExhibit';
  scene.add(group);

  const hemisphere = new THREE.HemisphereLight(0xcbdcf0, 0x5a493b, 1.8);
  const key = new THREE.DirectionalLight(0xffdfb0, 1.8);
  key.position.set(-3, 4, 3);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.camera.left = -5;
  key.shadow.camera.right = 5;
  key.shadow.camera.top = 5;
  key.shadow.camera.bottom = -5;
  key.shadow.camera.near = 0.1;
  key.shadow.camera.far = 20;
  key.shadow.normalBias = 0.018;
  key.shadow.radius = 12;
  const fill = new THREE.DirectionalLight(0x9bc8ff, 0.7);
  fill.position.set(4, 2, -3);
  scene.add(hemisphere, key, fill);

  const studioMaterial = new THREE.MeshStandardMaterial({ color: 0x33383c, roughness: 0.78 });
  const floor = new THREE.Mesh(new THREE.CircleGeometry(8, 192), studioMaterial);
  floor.name = 'StudioFloor';
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  group.add(floor);

  // A continuous circular cyclorama avoids a clipped floor/background horizon.
  // Its inward normals join the floor smoothly and work from every fixed camera.
  const positions = [], normals = [], indices = [];
  const radialSegments = 32, aroundSegments = 192;
  for (let ring = 0; ring <= radialSegments + 1; ring++) {
    const angle = Math.min(ring, radialSegments) / radialSegments * Math.PI / 2;
    const radius = 8 + 4 * Math.sin(angle);
    const y = ring > radialSegments ? 16 : 4 * (1 - Math.cos(angle));
    for (let column = 0; column <= aroundSegments; column++) {
      const azimuth = column / aroundSegments * Math.PI * 2;
      const x = Math.sin(azimuth), z = Math.cos(azimuth);
      positions.push(x * radius, y, z * radius);
      normals.push(-x * Math.sin(angle), Math.cos(angle), -z * Math.sin(angle));
      if (ring < radialSegments + 1 && column < aroundSegments) {
        const a = ring * (aroundSegments + 1) + column;
        const b = a + aroundSegments + 1;
        indices.push(a, b, a + 1, a + 1, b, b + 1);
      }
    }
  }
  const coveGeometry = new THREE.BufferGeometry();
  coveGeometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  coveGeometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  coveGeometry.setIndex(indices);
  const cove = new THREE.Mesh(coveGeometry, studioMaterial);
  cove.name = 'StudioCyclorama';
  cove.receiveShadow = true;
  group.add(cove);
  const plinth = new THREE.Mesh(new THREE.CylinderGeometry(1.62, 1.66, 0.25, 128),
    new THREE.MeshStandardMaterial({ color: 0x282e34, roughness: 0.58, metalness: 0.08 }));
  plinth.name = 'ExhibitPlinth';
  plinth.position.y = 0.125;
  plinth.castShadow = plinth.receiveShadow = true;
  group.add(plinth);
  const edge = new THREE.Mesh(new THREE.TorusGeometry(1.62, 0.009, 8, 192),
    new THREE.MeshStandardMaterial({ color: 0xa78d60, roughness: 0.28, metalness: 0.7 }));
  edge.name = 'BrassRim';
  edge.rotation.x = Math.PI / 2;
  edge.position.y = 0.235;
  group.add(edge);

  // The external shader uses an analytic studio environment. Its warm key and
  // blue fill share the host lights' world directions; it does not sample
  // environment maps or receive shadows from surrounding scene objects.
  const material = new THREE.ShaderMaterial({
    name: 'ExternalAnodizedAlloy',
    glslVersion: THREE.GLSL3,
    vertexShader,
    fragmentShader: `${commonSource}\n${fragmentSource}`,
    uniforms: {
      uTime: { value: 0 },
      uRoughness: { value: 0.39 },
      uFilmStrength: { value: 0.42 },
      uBaseReflectance: { value: new THREE.Color(0.50, 0.52, 0.56) },
    },
    fog: false,
  });
  const sculpture = new THREE.Mesh(new THREE.TorusKnotGeometry(0.84, 0.255, 320, 64, 2, 3), material);
  sculpture.name = 'ChromaticFold';
  sculpture.rotation.set(0.25, 0.22, -0.22);
  sculpture.geometry.computeBoundingBox();
  sculpture.position.y = 1.57;
  sculpture.castShadow = true;
  group.add(sculpture);
  const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.055, 0.54, 32),
    new THREE.MeshStandardMaterial({ color: 0x272a2e, metalness: 0.75, roughness: 0.32 }));
  stem.name = 'SculptureMount';
  stem.position.y = 0.52;
  stem.castShadow = true;
  group.add(stem);

  const resources = snapshotResources(group);
  resources.add({ dispose() { key.shadow.dispose(); } });
  attachDisposal(group, resources);
  return {
    scene,
    cameras: [
      { name: 'alloy', position: [4.5, 3.05, 5.8], lookAt: [0, 1.35, 0], fov: 37 },
      { name: 'surface', position: [2.05, 1.95, 3.05], lookAt: [0, 1.55, 0], fov: 34 },
      { name: 'profile', position: [-4.5, 2.25, 3.7], lookAt: [0, 1.38, 0], fov: 37 },
    ],
    update(t) {
      material.uniforms.uTime.value = t;
      sculpture.rotation.y = 0.22 + 0.18 * Math.sin(t * 0.4);
    },
    dispose() { group.userData.dispose(); },
  };
}
