// reelmachine/recipes/mapreel/runtime/pure.mjs
// Pure timing and camera maths for a map segment: no DOM, so Node can test it.
// Frames are {x, y, s}: the Mercator point at screen centre and px per Mercator unit.

const RHO = Math.SQRT2;
const W = 1080;

export const clamp01 = (t) => Math.max(0, Math.min(1, t));
export const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
export const backOut = (t) => {
  const c1 = 1.70158, c3 = c1 + 1;
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
};

// van Wijk & Nuij, "Smooth and efficient zooming and panning" (the d3.interpolateZoom path)
export function zoomPath(a, b) {
  const w0 = W / a.s, w1 = W / b.s;
  const dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy, d1 = Math.sqrt(d2);
  if (d1 < 1e-12) {
    const S = Math.log(w1 / w0) / RHO;
    return (t) => ({ x: a.x + t * dx, y: a.y + t * dy, s: W / (w0 * Math.exp(RHO * t * S)) });
  }
  const b0 = (w1 * w1 - w0 * w0 + RHO ** 4 * d2) / (2 * w0 * RHO * RHO * d1);
  const b1 = (w1 * w1 - w0 * w0 - RHO ** 4 * d2) / (2 * w1 * RHO * RHO * d1);
  const r0 = Math.log(Math.sqrt(b0 * b0 + 1) - b0);
  const r1 = Math.log(Math.sqrt(b1 * b1 + 1) - b1);
  const S = (r1 - r0) / RHO;
  return (t) => {
    if (t >= 1) return { x: b.x, y: b.y, s: b.s };
    const s = t * S;
    const coshr0 = Math.cosh(r0);
    const u = (w0 / (RHO * RHO * d1)) * (coshr0 * Math.tanh(RHO * s + r0) - Math.sinh(r0));
    const w = (w0 * coshr0) / Math.cosh(RHO * s + r0);
    return { x: a.x + u * dx, y: a.y + u * dy, s: W / w };
  };
}

export function cameraAt(camera, ms) {
  const { from, to, flyMs, drift } = camera;
  if (!flyMs || ms >= flyMs) {
    const after = Math.max(0, ms - (flyMs || 0));
    const grow = 1 + drift * clamp01(after / 6000);
    return { x: to.x, y: to.y, s: to.s * grow };
  }
  if (ms <= 0) return { x: from.x, y: from.y, s: from.s };
  return zoomPath(from, to)(easeInOut(ms / flyMs));
}

const POP_MS = 350;
export function popState(atMs, ms) {
  if (ms < atMs) return { visible: false, scale: 0, alpha: 0, blur: 0 };
  const p = clamp01((ms - atMs) / POP_MS);
  return { visible: true, scale: p >= 1 ? 1 : backOut(p), alpha: clamp01(p * 2), blur: p >= 1 ? 0 : (1 - p) * 10 };
}

export function fillAlpha(fillMs, ms) {
  return clamp01((ms - fillMs) / 400);
}

const HOLD_S = 0.15;
export function activeWord(words, seconds) {
  for (let i = words.length - 1; i >= 0; i--) {
    const w = words[i];
    const next = words[i + 1];
    const end = next ? Math.min(w.end + HOLD_S, next.start) : w.end + HOLD_S;
    if (seconds >= w.start && seconds < end) return i;
  }
  return -1;
}

// A font size that keeps text measured at `basePx` within `maxWidth` (never larger than base).
export function fitFontPx(basePx, measuredWidth, maxWidth) {
  if (!(measuredWidth > maxWidth)) return basePx;
  return Math.max(1, Math.floor((basePx * maxWidth) / measuredWidth));
}

// A text badge's lines: a long multi-word name breaks at the space nearest its middle, so it
// can be drawn twice as large ("Comme des Garçons" -> "Comme des" / "Garçons").
export function badgeLines(name) {
  const text = String(name || "");
  if (text.length <= 8) return [text];
  let best = -1;
  for (let i = 0; i < text.length; i++) {
    if (text[i] === " " && (best < 0 || Math.abs(i - text.length / 2) < Math.abs(best - text.length / 2))) best = i;
  }
  return best < 0 ? [text] : [text.slice(0, best), text.slice(best + 1)];
}
