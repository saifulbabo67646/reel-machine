// Shapes built from parameters. Origin = bottom centre; bbox = declared dims (checked by tests).
export const KINDS = ["bowl", "cup", "pot", "jar", "bottle", "ball", "box", "sack", "pile", "plinth"];
const MATERIALS = {
  stone: { colour: "#8f877c", roughness: 0.95 }, wood: { colour: "#8a5a33", roughness: 0.8 },
  ceramic: { colour: "#c9b8a3", roughness: 0.5 }, metal: { colour: "#9aa3ab", roughness: 0.35, metalness: 0.9 },
  glass: { colour: "#cfe8ef", roughness: 0.1, opacity: 0.5 }, dough: { colour: "#e2c35a", roughness: 0.85 },
  salt: { colour: "#f4f4f0", roughness: 0.9 }, grain: { colour: "#c79a4a", roughness: 0.9 },
  water: { colour: "#5aa6c8", roughness: 0.2, opacity: 0.8 }, cloth: { colour: "#c8b48a", roughness: 1.0 },
};
export const SURFACES = { liquid: 0.15, paste: 0.7, grain: 0.95, dough: 0.85 };
// outer profiles as [r/w, y/h]; containers continue down their inner wall to the floor
const PROFILES = {
  bowl: [[0, 0], [0.3, 0], [0.42, 0.35], [0.5, 1], [0.47, 1], [0.4, 0.4], [0.26, 0.15], [0, 0.15]],
  cup: [[0, 0], [0.4, 0], [0.5, 1], [0.45, 1], [0.36, 0.15], [0, 0.15]],
  pot: [[0, 0], [0.45, 0], [0.5, 0.15], [0.5, 1], [0.46, 1], [0.46, 0.15], [0, 0.15]],
  jar: [[0, 0], [0.45, 0], [0.5, 0.2], [0.5, 0.8], [0.35, 0.92], [0.35, 1], [0.31, 1], [0.31, 0.92], [0.46, 0.8], [0.46, 0.2], [0, 0.15]],
  bottle: [[0, 0], [0.5, 0], [0.5, 0.62], [0.18, 0.8], [0.15, 0.94], [0, 0.94]],
  sack: [[0, 0], [0.42, 0], [0.5, 0.3], [0.48, 0.75], [0.3, 0.92], [0.12, 1], [0, 1]],
  pile: [[0, 0], [0.5, 0], [0.3, 0.45], [0, 1]],
};
const INNER = {
  bowl: [[0.15, 0.26], [0.4, 0.4], [1, 0.47]], cup: [[0.15, 0.36], [1, 0.45]],
  pot: [[0.15, 0.46], [1, 0.46]], jar: [[0.15, 0.46], [0.8, 0.46], [0.92, 0.31], [1, 0.31]],
};

export function innerRadius(kind, w, h, y) {
  const table = INNER[kind], f = y / h;
  if (f <= table[0][0]) return table[0][1] * w;
  for (let i = 1; i < table.length; i++) {
    const [y0, r0] = table[i - 1], [y1, r1] = table[i];
    if (f <= y1) return (r0 + ((f - y0) / (y1 - y0)) * (r1 - r0)) * w;
  }
  return table[table.length - 1][1] * w;
}

export function material(THREE, name, colour) {
  const m = MATERIALS[name];
  return new THREE.MeshStandardMaterial({
    color: new THREE.Color(colour || m.colour), roughness: m.roughness, metalness: m.metalness || 0,
    transparent: m.opacity !== undefined, opacity: m.opacity ?? 1,
  });
}

export function buildShape(THREE, spec, dims) {
  const [w, h, d] = dims, group = new THREE.Group(), mat = material(THREE, spec.material, spec.colour);
  let mesh;
  if (spec.shape === "ball") {
    mesh = new THREE.Mesh(new THREE.SphereGeometry(0.5, 40, 24), mat);
    mesh.scale.set(w, h, d);
    mesh.position.y = h / 2;
  } else if (spec.shape === "box" || spec.shape === "plinth") {
    mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
    mesh.position.y = h / 2;
  } else {
    const points = PROFILES[spec.shape].map(([r, y]) => new THREE.Vector2(r * w, y * h));
    mesh = new THREE.Mesh(new THREE.LatheGeometry(points, 48), mat);
    mesh.material.side = THREE.DoubleSide;
    if (spec.shape === "bottle") {
      const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.16 * w, 0.16 * w, 0.06 * h, 24), material(THREE, "metal", "#c0392b"));
      cap.position.y = 0.97 * h;
      group.add(cap);
    }
  }
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  group.add(mesh);
  let fill = null;
  if (spec.shape in INNER) {
    fill = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 0.006, 48), new THREE.MeshStandardMaterial({ color: 0xd9d27a, roughness: 0.15 }));
    // tiny and on the floor until the first frame sizes it, so it never widens the bbox
    fill.scale.set(1e-3, 1, 1e-3);
    fill.position.y = 0.15 * h;
    fill.visible = false;
    group.add(fill);
  }
  return { group, fill, innerRadius: spec.shape in INNER ? (y) => innerRadius(spec.shape, w, h, y) : null };
}

// An object the agent built from parts. Each part is a unit primitive scaled to its own box
// (`size`), turned by `rotate` (degrees, XYZ) and centred on `at`; the whole is then moved so
// its bounding box sits on its origin, like every other actor.
const UNIT = {
  box: (T) => [new T.BoxGeometry(1, 1, 1), [1, 1, 1]],
  cylinder: (T) => [new T.CylinderGeometry(0.5, 0.5, 1, 40), [1, 1, 1]],
  sphere: (T) => [new T.SphereGeometry(0.5, 40, 24), [1, 1, 1]],
  cone: (T) => [new T.ConeGeometry(0.5, 1, 40), [1, 1, 1]],
  torus: (T) => [new T.TorusGeometry(0.375, 0.125, 20, 64), [1, 1, 0.25]],
  capsule: (T) => [new T.CapsuleGeometry(0.25, 0.5, 8, 24), [0.5, 1, 0.5]],
  plane: (T) => [new T.PlaneGeometry(1, 1), [1, 1, 1]],
};

export function buildCompound(THREE, spec) {
  const inner = new THREE.Group();
  for (const part of spec.parts) {
    const [geometry, unit] = UNIT[part.shape](THREE);
    const mat = material(THREE, part.material, part.colour);
    if (part.shape === "plane") mat.side = THREE.DoubleSide;
    if (part.glow > 0) {
      mat.emissive = new THREE.Color(part.colour || MATERIALS[part.material].colour);
      mat.emissiveIntensity = part.glow;
    }
    const mesh = new THREE.Mesh(geometry, mat);
    mesh.scale.set(part.size[0] / unit[0], part.size[1] / unit[1], part.size[2] / unit[2]);
    mesh.rotation.set(...part.rotate.map((d) => (d * Math.PI) / 180));
    mesh.position.set(...part.at);
    mesh.castShadow = mesh.receiveShadow = true;
    inner.add(mesh);
  }
  const box = new THREE.Box3().setFromObject(inner), c = box.getCenter(new THREE.Vector3());
  inner.position.set(-c.x, -box.min.y, -c.z);
  const group = new THREE.Group();
  group.add(inner);
  return group;
}
