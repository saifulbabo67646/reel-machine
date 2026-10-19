// reelmachine/recipes/techreel/runtime/pure.mjs
// Everything a frame depends on, as pure functions of time (ms). No DOM, no clock, no unseeded randomness:
// the same time always gives the same frame, which is what lets HyperFrames seek anywhere.

export const ENTER_MS = 260;
export const EXIT_MS = 200;
export const GLIDE_MS = 300;
export const COUNTER_MS = 900;
export const HERO_MOVE_MS = 450;
export const TYPE_CPS = 28;
export const STAGE = { top: 440, bottom: 1380, left: 64, right: 1016 };
export const STACK_GAP = 32;
export const MIN_SCALE = 0.6;
export const CAPTION_HOLD_MS = 300;

// hero states that take a slot in the stage stack; `corner` and `hidden` float outside it
export const HERO_STACKING = new Set(["center", "small-top", "pulse", "shake", "rings", "screen", "sleep"]);

// a beat's transition length; the schedule's gap before a beat is at least this long (a cut's gap is silence)
const TRANSITION_LEN = { cut: 0, slide: 250, flash: 200, zoom: 300 };
const TERM_CMD_GAP_MS = 250;
// floor() guard: (1000 / 28) * 2 ms must type exactly two characters despite float rounding
const EPS = 1e-9;

export const clamp01 = (x) => (x <= 0 ? 0 : x >= 1 ? 1 : x);
const lerp = (a, b, p) => a + (b - a) * p;
const easeOutCubic = (p) => 1 - Math.pow(1 - p, 3);

/** Back-out easing with the usual 1.70158 overshoot. */
export const backOut = (p) => {
  const c1 = 1.70158, c3 = c1 + 1;
  return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2);
};

// ------------------------------------------------------------ visual lifetimes

const enterOf = (v) => v.enterMs ?? -Infinity;
const endOf = (v) => (v.exitMs ?? Infinity) + EXIT_MS;

export const enterP = (v, t) => clamp01((t - v.enterMs) / ENTER_MS);
export const exitP = (v, t) => clamp01((t - v.exitMs) / EXIT_MS);

/** On the page from its enter until its exit animation has played out. */
export const alive = (v, t) => enterOf(v) <= t && t < endOf(v);

// ------------------------------------------------------------ the stage stack

/** Rows of the live blocks in enter order: a `left`/`right` block pairs with the next one when that is also
    left/right, the `left` one on the left. */
// edge cases: two `left`s (or two `right`s) still pair, in enter order; a side block whose neighbour is a stack
// block (or none) gets a row of its own, centred; three side blocks in a row pair the first two only
function rows(live) {
  const out = [];
  for (let i = 0; i < live.length; i++) {
    const a = live[i], b = live[i + 1];
    const side = (v) => v.place === "left" || v.place === "right";
    if (side(a) && b && side(b)) {
      out.push(a.place === "right" && b.place === "left" ? [b, a] : [a, b]);
      i++;
    } else out.push([a]);
  }
  return out;
}

/** Where each live block sits with no glide: `{x, y}` is the scaled box's top-left in frame px (the page
    scales from the top-left corner). The stack is centred in the stage; once even MIN_SCALE leaves it too
    tall it is pinned to the stage bottom instead, so the newest block stays whole and the oldest overflows
    up toward the heading. */
