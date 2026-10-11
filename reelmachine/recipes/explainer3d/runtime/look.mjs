// The set: sky, fog, ground, scenery, the stage and the lights — all from the look's numbers.
import { mulberry32 } from "./math.mjs";
import { material } from "./shapes.mjs";

export function buildSet(THREE, scene, look, set, anchors) {
  const sky = new THREE.Mesh(
    new THREE.SphereGeometry(60, 32, 16),
    new THREE.ShaderMaterial({
      side: THREE.BackSide, depthWrite: false,
      uniforms: { top: { value: new THREE.Color(look.sky.top) }, bottom: { value: new THREE.Color(look.sky.bottom) } },
      vertexShader: "varying float h; void main(){ h = normalize(position).y; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }",
      fragmentShader: "uniform vec3 top; uniform vec3 bottom; varying float h; void main(){ gl_FragColor = vec4(mix(bottom, top, clamp(h*1.6,0.0,1.0)),1.0); }",
    }),
  );
  scene.add(sky);
  scene.fog = new THREE.Fog(new THREE.Color(look.fog.colour), look.fog.near, look.fog.far);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(80, 80), new THREE.MeshStandardMaterial({ color: new THREE.Color(look.ground.colour), roughness: 1 }));
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);

  if (set.environment === "meadow-day") {
    const rnd = mulberry32(1234);
    const trunk = new THREE.MeshStandardMaterial({ color: 0x6b4a2b, roughness: 1 });
    const leaves = [new THREE.MeshStandardMaterial({ color: 0x2f6b34, roughness: 1 }), new THREE.MeshStandardMaterial({ color: 0x3f7f3a, roughness: 1 })];
    for (let i = 0; i < 46; i++) {
      const a = rnd() * Math.PI * 2, r = 7 + rnd() * 12, s = 0.8 + rnd() * 0.9;
      const tree = new THREE.Group();
      const t = new THREE.Mesh(new THREE.CylinderGeometry(0.08 * s, 0.12 * s, 0.8 * s, 8), trunk);
      t.position.y = 0.4 * s;
      const c = new THREE.Mesh(new THREE.ConeGeometry(0.7 * s, 2.6 * s, 10), leaves[i % 2]);
      c.position.y = 0.8 * s + 1.3 * s;
      tree.add(t, c);
      tree.position.set(Math.sin(a) * r, 0, Math.cos(a) * r - 4);
      tree.traverse((o) => (o.castShadow = true));
      scene.add(tree);
    }
  } else {
    const backdrop = new THREE.Mesh(
      new THREE.CylinderGeometry(14, 14, 20, 48, 1, true, -Math.PI / 2, Math.PI),
      new THREE.MeshStandardMaterial({ color: new THREE.Color(look.sky.bottom), side: THREE.BackSide, roughness: 1 }),
    );
    backdrop.position.set(0, 10, 0);
    scene.add(backdrop);
  }

  const top = anchors["stage.center"][1];
  if (set.stage === "stone-slab") {
    const slab = new THREE.Mesh(new THREE.BoxGeometry(1.6, top, 1.0), material(THREE, "stone", "#a39b8f"));
    slab.position.y = top / 2;
    slab.castShadow = true;
    slab.receiveShadow = true;
    scene.add(slab);
  } else if (set.stage === "table") {
    const wood = material(THREE, "wood", null);
    const board = new THREE.Mesh(new THREE.BoxGeometry(1.4, 0.06, 0.9), wood);
    board.position.y = top - 0.03;
    board.receiveShadow = true;
    board.castShadow = true;
    scene.add(board);
    for (const [x, z] of [[-0.62, -0.38], [0.62, -0.38], [-0.62, 0.38], [0.62, 0.38]]) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(0.06, top - 0.06, 0.06), wood);
      leg.position.set(x, (top - 0.06) / 2, z);
      scene.add(leg);
    }
  }

  const L = look.lights;
  scene.add(new THREE.HemisphereLight(new THREE.Color(L.hemi.sky), new THREE.Color(L.hemi.ground), L.hemi.intensity));
  const key = new THREE.DirectionalLight(new THREE.Color(L.key.colour), L.key.intensity);
  key.position.set(...L.key.position);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  Object.assign(key.shadow.camera, { left: -4, right: 4, top: 4, bottom: -4, near: 0.5, far: 30 });
  key.shadow.bias = -0.0005;
  scene.add(key);
  const fill = new THREE.DirectionalLight(new THREE.Color(L.fill.colour), L.fill.intensity);
  fill.position.set(...L.fill.position);
  scene.add(fill);
}
