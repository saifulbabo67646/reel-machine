// Closed-form in t: a particle's position never depends on the previous frame.
import { mulberry32 } from "./math.mjs";

export function stream(seed, from, to, t, count = 90) {
  const rnd = mulberry32(seed), out = [];
  for (let i = 0; i < count; i++) {
    const phase = rnd(), jx = (rnd() - 0.5) * 0.025, jz = (rnd() - 0.5) * 0.025;
    const age = (t / 0.45 + phase) % 1;
    out.push([from[0] + (to[0] - from[0]) * age + jx, from[1] + (to[1] - from[1]) * age * age, from[2] + (to[2] - from[2]) * age + jz, 0.02, 1]);
  }
  return out;
}

export function grains(seed, origin, floorY, t, count = 70) {
  const rnd = mulberry32(seed), out = [];
  for (let i = 0; i < count; i++) {
    const phase = rnd(), r = rnd() * 0.12, a = rnd() * Math.PI * 2;
    const age = (t / 0.7 + phase) % 1;
    out.push([origin[0] + Math.cos(a) * r * age, origin[1] - (origin[1] - floorY) * age * age, origin[2] + Math.sin(a) * r * age, 0.014, 1]);
  }
  return out;
}

export function steam(seed, origin, t, intensity, count = 40) {
  const rnd = mulberry32(seed), out = [];
  for (let i = 0; i < count; i++) {
    const phase = rnd(), jx = (rnd() - 0.5) * 0.15, jz = (rnd() - 0.5) * 0.15;
    const age = (t / 2.2 + phase) % 1;
    out.push([origin[0] + jx + Math.sin((age * 3 + phase) * 6.283) * 0.04, origin[1] + age * 0.9 * intensity, origin[2] + jz, 0.03 + age * 0.09, (1 - age) * 0.6]);
  }
  return out;
}
