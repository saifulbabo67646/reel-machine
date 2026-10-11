// Cinematic effects. Each frame is drawn from scratch from the fx list stateAt returns:
// every particle is closed-form in its event's age, so any frame renders on its own.
import { mulberry32 } from "./math.mjs";

const G = 4.9; // half of g, in metres per second squared

export function softDot(THREE) {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const x = c.getContext("2d"), g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.4, "rgba(255,255,255,0.5)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  x.fillStyle = g;
  x.fillRect(0, 0, 64, 64);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export function roughGeometry(THREE, seed, detail = 1) {
  const g = new THREE.IcosahedronGeometry(1, detail), p = g.attributes.position, r = mulberry32(seed);
  for (let i = 0; i < p.count; i++) {
    const k = 0.7 + r() * 0.6;
    p.setXYZ(i, p.getX(i) * k, p.getY(i) * k * 0.8, p.getZ(i) * k);
  }
  g.computeVertexNormals();
  return g;
}

// A pool of instanced pieces, refilled every frame.
class Pool {
  constructor(THREE, geometry, material, max) {
    this.mesh = new THREE.InstancedMesh(geometry, material, max);
    this.mesh.frustumCulled = false;
    this.mesh.castShadow = true;
    this.max = max;
    this.n = 0;
    this.m = new THREE.Matrix4();
    this.q = new THREE.Quaternion();
    this.e = new THREE.Euler();
    this.v = new THREE.Vector3();
    this.s = new THREE.Vector3();
    this.c = new THREE.Color();
  }
  reset() { this.n = 0; }
  add(x, y, z, scale, rot = 0, colour = null, squashY = 1) {
    if (this.n >= this.max) return;
    this.e.set(rot, rot * 1.3, rot * 0.7);
    this.q.setFromEuler(this.e);
    this.m.compose(this.v.set(x, y, z), this.q, this.s.set(scale, scale * squashY, scale));
    this.mesh.setMatrixAt(this.n, this.m);
    if (colour) this.mesh.setColorAt(this.n, this.c.set(colour));
    this.n++;
  }
  commit() {
    this.mesh.count = this.n;
    this.mesh.instanceMatrix.needsUpdate = true;
    if (this.mesh.instanceColor) this.mesh.instanceColor.needsUpdate = true;
  }
}

class Points {
  constructor(THREE, map, { size, colour, opacity, max }) {
    this.geometry = new THREE.BufferGeometry();
    this.array = new Float32Array(max * 3);
    this.geometry.setAttribute("position", new THREE.BufferAttribute(this.array, 3));
    this.material = new THREE.PointsMaterial({ map, size, color: colour, transparent: true, opacity, depthWrite: false });
    this.points = new THREE.Points(this.geometry, this.material);
    this.points.frustumCulled = false;
    this.max = max;
    this.n = 0;
    this.base = opacity;
  }
  reset() { this.n = 0; }
  add(x, y, z) {
    if (this.n >= this.max) return;
    this.array.set([x, y, z], this.n * 3);
    this.n++;
  }
  commit(opacity = 1) {
    for (let i = this.n; i < this.max; i++) this.array[i * 3 + 1] = -500;
    this.geometry.attributes.position.needsUpdate = true;
    this.material.opacity = this.base * opacity;
  }
}

// The deterministic heap a container's crumbs settle into: flying crumbs land exactly here.
export function crumbHeap(seed, count, radius, height) {
  const r = mulberry32(seed), out = [];
  for (let i = 0; i < count; i++) {
    const a = r() * Math.PI * 2, rad = Math.sqrt(r()) * radius;
    const y = height * (1 - (rad / radius) ** 2) + r() * height * 0.25;
    out.push({ x: Math.cos(a) * rad, y, z: Math.sin(a) * rad, s: 0.0035 + r() * 0.0055, rot: r() * 6, shade: r() });
  }
  return out;
}

export class Effects {
  constructor(THREE, scene, opts) {
    this.T = THREE;
    this.opts = opts;
    const dot = softDot(THREE);
    this.drops = new Pool(THREE, new THREE.SphereGeometry(1, 10, 8),
      new THREE.MeshStandardMaterial({ color: 0xe8f6ff, roughness: 0.02, metalness: 0, envMapIntensity: 2.5, emissive: 0x334455 }), 700);
    this.drops.mesh.castShadow = false;
    this.mist = new Points(THREE, dot, { size: 0.22, colour: 0xfff4e6, opacity: 0.5, max: 400 });
    this.dust = new Points(THREE, dot, { size: 0.05, colour: 0xd8c3a0, opacity: 0.55, max: 300 });
    this.bubbles = new Pool(THREE, new THREE.SphereGeometry(1, 12, 10),
      new THREE.MeshStandardMaterial({ color: 0xffffff, transparent: true, opacity: 0.55, roughness: 0, metalness: 0.2, emissive: 0x4a9aa8 }), 260);
    this.bubbles.mesh.castShadow = false;
    this.crumbs = new Pool(THREE, roughGeometry(THREE, 5), new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.92 }), 400);
    this.chunks = new Pool(THREE, roughGeometry(THREE, 8), new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.55 }), 120);
    this.ripples = Array.from({ length: 9 }, () => {
      const m = new THREE.Mesh(new THREE.RingGeometry(0.93, 1, 96), new THREE.MeshBasicMaterial({ color: 0xfff0dd, transparent: true, opacity: 0.5, depthWrite: false }));
      m.rotation.x = -Math.PI / 2;
      return m;
    });
    this.ring = new THREE.Mesh(new THREE.RingGeometry(0.8, 1, 96), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, depthWrite: false }));
    this.ring.rotation.x = -Math.PI / 2;
    this.honeyMat = new THREE.MeshPhysicalMaterial({ color: 0xffc24a, roughness: 0.03, transmission: 0.7, thickness: 0.03, ior: 1.5, clearcoat: 1,
      attenuationColor: new THREE.Color(0xe09000), attenuationDistance: 0.08, emissive: 0x2a1500 });
    this.stream = new THREE.Mesh(new THREE.BufferGeometry(), this.honeyMat);
    this.stream.frustumCulled = false;
    for (const o of [this.drops.mesh, this.mist.points, this.dust.points, this.bubbles.mesh, this.crumbs.mesh, this.chunks.mesh, ...this.ripples, this.ring, this.stream]) scene.add(o);
  }

  begin() {
    for (const p of [this.drops, this.mist, this.dust, this.bubbles, this.crumbs, this.chunks]) p.reset();
    this.ripples.forEach((r) => (r.visible = false));
    this.ring.visible = false;
    this.stream.visible = false;
    this.ri = 0;
    this.dustAge = Infinity;
  }

  end() {
    for (const p of [this.drops, this.bubbles, this.crumbs, this.chunks]) p.commit();
    this.mist.commit();
    this.dust.commit(this.dustAge < 1.2 ? 1 - this.dustAge / 1.2 : 0);
  }

  splash(at, age, size, seed) {
    if (age < 0 || age > 3.2) return;
    const r = mulberry32(seed * 7 + 1), count = Math.floor(220 * size);
    for (let i = 0; i < count; i++) {
      const a = r() * Math.PI * 2, sp = (0.6 + r() * 1.6) * size, up = (1.8 + r() * 2.6) * Math.sqrt(size), delay = r() * 0.12;
      const s = (0.004 + r() * 0.012) * Math.sqrt(size);
      const t = age - delay;
      if (t < 0) continue;
      const y = at[1] + up * t - G * t * t;
      if (y < at[1] - 0.02) continue;
      this.drops.add(at[0] + Math.cos(a) * sp * t * 0.6, y, at[2] + Math.sin(a) * sp * t * 0.6, s * (1 - t * 0.3));
    }
    for (let i = 0; i < 60 * size; i++) {
      const a = r() * Math.PI * 2, rad = r() * 0.5 * size * (0.4 + age), h = r() * 0.6 * size * Math.min(1, age * 3);
      if (age < 1.6) this.mist.add(at[0] + Math.cos(a) * rad, at[1] + h, at[2] + Math.sin(a) * rad);
    }
    for (let k = 0; k < 3 && this.ri < this.ripples.length; k++) {
      const ra = age - k * 0.25;
      if (ra < 0) continue;
      const rp = this.ripples[this.ri++];
      rp.visible = true;
      rp.position.set(at[0], 0.006, at[2]);
      rp.scale.setScalar(0.1 + ra * 0.9 * size);
      rp.material.opacity = 0.45 * Math.max(0, 1 - ra / 2.8);
    }
  }

  // droplets shed along a leap's path, falling since they left the fish
  drops_(path, u, age, seed) {
    const r = mulberry32(seed * 3 + 5);
    const at = (k) => [path.from[0] + (path.to[0] - path.from[0]) * k, path.from[1] + (path.to[1] - path.from[1]) * k + 4 * path.height * k * (1 - k), path.from[2] + (path.to[2] - path.from[2]) * k];
    for (let i = 0; i < 70; i++) {
      const k = (i / 70) * Math.min(u, 0.85), p = at(k), t = (u - k) * 2.2 + r() * 0.05;
      const y = p[1] - G * t * t;
      if (y < 0) continue;
      this.drops.add(p[0] + (r() - 0.5) * 0.1, y, p[2] + (r() - 0.5) * 0.1, 0.004 + r() * 0.006);
    }
  }

  dust_(at, age, size, seed) {
    if (age < 0 || age > 1.2) return;
    const r = mulberry32(seed * 11 + 3), d = 1 - Math.pow(1 - Math.min(1, age), 3);
    for (let i = 0; i < 60; i++) {
      const a = r() * Math.PI * 2, sp = (0.15 + r() * 0.5) * size;
      this.dust.add(at[0] + Math.cos(a) * sp * d, at[1] + 0.01 + r() * 0.06 * size * d, at[2] + Math.sin(a) * sp * d);
    }
    this.dustAge = Math.min(this.dustAge, age);
  }

  // crumbs fly from `from` and land on the heap of the container they fill
  crumbsFly(from, heap, base, age, colour, seed) {
    const r = mulberry32(seed);
    heap.forEach((c, i) => {
      const T = 0.35 + r() * 0.35, t = Math.min(age, T);
      const f = [base[0] + c.x, base[1] + c.y, base[2] + c.z];
      const vx = (f[0] - from[0]) / T, vz = (f[2] - from[2]) / T, vy = (f[1] - from[1] + G * T * T) / T;
      if (age < T) this.crumbs.add(from[0] + vx * t, from[1] + vy * t - G * t * t, from[2] + vz * t, c.s, c.rot + t * 9, shade(colour, c.shade));
    });
  }

  chunks_(at, age, colour, floorY, seed) {
    if (age < 0 || age > 2.5) return;
    const r = mulberry32(seed * 5 + 9);
    for (let i = 0; i < 36; i++) {
      const a = r() * Math.PI * 2, sp = 0.2 + r() * 0.45, up = 0.5 + r() * 1.1, s = 0.004 + r() * 0.007;
      const land = (up + Math.sqrt(up * up + 4 * G * Math.max(0, at[1] - floorY))) / (2 * G);
      const t = Math.min(age, land);
      const y = Math.max(floorY + 0.004, at[1] + up * t - G * t * t);
      this.chunks.add(at[0] + Math.cos(a) * sp * t, y, at[2] + Math.sin(a) * sp * t, s, a + Math.min(t, 0.6) * 8, shade(colour, r()));
    }
  }

  ring_(at, age) {
    if (age < 0 || age > 0.5) return;
    this.ring.visible = true;
    this.ring.position.set(at[0], at[1] + 0.005, at[2]);
    this.ring.scale.setScalar(0.1 + (1 - Math.pow(1 - age / 0.5, 3)) * 0.5);
    this.ring.material.opacity = 0.8 * (1 - age / 0.5);
  }

  honey(from, to, t, grow, thin, colour) {
    const T = this.T, sway = Math.sin(t * 5) * 0.003;
    const pts = [];
    for (let i = 0; i < 14; i++) {
      const k = (i / 13) * grow;
      pts.push(new T.Vector3(from[0] + (to[0] - from[0]) * k + sway * Math.sin(k * 3), from[1] + (to[1] - from[1]) * k, from[2] + (to[2] - from[2]) * k + sway * Math.cos(k * 3)));
    }
    this.stream.geometry.dispose();
    this.stream.geometry = new T.TubeGeometry(new T.CatmullRomCurve3(pts), 80, 0.0032 * (0.4 + 0.6 * thin), 10);
    this.honeyMat.color.set(colour);
    this.stream.visible = true;
  }

  bubbleTrail(from, to, p, dur, seed) {
    const r = mulberry32(seed * 13 + 1), t = p * dur;
    for (let i = 0; i < 110; i++) {
      const born = r() * dur, life = 2.2, age = t - born;
      const k = Math.min(1, born / dur), ease = 1 - Math.pow(1 - k, 3);
      const sx = from[0] + (to[0] - from[0]) * ease, sy = from[1] + (to[1] - from[1]) * ease, sz = from[2] + (to[2] - from[2]) * ease;
      const jx = (r() - 0.5) * 0.06, jz = (r() - 0.5) * 0.06, s = 0.006 + r() * 0.01;
      if (age < 0 || age > life) continue;
      this.bubbles.add(sx + jx + Math.sin(age * 6 + i) * 0.02, sy + age * 0.45, sz + jz, s);
    }
  }

  burstBubbles(at, age, seed) {
    if (age < 0 || age > 2.2) return;
    const r = mulberry32(seed * 17 + 2);
    for (let i = 0; i < 60; i++) {
      const spread = Math.min(1, age * 3);
      this.bubbles.add(at[0] + (r() - 0.5) * 0.4 * spread, at[1] + age * (0.5 + r() * 0.6), at[2] + (r() - 0.5) * 0.4 * spread, 0.008 + r() * 0.02);
    }
  }
}

function shade(hex, k) {
  const v = parseInt(hex.slice(1), 16), f = 0.7 + k * 0.45;
  const ch = (s) => Math.max(0, Math.min(255, Math.round(((v >> s) & 255) * f)));
  return "#" + [16, 8, 0].map((s) => ch(s).toString(16).padStart(2, "0")).join("");
}
