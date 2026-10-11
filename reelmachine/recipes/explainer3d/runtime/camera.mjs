// Camera moves — mirrors camera.py.
import { lerp3 } from "./math.mjs";

export function ease(p, easeIn, easeOut) {
  p = Math.min(1, Math.max(0, p));
  if (easeIn && easeOut) return p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
  if (easeIn) return 1 - Math.cos((p * Math.PI) / 2);
  if (easeOut) return Math.sin((p * Math.PI) / 2);
  return p;
}

export function cameraPose(move, params, start, target, p) {
  const sp = start.position, st = start.target;
  switch (move) {
    case "static":
      return { position: [...sp], target: [...st], fov: start.fov };
    case "orbit": {
      const dx = sp[0] - target[0], dy = sp[1] - target[1], dz = sp[2] - target[2];
      const a0 = Math.atan2(dx, dz), r = Math.hypot(dx, dz), a1 = (params.to * Math.PI) / 180;
      const a = a0 + (a1 - a0) * p;
      return { position: [target[0] + r * Math.sin(a), target[1] + dy, target[2] + r * Math.cos(a)], target: lerp3(st, target, p), fov: start.fov };
    }
    case "push_in": {
      const k = 1 + (params.factor - 1) * p;
      return { position: [0, 1, 2].map((i) => target[i] + (sp[i] - target[i]) * k), target: lerp3(st, target, p), fov: start.fov };
    }
    case "dolly": {
      const o = params.offset;
      return { position: [0, 1, 2].map((i) => sp[i] + o[i] * p), target: [0, 1, 2].map((i) => st[i] + o[i] * p), fov: start.fov };
    }
    default:
      throw new Error(`unknown camera move ${move}`);
  }
}
