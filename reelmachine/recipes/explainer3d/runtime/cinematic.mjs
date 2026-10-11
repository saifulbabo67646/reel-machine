// The cinematic renderer: CC0 models, image-based light, water, impact effects and
// post-processing. Like the classic renderer, every frame is a function of t alone.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { BokehPass } from "three/addons/postprocessing/BokehPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";
import { stateAt } from "./poses.mjs";
import { cameraPose, ease } from "./camera.mjs";
import { buildShape, innerRadius } from "./shapes.mjs";
import { buildSet } from "./look.mjs";
import { Effects, crumbHeap, roughGeometry } from "./fx.mjs";
import { buildLake, buildUnderwater } from "./sets.mjs";
import { CinematicOverlay } from "./overlay.mjs";
import { clamp01, easeOutCubic, fillY, lerp, mulberry32 } from "./math.mjs";

const CONTAINER_SHAPES = new Set(["bowl", "cup", "pot", "jar"]);

function gradePass(post) {
  return new ShaderPass({
    uniforms: {
      tDiffuse: { value: null }, uTime: { value: 0 }, uSat: { value: post.saturation ?? 1.25 },
      uContrast: { value: post.contrast ?? 1.08 }, uVig: { value: post.vignette ?? 0.55 },
      uWarm: { value: new THREE.Vector3(...(post.warmth || [1.06, 1.0, 0.9])) }, uGrain: { value: post.grain ?? 0.03 },
    },
    vertexShader: "varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
    fragmentShader: `uniform sampler2D tDiffuse; uniform float uTime, uSat, uContrast, uVig, uGrain; uniform vec3 uWarm; varying vec2 vUv;
      float hash(vec2 p){ return fract(sin(dot(p, vec2(12.9898, 78.233)) + uTime * 17.0) * 43758.5453); }
      void main(){ vec3 c = texture2D(tDiffuse, vUv).rgb; float l = dot(c, vec3(0.299, 0.587, 0.114));
        c = mix(vec3(l), c, uSat) * uWarm; c = (c - 0.5) * uContrast + 0.5; vec2 d = vUv - 0.5; d.x *= 0.5625;
        c *= mix(1.0, smoothstep(0.78, 0.2, length(d) * 1.3), uVig); c += (hash(vUv * 977.0) - 0.5) * uGrain;
        gl_FragColor = vec4(c, 1.0); }`,
  });
}

// a swimming body wave: displaces each vertex sideways, growing toward the tail
function wiggly(obj) {
  const box = new THREE.Box3().setFromObject(obj), size = box.getSize(new THREE.Vector3());
  const uniforms = { uPhase: { value: 0 }, uAmp: { value: 0 }, uHeadZ: { value: box.max.z }, uLen: { value: Math.max(size.z, 1e-4) } };
  obj.traverse((o) => {
    if (!o.isMesh) return;
    const m = o.material.clone();
    m.onBeforeCompile = (sh) => {
      Object.assign(sh.uniforms, uniforms);
      sh.vertexShader = "uniform float uPhase, uAmp, uHeadZ, uLen;\n" + sh.vertexShader.replace("#include <begin_vertex>",
        `#include <begin_vertex>
         float tf = clamp((uHeadZ - position.z) / uLen, 0.0, 1.0);
         transformed.x += sin(tf * 5.0 - uPhase) * uAmp * uLen * tf * tf;`);
    };
    m.customProgramCacheKey = () => "e3d-wiggle";
    o.material = m;
  });
  return uniforms;
}

function moundGeometry(seed) {
  const g = new THREE.SphereGeometry(1, 96, 48, 0, Math.PI * 2, 0, Math.PI / 2), p = g.attributes.position, r = mulberry32(seed);
  const bumps = Array.from({ length: 60 }, () => [new THREE.Vector3(r() - 0.5, r(), r() - 0.5).normalize(), r() * 0.07]);
  const v = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) {
    v.set(p.getX(i), p.getY(i), p.getZ(i));
    let d = 1;
    for (const [b, s] of bumps) d += s * Math.max(0, v.dot(b)) ** 6;
    v.multiplyScalar(d);
    p.setXYZ(i, v.x, v.y, v.z);
  }
  g.computeVertexNormals();
  return g;
}

