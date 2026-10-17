// reelmachine/recipes/mapreel/runtime/pure.mjs
// Pure timing and camera maths for a map segment: no DOM, so Node can test it.
// Frames are {x, y, s}: the Mercator point at screen centre and px per Mercator unit.

const RHO = Math.SQRT2;
const W = 1080, H = 1920;

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

export const POP_MS = 350;
export function popState(atMs, ms) {
  if (ms < atMs) return { visible: false, scale: 0, alpha: 0, blur: 0 };
  const p = clamp01((ms - atMs) / POP_MS);
  return { visible: true, scale: p >= 1 ? 1 : backOut(p), alpha: clamp01(p * 2), blur: p >= 1 ? 0 : (1 - p) * 10 };
}

// A stat's text part way through its count-up (p in 0..1, eased out): the number grows from zero with
// its decimals and comma grouping, inside its prefix and suffix. No number (`count` null): the raw value.
export function countText(count, p, raw = "") {
  if (!count) return raw;
  const t = 1 - Math.pow(1 - clamp01(p), 3);
  let [whole, fraction] = (count.number * t).toFixed(count.decimals).split(".");
  if (count.comma) whole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return count.prefix + whole + (fraction === undefined ? "" : "." + fraction) + count.suffix;
}

// The stat card sits right of the rank badge (which ends at x 315): centred in x 330..1048, never wider.
// valueW / labelW are measured at the base fonts (104 / 40 px); each font shrinks until its text fits.
export const CARD_X0 = 330, CARD_X1 = 1048, CARD_PAD = 40;
// x0 / x1: the span the card is centred in and never wider than (a versus panel passes its own).
export function cardLayout(valueW, labelW, x0 = CARD_X0, x1 = CARD_X1) {
  const room = x1 - x0 - 2 * CARD_PAD;
  const valuePx = fitFontPx(104, valueW, room), labelPx = fitFontPx(40, labelW, room);
  const width = Math.min(x1 - x0, Math.max(valueW * valuePx / 104, labelW * labelPx / 40) + 2 * CARD_PAD);
  return { cx: (x0 + x1) / 2, width, valuePx, labelPx };
}

// A versus panel's horizontal offset: a whole screen off to its side (dir -1 left, +1 right) until enterMs,
// sliding home over SLIDE_MS.
export const SLIDE_MS = 350;
export function slideOffset(enterMs, ms, dir) {
  return dir * W * (1 - easeInOut(clamp01((ms - enterMs) / SLIDE_MS)));
}

// A place's on-screen bounding box under a camera: {x0, y0, x1, y1, w, h} in px. `view` is the camera's
// screen centre: the screen's by default, a panel's in a versus.
export function ringsBox(rings, cam, view = { cx: W / 2, cy: H / 2 }) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const ring of rings) {
    for (const [x, y] of ring) {
      x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
    }
  }
  const px0 = (x0 - cam.x) * cam.s + view.cx, px1 = (x1 - cam.x) * cam.s + view.cx;
  const py0 = (y0 - cam.y) * cam.s + view.cy, py1 = (y1 - cam.y) * cam.s + view.cy;
  return { x0: px0, y0: py0, x1: px1, y1: py1, w: px1 - px0, h: py1 - py0 };
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

// Where to centre a ghosted name so its rotated box stays on screen for the whole segment while it
// drifts right by `driftPx`. Returns the centre at the segment's start.
export function ghostCentre(px, py, textW, textH, angle, driftPx, W, H, margin = 8) {
  const c = Math.abs(Math.cos(angle)), s = Math.abs(Math.sin(angle));
  const hx = (c * textW) / 2 + (s * textH) / 2;
  const hy = (s * textW) / 2 + (c * textH) / 2;
  const fit = (v, lo, hi) => (lo > hi ? (lo + hi) / 2 : Math.max(lo, Math.min(hi, v)));
  return { x: fit(px, margin + hx, W - margin - hx - driftPx), y: fit(py, margin + hy, H - margin - hy) };
}

// Where to centre a region's name tag (w x h px, upright, still): on screen, and moved to the nearer side of
// the caption `band` ({y0, y1}) when it would sit on it.
export function tagCentre(px, py, w, h, W, H, band = null, margin = 8) {
  const at = ghostCentre(px, py, w, h, 0, 0, W, H, margin);
  if (band && at.y + h / 2 > band.y0 && at.y - h / 2 < band.y1) {
    const above = band.y0 - h / 2, below = band.y1 + h / 2;
    at.y = Math.abs(at.y - above) <= Math.abs(at.y - below) ? above : below;
  }
  return at;
}