function layoutOf(live, sizes) {
  const size = (v) => sizes.get(v.id) || { w: 0, h: 0 };
  const rs = rows(live).map((items) => ({
    items,
    w: items.reduce((s, v) => s + size(v).w, 0) + STACK_GAP * (items.length - 1),
    h: Math.max(...items.map((v) => size(v).h)),
  }));
  const out = new Map();
  if (!rs.length) return out;
  const needed = rs.reduce((s, r) => s + r.h, 0) + STACK_GAP * (rs.length - 1);
  const room = STAGE.bottom - STAGE.top;
  // a row wider than the stage (a left/right pair) shrinks the stack too, below MIN_SCALE if it must:
  // nothing may cross the side margins
  const widest = Math.max(...rs.map((r) => r.w));
  const fitW = widest > STAGE.right - STAGE.left ? (STAGE.right - STAGE.left) / widest : 1;
  const scale = Math.min(fitW, needed > room ? Math.max(MIN_SCALE, room / needed) : 1);
  const height = needed * scale;
  const centre = (STAGE.left + STAGE.right) / 2;
  // rows are placed by their unscaled offset into the stack; a pinned stack measures from the bottom so the
  // last block's bottom lands on STAGE.bottom exactly, without float drift
  const yAt = height <= room
    ? (offset) => STAGE.top + (room - height) / 2 + offset * scale
    : (offset) => STAGE.bottom - (needed - offset) * scale;
  let offset = 0;
  for (const r of rs) {
    let x = centre - (r.w * scale) / 2;
    for (const v of r.items) {
      const s = size(v);
      out.set(v.id, { x, y: yAt(offset + (r.h - s.h) / 2), scale });
      x += (s.w + STACK_GAP) * scale;
    }
    offset += r.h + STACK_GAP;
  }
  return out;
}

/** The stage stack at `t`. `blocks` are the current beat's block visuals (plus `{id: "hero", ...heroSpan()}`
    while the hero is in a HERO_STACKING state); `sizes` their natural `{w, h}` measured at build.
    Glide rule: membership changes when a block enters (`enterMs`) or has finished leaving (`exitMs + EXIT_MS`).
    At each change every block that stays glides over GLIDE_MS (ease-out: most of the way in the first third, so
    a newcomer, which starts in its slot under its own enter animation, is not drawn over a block still making
    room for it — the orb sat on a counter's digits with a smoothstep glide). Walking
    the changes in order makes a change during a glide start from the half-glided place, so nothing jumps. */
export function stackLayout(blocks, sizes, t) {
  // the hero always takes the top row, whenever it joined
  const rank = (v) => (v.id === "hero" ? -Infinity : enterOf(v));
  const order = blocks.map((v, i) => ({ v, i }))
    .sort((a, b) => rank(a.v) - rank(b.v) || a.i - b.i)
    .map((e) => e.v);
  const changes = [...new Set(order.flatMap((v) => [enterOf(v), endOf(v)]))]
    .filter((c) => Number.isFinite(c) && c <= t)
    .sort((a, b) => a - b);
  const liveAt = (c) => order.filter((v) => alive(v, c));
  const blend = (from, to, p) => {
    const out = new Map();
    for (const [id, end] of to) {
      const start = from.get(id);
      out.set(id, start && p < 1
        ? { x: lerp(start.x, end.x, p), y: lerp(start.y, end.y, p), scale: lerp(start.scale, end.scale, p) }
        : end);
    }
    return out;
  };
  // blocks with no times (the hero) are live before any change
  let from = new Map(), to = layoutOf(liveAt(-Infinity), sizes), since = -Infinity;
  for (const c of changes) {
    from = blend(from, to, easeOutCubic(clamp01((c - since) / GLIDE_MS)));
    to = layoutOf(liveAt(c), sizes);
    since = c;
  }
  return blend(from, to, easeOutCubic(clamp01((t - since) / GLIDE_MS)));
}

// ------------------------------------------------------------ counters and typing

const group = (digits) => digits.replace(/\B(?=(\d{3})+(?!\d))/g, " ");

/** A counter's text at `t`: `from` → `to` over COUNTER_MS from its enter, ease-out cubic, with prefix/suffix.
    Thin-space grouping only for counters reaching 10 000, so a `to: 19.9, suffix: "K"` stays "19.9K". */
