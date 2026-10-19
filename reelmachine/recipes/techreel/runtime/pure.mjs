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
export const HERO_STACKING = new Set(["center", "small-top", "pulse", "shake", "rings", "screen", "sleep", "locked", "split"]);
const BLINK_MS = 180;

// a beat's transition length; the schedule's gap before a beat is at least this long (a cut's gap is silence)
const TRANSITION_LEN = { cut: 0, slide: 250, flash: 200, zoom: 300 };
const TERM_CMD_GAP_MS = 250;
// floor() guard: (1000 / 28) * 2 ms must type exactly two characters despite float rounding
const EPS = 1e-9;

const UNSPACED = new Set(["ja", "zh", "th"]);

/** Whether a language writes without spaces between words (markup.is_unspaced's mirror): its heading and
    caption units are characters, set with no gap between them. */
export const isUnspaced = (language) => UNSPACED.has(String(language || "").split("-")[0].toLowerCase());

const PUNCT = /^\p{P}$/u;

/** `*accent*` text as words (markup.units' spaced-script rule, for the text visuals carry raw): `*` pairs toggle
    the accent and a lone trailing `*` stays literal. A word accented in every letter (punctuation outside the
    stars aside) is `a: true`; a partly accented one keeps `accentSpans`, [start, end) code-point offsets. */
export function accentUnits(text) {
  const chars = Array.from(String(text || ""));
  const stars = chars.flatMap((ch, i) => (ch === "*" ? [i] : []));
  const paired = new Set(stars.slice(0, stars.length - (stars.length % 2)));
  const out = [];
  let on = false, word = [];
  const flush = () => {
    if (!word.length) return;
    const t = word.map(([ch]) => ch).join("");
    const letters = word.filter(([ch]) => !PUNCT.test(ch));
    if ((letters.length ? letters : word).every(([, a]) => a)) out.push({ t, a: true });
    else {
      const spans = [];
      word.forEach(([, a], i) => {
        if (!a) return;
        if (spans.length && spans[spans.length - 1][1] === i) spans[spans.length - 1][1] = i + 1;
        else spans.push([i, i + 1]);
      });
      out.push(spans.length ? { t, a: false, accentSpans: spans } : { t, a: false });
    }
    word = [];
  };
  chars.forEach((ch, i) => {
    if (paired.has(i)) on = !on;
    else if (/\s/.test(ch)) flush();
    else word.push([ch, on]);
  });
  flush();
  return out;
}

/** A word's text as runs to colour: [{t, a}], the accented spans (code-point offsets) as `a: true`. */
export function accentPieces(word) {
  if (!word.accentSpans || !word.accentSpans.length) return [{ t: word.t, a: !!word.a }];
  const chars = Array.from(word.t);
  const out = [];
  let at = 0;
  for (const [start, end] of word.accentSpans) {
    if (start > at) out.push({ t: chars.slice(at, start).join(""), a: false });
    out.push({ t: chars.slice(start, end).join(""), a: true });
    at = end;
  }
  if (at < chars.length) out.push({ t: chars.slice(at).join(""), a: false });
  return out;
}

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

/** How much bigger than its measured size a block kind is laid out: the big cards fill the stage better a
    little larger. The stack's fit still shrinks a grown block that no longer fits. */
export const BLOCK_GROW = 1.15;
const GROWN = new Set(["counter", "repo", "window", "terminal", "chat", "dialog", "receipts", "people"]);

