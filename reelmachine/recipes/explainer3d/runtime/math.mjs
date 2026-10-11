// Pure helpers. Nothing in the runtime reads a clock: every value is a function of p or t.
export const clamp01 = (x) => Math.min(1, Math.max(0, x));
export const lerp = (a, b, t) => a + (b - a) * t;
export const lerp3 = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
export const add3 = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const easeInCubic = (p) => p * p * p;
export const easeOutCubic = (p) => 1 - Math.pow(1 - p, 3);
export const easeInOutCubic = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
// back ease with c1 = 0.8: overshoots by ~4% before settling exactly on 1
export const easeOutBack = (p) => {
  const c1 = 0.8, c3 = c1 + 1;
  return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2);
};
// a critically damped spring, normalised so it lands exactly on 1 at p = 1
export function spring(p, omega = 8) {
  const raw = (x) => 1 - (1 + omega * x) * Math.exp(-omega * x);
  return raw(clamp01(p)) / raw(1);
}
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rgb = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
export function mixHex(a, b, t) {
  const x = rgb(a), y = rgb(b);
  return "#" + [0, 1, 2]
    .map((i) => Math.max(0, Math.min(255, Math.floor(x[i] + (y[i] - x[i]) * t + 0.5))).toString(16).padStart(2, "0"))
    .join("");
}
export const FLOOR = 0.15;
export const fillY = (h, level) => h * (FLOOR + (1 - FLOOR) * 0.9 * level);