export function counterValue(v, t) {
  const from = v.from ?? 0, to = v.to ?? 0, decimals = v.decimals ?? 0;
  const p = clamp01((t - (v.enterMs ?? 0)) / COUNTER_MS);
  const value = p >= 1 ? to : lerp(from, to, easeOutCubic(p));
  let text = Math.abs(value).toFixed(decimals);
  if (Math.abs(to) >= 10000) {
    const [whole, frac] = text.split(".");
    text = group(whole) + (frac !== undefined ? "." + frac : "");
  }
  const sign = value < 0 && Number(text.replace(/ /g, "")) !== 0 ? "-" : "";
  return `${v.prefix ?? ""}${sign}${text}${v.suffix ?? ""}`;
}

/** `text` as `{prefix, to, decimals, suffix}` for counterValue when it is one number (at most 2 decimals) with
    non-digit text round it, and counterValue's final text gives it back exactly; else null (the caller slams the
    text instead of rolling it, so "2,000" or "free" are never rewritten). */
export function numberParts(text) {
  const m = /^(\D*?)(-?\d+(?:\.\d{1,2})?)(.*)$/su.exec(String(text ?? ""));
  // a separator then more digits after the number ("2,000", "12 000", "1.234") is not one number
  if (!m || /^[.,\s]?\d/u.test(m[3])) return null;
  const decimals = m[2].includes(".") ? m[2].split(".")[1].length : 0;
  const parts = { prefix: m[1], to: Number(m[2]), decimals, suffix: m[3] };
  return counterValue({ ...parts, enterMs: 0 }, Infinity) === text ? parts : null;
}

/** Item `i` of a staggered run, 0..1: it starts `gapMs` after item i-1 (item 0 at `enterMs`) and takes `durMs`. */
export function staggerP(i, enterMs, t, gapMs, durMs) {
  const start = enterMs + i * gapMs;
  if (durMs <= 0) return t >= start ? 1 : 0;
  return clamp01((t - start) / durMs);
}

/** Which of two score values wins: the higher number; a tie, or a side that is not a number, goes right. */
export function winner(left, right) {
  const num = (text) => {
    const m = /-?\d+(?:\.\d+)?/.exec(String(text ?? "").replace(/(\d)[,\s](?=\d{3}\b)/g, "$1"));
    return m ? Number(m[0]) : null;
  };
  const a = num(left), b = num(right);
  return a !== null && b !== null && a > b ? "left" : "right";
}

/** The first floor((t - startMs) / 1000 * TYPE_CPS) characters of `text` (code points), all of it after. */
export function typed(text, startMs, t) {
  const chars = [...(text ?? "")];
  const n = Math.floor(((t - startMs) / 1000) * TYPE_CPS + EPS);
  return n <= 0 ? "" : chars.slice(0, n).join("");
}

/** When each terminal line appears. The first command starts typing at the visual's enter; each later one
    250 ms after the previous command finished typing; an `out` line appears the moment its preceding command
    finished typing (at the enter when no command precedes it). Lengths count code points, as Python's len(). */
export function terminalStarts(v) {
  const out = [];
  let typedAt = null; // when the latest command finished typing
  for (const line of v.lines || []) {
    if (line.cmd != null) {
      const start = typedAt == null ? v.enterMs : typedAt + TERM_CMD_GAP_MS;
      typedAt = start + ([...line.cmd].length * 1000) / TYPE_CPS;
      out.push(start);
    } else out.push(typedAt ?? v.enterMs);
  }
  return out;
}

// ------------------------------------------------------------ captions, hero, beats

/** The latest caption started by `t`, held across the short gaps between chunks (up to CAPTION_HOLD_MS past
    its end, so no caption hangs over a beat transition or a pause); -1 before the first, once the hold has run
    out, and from the last one's end. */
export function captionAt(captions, t) {
  let index = -1;
  for (let i = 0; i < captions.length; i++) if (captions[i].startMs <= t) index = i;
  if (index < 0) return -1;
  const end = captions[index].endMs;
  if (index === captions.length - 1 ? t >= end : t > end + CAPTION_HOLD_MS) return -1;
  return index;
}