// What sits inside a container: a liquid disc, a paste mound, a heap of crumbs, a honey coil.
class FillView {
  constructor(group, dims, innerR, seed, honeyMat) {
    this.dims = dims;
    this.innerR = innerR;
    this.disc = new THREE.Mesh(new THREE.CircleGeometry(1, 64), new THREE.MeshPhysicalMaterial({ color: 0xd9d27a, roughness: 0.12, clearcoat: 0.6 }));
    this.disc.rotation.x = -Math.PI / 2;
    this.mound = new THREE.Mesh(moundGeometry(seed), new THREE.MeshStandardMaterial({ color: 0xc97a34, roughness: 0.7 }));
    this.mound.castShadow = true;
    this.heap = crumbHeap(seed, 260, 1, 1);
    this.crumbs = new THREE.InstancedMesh(roughGeometry(THREE, seed + 1), new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.92 }), 260);
    this.crumbs.castShadow = true;
    this.crumbs.frustumCulled = false;
    const coil = [];
    for (let i = 0; i <= 260; i++) {
      const u = i / 260, a = u * Math.PI * 2 * 5.5, rr = 0.42 - 0.38 * u;
      coil.push(new THREE.Vector3(Math.cos(a) * rr, u * 0.22, Math.sin(a) * rr));
    }
    this.coil = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(coil), 700, 0.045, 10), honeyMat);
    for (const o of [this.disc, this.mound, this.crumbs, this.coil]) { o.visible = false; group.add(o); }
    this.m = new THREE.Matrix4();
    this.c = new THREE.Color();
  }
  update(fill, coilAmount, squash) {
    const [w, h] = this.dims;
    const floor = 0.15 * h, y = fillY(h, fill.level), r = this.innerR(Math.max(y, floor + 1e-3));
    const show = fill.level > 0.001;
    this.disc.visible = show && fill.surface === "liquid";
    this.mound.visible = show && fill.surface === "paste";
    this.crumbs.visible = show && fill.surface === "crumbs";
    if (this.disc.visible) {
      this.disc.position.y = y;
      this.disc.scale.setScalar(r);
      this.disc.material.color.set(fill.colour);
    }
    if (this.mound.visible) {
      const mh = Math.max(0.002, (y - floor) * 1.4) * (1 - squash);
      this.mound.position.y = floor;
      this.mound.scale.set(r * 0.95 * (1 + squash), mh, r * 0.95 * (1 + squash));
      this.mound.material.color.set(fill.colour);
    }
    let heapTop = y;
    if (this.crumbs.visible) {
      const count = Math.floor(260 * clamp01(fill.level / 0.35)), hh = Math.max(0.004, (y - floor) * 1.3);
      for (let i = 0; i < count; i++) {
        const c = this.heap[i];
        this.m.makeRotationFromEuler(new THREE.Euler(c.rot, c.rot * 1.3, 0));
        this.m.scale(new THREE.Vector3(c.s, c.s * 0.8, c.s));
        this.m.setPosition(c.x * r * 0.85, floor + c.y * hh, c.z * r * 0.85);
        this.crumbs.setMatrixAt(i, this.m);
        this.crumbs.setColorAt(i, this.c.set(fill.colour).multiplyScalar(0.7 + c.shade * 0.45));
      }
      this.crumbs.count = count;
      this.crumbs.instanceMatrix.needsUpdate = true;
      if (this.crumbs.instanceColor) this.crumbs.instanceColor.needsUpdate = true;
      heapTop = floor + hh;
    }
    this.coil.visible = !!fill.glaze && show && coilAmount > 0;
    if (this.coil.visible) {
      this.coil.position.y = (fill.surface === "paste" ? floor + (y - floor) * 1.4 : heapTop) + 0.002;
      this.coil.scale.setScalar(r);
      this.coil.material.color.set(fill.glaze);
      const n = this.coil.geometry.index.count;
      this.coil.geometry.setDrawRange(0, Math.floor((n * clamp01(coilAmount)) / 3) * 3);
    }
  }
  // world position of crumb i landing in this container (for crumbs in flight)
  heapPoint(i, groupPos, level) {
    const [, h] = this.dims, floor = 0.15 * h, y = fillY(h, level), r = this.innerR(Math.max(y, floor + 1e-3));
    const c = this.heap[i % this.heap.length], hh = Math.max(0.004, (y - floor) * 1.3);
    return [groupPos[0] + c.x * r * 0.85, groupPos[1] + floor + c.y * hh, groupPos[2] + c.z * r * 0.85];
  }
}