/** A block's natural size for the stack, from its measured size, and the factor it was grown by. */
export function grownSize(kind, w, h) {
  const grow = GROWN.has(kind) ? BLOCK_GROW : 1;
  return { w: w * grow, h: h * grow, grow };
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

// counts (K, M, B = billion) and byte sizes (KB…TB; a bare G or T is giga/tera): only the order matters here
const SCALE = { k: 1e3, m: 1e6, b: 1e9, g: 1e9, t: 1e12, kb: 1e3, mb: 1e6, gb: 1e9, tb: 1e12 };

/** Which of two score values wins: the higher number (a K, M or B right after it scales it, any case, so "1.2M"
    beats "900K"; so do byte units, "2MB" beats "900KB"); a tie, or a side that is not a number, goes right. */
export function winner(left, right) {
  const num = (text) => {
    // a byte unit may be plural ("3 GBs"); a count suffix may not ("5 bs" is no billion)
    const m = /(-?\d+(?:\.\d+)?)(?:\s?(?:([kmgt]b)s?|([kmbgt]))(?![a-z]))?/i.exec(String(text ?? "").replace(/(\d)[,\s](?=\d{3}\b)/g, "$1"));
    const unit = m && (m[2] || m[3]);
    return m ? Number(m[1]) * (unit ? SCALE[unit.toLowerCase()] : 1) : null;
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

// ------------------------------------------------------------ people and nodes

/** A people grid: a wide strip, about 5× as many columns as rows (125 people make the references' 25 × 5;
    never more columns than people), each cell `cell` px, smaller when the grid would be wider than `maxW`. */
export function gridOf(total, maxW, cell) {
  const n = Math.max(1, total);
  const cols = Math.min(n, Math.ceil(Math.sqrt(n * 5)));
  return { cols, rows: Math.ceil(n / cols), size: Math.min(cell, Math.floor(maxW / cols)) };
}

/** Which `lit` of `total` people light up: one at a seeded place inside each of `lit` equal runs of the grid
    (in reading order), so they are spread over it and never bunch up. */
export function litSet(total, lit, seed) {
  const rand = mulberry32(seed);
  const out = new Set();
  const k = Math.max(0, Math.min(lit, total));
  for (let i = 0; i < k; i++) {
    const lo = Math.floor((i * total) / k), hi = Math.floor(((i + 1) * total) / k);
    out.add(lo + Math.floor(rand() * (hi - lo)));
  }
  return out;
}

/** Where a `w`×`h` cell badge can sit over people who are all unlit: `{col, row}` of its top-left cell, the
    free spot nearest the grid's middle (ties to the upper, then the left one); null when there is none. */
export function emptySpot(cols, rows, total, lit, w, h) {
  let best = null, bestD = Infinity;
  for (let row = 0; row + h <= rows; row++) {
    for (let col = 0; col + w <= cols; col++) {
      let free = true;
      for (let r = row; r < row + h && free; r++) {
        for (let c = col; c < col + w; c++) {
          const i = r * cols + c;
          if (i >= total || lit.has(i)) { free = false; break; }
        }
      }
      if (!free) continue;
      const d = Math.hypot(col + w / 2 - cols / 2, row + h / 2 - rows / 2);
      if (d < bestD - 1e-9) [best, bestD] = [{ col, row }, d];
    }
  }
  return best;
}

// a triangle's corners first (top, bottom-left, bottom-right), then its edges' midpoints; `PERIM` is each slot's
// place going round the edge (top 0, left side 1, bottom-left 2, base 3, bottom-right 4, right side 5)
const TRI = { top: [0.5, 0.08], bl: [0.08, 0.92], br: [0.92, 0.92] };
const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
const TRI_SLOTS = {
  3: [[TRI.top, 0], [TRI.bl, 2], [TRI.br, 4]],
  4: [[TRI.top, 0], [TRI.bl, 2], [TRI.br, 4], [mid(TRI.bl, TRI.br), 3]],
  5: [[TRI.top, 0], [TRI.bl, 2], [TRI.br, 4], [mid(TRI.top, TRI.bl), 1], [mid(TRI.top, TRI.br), 5]],
  6: [[TRI.top, 0], [TRI.bl, 2], [TRI.br, 4], [mid(TRI.top, TRI.bl), 1], [mid(TRI.bl, TRI.br), 3], [mid(TRI.top, TRI.br), 5]],
};
const RING_R = 0.42;

/** `n` nodes' centres as fractions of their box: `row`/`flow` evenly across the middle, `ring` round a circle
    from the top clockwise, `triangle` on a triangle's corners then its edges (fewer than 3 make a row). */
export function nodeLayout(n, layout) {
  if (layout === "ring" && n > 1) {
    return Array.from({ length: n }, (_, i) => {
      const a = -Math.PI / 2 + (2 * Math.PI * i) / n;
      return { x: 0.5 + RING_R * Math.cos(a), y: 0.5 + RING_R * Math.sin(a) };
    });
  }
  if (layout === "triangle" && TRI_SLOTS[n]) return TRI_SLOTS[n].map(([[x, y]]) => ({ x, y }));
  return Array.from({ length: n }, (_, i) => ({ x: (i + 0.5) / n, y: 0.5 }));
}

/** Which nodes a line joins, `[from, to]`: each to the next for `row`/`flow` (a flow's arrows point that way),
    round the loop for `ring` and `triangle` (two nodes are joined once). */
export function nodeLinks(n, layout) {
  let order = Array.from({ length: n }, (_, i) => i);
  if (layout === "triangle" && TRI_SLOTS[n]) order = order.sort((a, b) => TRI_SLOTS[n][a][1] - TRI_SLOTS[n][b][1]);
  const loop = (layout === "ring" || (layout === "triangle" && TRI_SLOTS[n])) && n > 2;
  const links = order.slice(1).map((b, k) => [order[k], b]);
  if (loop) links.push([order[n - 1], order[0]]);
  return links;
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

export const PILL_EASE_MS = 120;

/** The caption pill at `t`: `sizes[k]` is its size with words 0..k revealed (measured at build); it eases from
    where it was to the newest word's over PILL_EASE_MS. `k` is the newest revealed word. A word that comes
    before the last ease finished starts from that ease's current (partial) size, so the pill never jumps. */
export function pillAt(sizes, words, t) {
  if (!sizes.length) return null;
  let k = 0;
  for (let i = 0; i < words.length; i++) if (words[i].startMs <= t) k = i;
  const size = (j, at) => {
    if (j === 0) return sizes[0];
    const from = size(j - 1, words[j].startMs);
    const p = easeOutCubic(clamp01((at - words[j].startMs) / PILL_EASE_MS));
    return { w: lerp(from.w, sizes[j].w, p), h: lerp(from.h, sizes[j].h, p) };
  };
  const { w, h } = size(k, t);
  return { w, h, k };
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

/** What the hero shows for an authored `state`: in the centre it moves to small-top while any other block is
    live in the stack (spec §5.2); every other state, pinned or placed, is kept. */
export const effectiveHeroState = (state, othersLive) => (state === "center" && othersLive ? "small-top" : state);

/** The authored hero states with the automatic small-top moves added: a state at each time the stage gains or
    loses its last other block (`lives` are those blocks' `{enterMs, exitMs}`, live until their exit has played)
    where the effective state changes. Gaps shorter than a hero move are bridged, so the hero does not bounce
    to the centre and straight back between two blocks. heroAt and heroSpan both read the result, so what the
    hero does and whether it stacks agree. */
export function heroTimeline(states, lives) {
  if (!states || !states.length) return [];
  const spans = lives.map((v) => [enterOf(v), endOf(v)]).sort((a, b) => a[0] - b[0]);
  const windows = [];
  for (const [a, b] of spans) {
    const last = windows[windows.length - 1];
    if (last && a - last[1] < HERO_MOVE_MS) last[1] = Math.max(last[1], b);
    else windows.push([a, b]);
  }
  const liveAt = (t) => windows.some(([a, b]) => a <= t && t < b);
  const edges = windows.flat().filter(Number.isFinite);
  const times = [...new Set([...states.map((s) => s.atMs), ...edges])].sort((a, b) => a - b);
  const out = [];
  let authored = null;
  for (const t of times) {
    const here = states.filter((s) => s.atMs === t);
    if (here.length) {
      for (const s of here) out.push({ ...s, state: effectiveHeroState(s.state, liveAt(t)) });
      authored = here[here.length - 1];
    } else if (authored) {
      const state = effectiveHeroState(authored.state, liveAt(t));
      if (state !== out[out.length - 1].state) out.push({ ...authored, atMs: t, state });
    }
  }
  return out;
}

/** Where `count` split dots are at move progress `p`, as px offsets from the hero's centre: out from the centre
    (back-out, so they overshoot a touch) to nodeLayout's triangle (a row for two) over a `w`×`h` box. */
export function splitPoints(count, w, h, p) {
  const e = p <= 0 ? 0 : p >= 1 ? 1 : backOut(p);
  return nodeLayout(count, count >= 3 ? "triangle" : "row")
    // + 0 turns -0 into 0
    .map(({ x, y }) => ({ x: (x - 0.5) * w * e + 0, y: (y - 0.5) * h * e + 0 }));
}

/** How open the robot's eyes are at `t`, 0..1: a BLINK_MS blink every 3–5 s, the gaps drawn from `seed`. */
export function blinkAt(t, seed) {
  const rand = mulberry32(seed);
  const gap = () => 3000 + 2000 * rand();
  let start = gap();
  while (start + BLINK_MS <= t) start += gap();
  return t < start ? 1 : 1 - Math.sin((Math.PI * (t - start)) / BLINK_MS);
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