export const wordPhase = (w, t) => (t < w.startMs ? "future" : t < w.endMs ? "now" : "past");

/** The hero's latest state by `t`, the state before it as `from` (null for the first) and the move's
    progress over HERO_MOVE_MS; null without states. */
export function heroAt(states, t) {
  if (!states || !states.length) return null;
  let i = 0;
  for (let k = 0; k < states.length; k++) if (states[k].atMs <= t) i = k;
  const s = states[i];
  return { state: s.state, from: i > 0 ? states[i - 1].state : null, p: clamp01((t - s.atMs) / HERO_MOVE_MS), text: s.text ?? null };
}

/** The hero as a stack block at `t`: `{id: "hero", enterMs, exitMs}` for the run of HERO_STACKING states the
    latest one started by `t` belongs to — entering when the run starts (no time when that is the first state,
    so it is in the stack from the start) and leaving when a non-stacking state follows it. null when no run
    has started yet; a run that has ended is still returned, so the stack sees the hero leave and glides. */
export function heroSpan(states, t) {
  if (!states || !states.length) return null;
  let last = -1;
  for (let k = 0; k < states.length; k++) if (states[k].atMs <= t && HERO_STACKING.has(states[k].state)) last = k;
  if (last < 0) return null;
  let first = last;
  while (first > 0 && HERO_STACKING.has(states[first - 1].state)) first--;
  const after = states.slice(last + 1).find((s) => !HERO_STACKING.has(s.state));
  const span = { id: "hero" };
  if (first > 0) span.enterMs = states[first].atMs;
  if (after) span.exitMs = after.atMs;
  return span;
}

const transitionLen = (beat) => TRANSITION_LEN[beat.transition] ?? 0;

/** The current beat: a beat takes over when its transition starts, which runs across the gap before its
    startMs (a cut's at startMs itself). Before the first beat, 0. */
export function beatAt(beats, t) {
  let index = 0;
  for (let i = 0; i < beats.length; i++) if (beats[i].startMs - transitionLen(beats[i]) <= t) index = i;
  return index;
}

/** Beat `i`'s transition in, 0..1, ending at its startMs; a cut (and the first beat) is always 1. */
export function transitionP(beats, i, t) {
  const len = transitionLen(beats[i]);
  if (i === 0 || len <= 0) return 1;
  return clamp01((t - (beats[i].startMs - len)) / len);
}

/** `durationMs` in ms; the document's `duration` is in seconds. */
export const progress = (t, durationMs) => (durationMs > 0 ? clamp01(t / durationMs) : 0);

// ------------------------------------------------------------ seeded background

function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A stable 32-bit seed from a string (FNV-1a). */
export function seedOf(text) {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193);
  return h >>> 0;
}

/** Glow blob `i`'s centre and radius in frame px: a slow Lissajous drift, each axis with its own 9–14 s
    period, phase and home drawn from `seed`. */
export function blob(i, t, seed) {
  const rand = mulberry32((seed ^ Math.imul(i + 1, 0x9e3779b1)) >>> 0);
  const px = 9000 + rand() * 5000, py = 9000 + rand() * 5000;
  const phx = rand() * 2 * Math.PI, phy = rand() * 2 * Math.PI;
  const cx = 1080 * (0.2 + 0.6 * rand()), cy = 1920 * (0.15 + 0.7 * rand());
  const ax = 80 + 60 * rand(), ay = 80 + 60 * rand();
  const r0 = 380 + 180 * rand();
  return {
    x: cx + ax * Math.sin((2 * Math.PI * t) / px + phx),
    y: cy + ay * Math.sin((2 * Math.PI * t) / py + phy),
    r: r0 * (1 + 0.05 * Math.sin((2 * Math.PI * t) / (px + py) + phx)),
  };
}
