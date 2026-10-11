// The classic renderer: shapes on a diorama set. Everything it draws is a function of t.
import * as THREE from "three";
import { stateAt } from "./poses.mjs";
import { cameraPose, ease } from "./camera.mjs";
import { buildShape, material, SURFACES } from "./shapes.mjs";
import { buildSet } from "./look.mjs";
import { grains, steam, stream } from "./particles.mjs";
import { captionState } from "./captions.mjs";

export function startClassic(shot) {
  window.THREE = THREE; // the HyperFrames three adapter waits on THREE.DefaultLoadingManager
  const W = shot.width, H = shot.height;

  const renderer = new THREE.WebGLRenderer({ canvas: document.getElementById("stage3d"), antialias: true, preserveDrawingBuffer: true });
  renderer.setSize(W, H, false);
  renderer.setPixelRatio(1);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = shot.look.exposure;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  buildSet(THREE, scene, shot.look, shot.set, shot.ctx.anchors);
  const camera = new THREE.PerspectiveCamera(shot.camera.start.fov, W / H, 0.05, 120);

  const actors = {};
  for (const [id, actor] of Object.entries(shot.actors)) {
    const built = buildShape(THREE, actor.shape, actor.dims);
    scene.add(built.group);
    actors[id] = built;
  }
  const pestle = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.04, 0.35, 16), material(THREE, "stone", "#7d756a"));
  pestle.geometry.translate(0, 0.175, 0);
  pestle.castShadow = true;
  pestle.visible = false;
  scene.add(pestle);
  const drops = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 8, 6), new THREE.MeshStandardMaterial({ roughness: 0.3 }), 400);
  drops.count = 0;
  drops.frustumCulled = false; // its bounds are computed once, while empty, and would cull it forever
  scene.add(drops);
  const puffs = new THREE.InstancedMesh(
    new THREE.SphereGeometry(1, 10, 8),
    new THREE.MeshStandardMaterial({ color: 0xffffff, transparent: true, opacity: 0.35, roughness: 1, depthWrite: false }),
    200,
  );
  puffs.count = 0;
  puffs.frustumCulled = false;
  scene.add(puffs);
  const m4 = new THREE.Matrix4(), tint = new THREE.Color();

  // overlay: title banner, step label, captions
  const overlay = { title: document.getElementById("title"), label: document.getElementById("label"), captions: document.getElementById("captions") };
  const L = shot.look;
  if (shot.captions.title) {
    Object.assign(overlay.title.style, { background: L.banner.background, color: L.banner.text, border: `4px solid ${L.banner.border}`, display: "block" });
    overlay.title.textContent = shot.captions.title;
  }
  if (shot.captions.label) {
    Object.assign(overlay.label.style, { background: L.label.background, color: L.label.text, display: "block" });
    overlay.label.textContent = shot.captions.label;
  }
  overlay.captions.style.fontSize = `${L.caption.size}px`;
  overlay.captions.style.textShadow = `0 4px 12px ${L.caption.shadow}, 0 0 3px ${L.caption.shadow}`;
  const spans = shot.captions.words.map((w) => {
    const s = document.createElement("span");
    s.textContent = w.text;
    overlay.captions.appendChild(s);
    return s;
  });

  function renderAt(t) {
    const { world, fx } = stateAt(shot, t);
    for (const [id, built] of Object.entries(actors)) {
      const a = world[id];
      built.group.visible = a.visible && a.scale > 0;
      built.group.position.set(...a.position);
      built.group.rotation.set(0, a.rotationY, 0);
      built.group.scale.setScalar(Math.max(a.scale, 1e-4));
      if (built.fill) {
        const f = a.fill, y = shot.actors[id].dims[1] * (0.15 + 0.85 * 0.9 * f.level);
        built.fill.visible = f.level > 0;
        const r = built.innerRadius(y);
        built.fill.position.y = y;
        built.fill.scale.set(r, 1, r);
        built.fill.material.color.set(f.colour);
        built.fill.material.roughness = SURFACES[f.surface];
      }
    }
    pestle.visible = false;
    drops.count = 0;
    puffs.count = 0;
    for (const e of fx) {
      if (e.type === "tilt") actors[e.actor].group.rotation.z = e.angle;
      else if (e.type === "pestle") {
        pestle.visible = true;
        pestle.position.set(...e.position);
      } else if (e.type === "stream" || e.type === "grains") {
        const pts = e.type === "stream" ? stream(e.seed, e.from, e.to, e.t) : grains(e.seed, e.origin, e.floorY, e.t);
        tint.set(e.colour);
        for (const [x, y, z, s] of pts) {
          if (drops.count >= 400) break;
          m4.makeScale(s, s, s).setPosition(x, y, z);
          drops.setMatrixAt(drops.count, m4);
          drops.setColorAt(drops.count, tint);
          drops.count++;
        }
      } else if (e.type === "steam") {
        for (const [x, y, z, s] of steam(e.seed, e.origin, e.t, e.intensity)) {
          if (puffs.count >= 200) break;
          m4.makeScale(s, s, s).setPosition(x, y, z);
          puffs.setMatrixAt(puffs.count++, m4);
        }
      }
    }
    drops.instanceMatrix.needsUpdate = true;
    if (drops.instanceColor) drops.instanceColor.needsUpdate = true;
    puffs.instanceMatrix.needsUpdate = true;

    const c = shot.camera, p = ease(t / shot.duration, c.easeIn, c.easeOut);
    const pose = cameraPose(c.move, c.params, c.start, c.target, p);
    camera.position.set(...pose.position);
    camera.fov = pose.fov;
    camera.updateProjectionMatrix();
    camera.lookAt(...pose.target);

    const { active, speaking } = captionState(shot.captions.words, t);
    overlay.captions.style.opacity = active >= 0 || t < 0.4 ? "1" : "0";
    spans.forEach((s, i) => (s.style.color = i === active && speaking ? L.caption.active : L.caption.text));

    renderer.render(scene, camera);
  }

  return renderAt;
}