export async function startCinematic(shot) {
  window.THREE = THREE;
  const W = shot.width, H = shot.height, L = shot.look, post = L.post || {};
  const renderer = new THREE.WebGLRenderer({ canvas: document.getElementById("stage3d"), antialias: true, preserveDrawingBuffer: true });
  renderer.setSize(W, H, false);
  renderer.setPixelRatio(1);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = L.exposure ?? 1;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(shot.camera.start.fov, W / H, 0.01, 3000);

  const loader = new GLTFLoader(), models = new Map();
  const loadModel = (url) => {
    if (!models.has(url)) models.set(url, loader.loadAsync(url).then((g) => g.scene));
    return models.get(url).then((s) => s.clone(true));
  };
  const fit = (obj, dims) => {
    const box = new THREE.Box3().setFromObject(obj), size = box.getSize(new THREE.Vector3());
    obj.scale.multiplyScalar(dims[1] / Math.max(size.y, 1e-6));
    const b2 = new THREE.Box3().setFromObject(obj), c = b2.getCenter(new THREE.Vector3());
    obj.position.sub(new THREE.Vector3(c.x, b2.min.y, c.z));
    obj.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
    const g = new THREE.Group();
    g.add(obj);
    return g;
  };

  const env = shot.set.environment, files = shot.env ? shot.env.files : {};
  let set = { tick() {} }, underwater = false;
  if (env === "lake") set = await buildLake(THREE, renderer, scene, shot, files, loadModel, fit);
  else if (env === "underwater") { set = await buildUnderwater(THREE, renderer, scene, shot, files); underwater = true; }
  else buildSet(THREE, scene, L, shot.set, shot.ctx.anchors);

  const effects = new Effects(THREE, scene);
  const actors = {};
  for (const [id, a] of Object.entries(shot.actors)) {
    let base, innerR = null, wiggle = null, template = null;
    if (a.shape) {
      const built = buildShape(THREE, a.shape, a.dims);
      base = built.group;
      if (built.fill) { built.fill.visible = false; built.fill.scale.set(0, 0, 0); }
      if (CONTAINER_SHAPES.has(a.shape.shape)) innerR = (y) => innerRadius(a.shape.shape, a.dims[0], a.dims[1], y);
    } else {
      const raw = await loadModel(a.model.url);
      wiggle = wiggly(raw);
      template = raw;
      base = fit(raw, a.dims);
      if (a.model.container) innerR = (y) => (a.dims[0] / 2) * (0.6 + 0.3 * clamp01(y / a.dims[1]));
    }
    const group = new THREE.Group();
    const count = a.count || 1, spacing = a.dims[0] * 1.7, pieces = [];
    for (let k = 0; k < count; k++) {
      const piece = k === 0 ? base : base.clone(true);
      piece.position.x = (k - (count - 1) / 2) * spacing;
      group.add(piece);
      pieces.push(piece);
    }
    scene.add(group);
    const fill = innerR ? new FillView(group, a.dims, innerR, 101 + Object.keys(actors).length, effects.honeyMat) : null;
    let school = null;
    if (template && count > 1) {
      school = Array.from({ length: count }, () => {
        const clone = template.clone(true);
        const u = wiggly(clone);
        const g = fit(clone, a.dims);
        const holder = new THREE.Group();
        holder.add(g);
        holder.visible = false;
        scene.add(holder);
        return { holder, u };
      });
    }
    actors[id] = { group, pieces, fill, wiggle, school, spacing, dims: a.dims };
  }

  const composer = new EffectComposer(renderer);
  const renderPass = new RenderPass(scene, camera);
  const bokeh = new BokehPass(scene, camera, { focus: 1, aperture: post.aperture ?? 0.0018, maxblur: post.maxblur ?? 0.012 });
  const bloomCfg = underwater ? post.bloomUnder || [0.5, 0.55, 0.9] : post.bloom || [0.3, 0.5, 2.0];
  const bloom = new UnrealBloomPass(new THREE.Vector2(W / 2, H / 2), bloomCfg[0], bloomCfg[1], bloomCfg[2]);
  const grade = gradePass(post);
  composer.addPass(renderPass);
  composer.addPass(bokeh);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());
  composer.addPass(grade);
  const overlay = new CinematicOverlay(document.getElementById("overlay"), shot);
  const centre = (w, id) => [w[id].position[0], w[id].position[1] + (shot.ctx.dims[id][1] * w[id].scale) / 2, w[id].position[2]];

  return function renderAt(t) {
    const { world, fx } = stateAt(shot, t);
    const byActor = {};
    for (const e of fx) if (e.actor) (byActor[e.actor] ||= []).push(e);
    let coil = {}, squashFill = {};
    for (const e of fx) {
      if (e.type === "coil") coil[e.into] = e.amount;
      if (e.type === "squashFill") squashFill[e.into] = e.k;
    }
    for (const [id, A] of Object.entries(actors)) {
      const a = world[id], g = A.group, mine = byActor[id] || [];
      g.visible = a.visible && a.scale > 0;
      g.position.set(...a.position);
      g.rotation.set(0, a.rotationY, 0);
      g.scale.setScalar(Math.max(a.scale, 1e-4));
      if (A.wiggle) A.wiggle.uAmp.value = 0;
      A.pieces.forEach((p, k) => { p.visible = true; p.position.set((k - (A.pieces.length - 1) / 2) * A.spacing, 0, 0); p.scale.setScalar(1); });
      for (const e of mine) {
        if (e.type === "orient") g.lookAt(g.position.x + e.dir[0], g.position.y + e.dir[1], g.position.z + e.dir[2]);
        else if (e.type === "wiggle" && A.wiggle) { A.wiggle.uPhase.value = e.phase; A.wiggle.uAmp.value = e.amp; }
        else if (e.type === "squash") g.scale.set(a.scale * (1 + e.k), a.scale * (1 - e.k), a.scale * (1 + e.k));
        else if (e.type === "spin") { g.rotation.y = a.rotationY + e.angle; g.rotation.x = 0.3 * Math.sin(e.angle * 1.4); }
        else if (e.type === "popRow") {
          const n = A.pieces.length;
          A.pieces.forEach((piece, k) => {
            const s0 = (k / (n + 1)) * 0.7, u = clamp01((e.p - s0) / 0.25), slot = (k - (n - 1) / 2) * A.spacing;
            if (u <= 0) { piece.visible = false; return; }
            const from = [e.from[0] - a.position[0], e.from[1] - a.position[1], e.from[2] - a.position[2]];
            piece.position.set(lerp(from[0], slot, u), lerp(from[1], 0, u) + Math.sin(u * Math.PI) * 0.16, lerp(from[2], 0, u));
            const land = (e.p - s0 - 0.25) * e.dur, sq = land >= 0 ? Math.exp(-land * 9) * Math.sin(land * 45) * 0.3 : 0;
            piece.scale.set(1 + sq, 1 - sq, 1 + sq);
          });
        } else if (e.type === "school" && A.school) {
          g.visible = false;
          const T = e.p * e.dur, eatT = e.eatAt * e.dur;
          A.school.forEach((f, i) => {
            const a0 = i * 1.1, k = clamp01((T - (0.4 + i * 0.25)) / (2.8 + i * 0.2 - i * 0.25)), rad = lerp(4.5, e.radius + i * 0.12, easeOutCubic(k));
            const ang = a0 + T * (0.5 + i * 0.07) * (i % 2 ? 1 : -1);
            let pos = new THREE.Vector3(e.center[0] + Math.cos(ang) * rad, e.center[1] + Math.sin(T * 0.8 + i) * 0.15 - i * 0.04, e.center[2] + Math.sin(ang) * rad);
            let vel = new THREE.Vector3(-Math.sin(ang), 0, Math.cos(ang)).multiplyScalar(i % 2 ? 1 : -1);
            let speed = 9, amp = 0.1;
            if (i === 0 && e.eat) {
              const lunge = clamp01((T - (eatT - 0.5)) / 0.5) ** 3, after = clamp01((T - eatT) / 1.1);
              const target = new THREE.Vector3(...e.center), head = target.clone().sub(vel.clone().multiplyScalar(A.dims[2] * 0.5));
              pos = pos.lerp(head, lunge);
              if (lunge > 0 && lunge < 1) vel = target.clone().sub(pos);
              if (after > 0) { pos = head.clone().add(new THREE.Vector3(0, 0.5 * after, 0)).add(vel.clone().normalize().multiplyScalar(after * 0.8)); vel.add(new THREE.Vector3(0, 0.4, 0)); }
              if (T > eatT - 0.5 && T < eatT + 0.4) { speed = 26; amp = 0.2; }
            }
            f.holder.visible = true;
            f.holder.position.copy(pos);
            f.holder.lookAt(pos.clone().add(vel.lengthSq() > 1e-8 ? vel : new THREE.Vector3(0, 0, 1)));
            f.u.uPhase.value = T * speed + i;
            f.u.uAmp.value = amp;
          });
        }
      }
      if (A.school && !mine.some((e) => e.type === "school")) A.school.forEach((f) => (f.holder.visible = false));
      if (A.fill && a.fill) A.fill.update(a.fill, coil[id] ?? 1, squashFill[id] || 0);
    }

    effects.begin();
    let shakeAmt = 0, flash = 0;
    for (const e of fx) {
      if (e.type === "splash") effects.splash(e.at, e.age, e.size, e.seed);
      else if (e.type === "drops") effects.drops_(e.path, e.u, e.age, e.seed);
      else if (e.type === "dust") effects.dust_(e.at, e.age, e.size, e.seed);
      else if (e.type === "chunks") effects.chunks_(e.at, e.age, e.colour, e.floorY, e.seed);
      else if (e.type === "ring") effects.ring_(e.at, e.age);
      else if (e.type === "honey") effects.honey(e.from, e.to, e.t, e.grow, e.thin, e.colour);
      else if (e.type === "bubbles") effects.bubbleTrail(e.from, e.to, e.p, e.dur, e.seed);
      else if (e.type === "burstBubbles") effects.burstBubbles(e.at, e.age, e.seed);
      else if (e.type === "crumbs") {
        const box = actors[e.into], into = world[e.into];
        if (box && box.fill) {
          const heap = Array.from({ length: 120 }, (_, i) => {
            const p = box.fill.heapPoint(i, into.position, into.fill.level);
            return { x: p[0] - into.position[0], y: p[1] - into.position[1], z: p[2] - into.position[2], s: 0.0035 + (i % 7) * 0.0008, rot: i, shade: (i % 5) / 5 };
          });
          effects.crumbsFly(e.from, heap, into.position, e.age, e.colour, e.seed);
        }
      } else if (e.type === "impact") shakeAmt += e.strength * Math.exp(-e.age * 9) * (L.shake ?? 1);
      if (e.type === "flash") flash += e.amount * Math.exp(-e.age * 12);
    }
    effects.end();

    const c = shot.camera, p = ease(t / shot.duration, c.easeIn, c.easeOut);
    const aim = c.params.aim || [0, 0, 0];
    const target = c.track && world[c.track] ? centre(world, c.track).map((v, i) => v + aim[i]) : c.target;
    const pose = cameraPose(c.move, c.params, c.start, target, p);
    const k = new THREE.Vector3(Math.sin(t * 91) * shakeAmt, Math.sin(t * 77 + 1) * shakeAmt, Math.cos(t * 83) * shakeAmt);
    camera.position.set(...pose.position).add(k);
    camera.lookAt(new THREE.Vector3(...pose.target).add(k.multiplyScalar(0.5)));
    const punch = c.easeIn ? 1 - (L.punch ?? 0.1) * (1 - easeOutCubic(clamp01(t / 0.35))) : 1;
    camera.fov = pose.fov * punch;
    camera.updateProjectionMatrix();
    const focus = camera.position.distanceTo(new THREE.Vector3(...pose.target));
    bokeh.uniforms.focus.value = focus;
    bokeh.uniforms.aperture.value = (post.aperture ?? 0.0018) * Math.min(4, Math.max(0.2, 1 / focus));
    set.tick(t + shot.reel.startMs / 1000, camera);
    grade.uniforms.uTime.value = t;
    overlay.update(t, flash);
    composer.render();
  };
}
