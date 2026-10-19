// reelmachine/recipes/techreel/runtime/main.mjs
// Builds the reel's DOM once from #reel-data, then sets every transform, opacity and text from the time
// HyperFrames seeks to (pure.mjs decides; this file only applies). HyperFrames waits on
// window.__hf.buildReady (set inline in index.html) before capturing. Opened directly in a browser,
// index.html?t=3.2 shows the frame at 3.2 s.
import {
  ENTER_MS, GLIDE_MS, HERO_MOVE_MS, HERO_STACKING, STAGE, backOut, clamp01, enterP, exitP, alive, stackLayout,
  captionAt, wordPhase, pillAt, heroAt, heroSpan, heroTimeline, beatAt, transitionP, progress, blob, seedOf,
} from "./pure.mjs";
import { COMPONENTS, STAMP_FROM, el, wordSpans, slam } from "./components.mjs";
import { CARDS } from "./cards.mjs";
import { SOCIAL } from "./social.mjs";
import { HERO_SIZE, SPLITS, buildHero, updateHero } from "./hero.mjs";

const done = () => window.__reelResolve && window.__reelResolve();
const lerp = (a, b, p) => a + (b - a) * p;
const smooth = (p) => p * p * (3 - 2 * p);
const easeOut = (p) => 1 - Math.pow(1 - p, 3);
const easeInOut = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
const TRANSITION_LEN = { cut: 0, slide: 250, flash: 200, zoom: 300 };
const HEADING_STAGGER_MS = 70, HEADING_OUT_MS = 150;
const HEADING_ROOM = 300, HEADING_GAP = 22, HEADING_PX = 96, TEXT_W = 952;
const CAPTION_ROOM = 220, CAPTION_POP_MS = 160;
const SMALL_TOP = 0.45, CORNER = 0.3;
const STAMP_TILT = 9; // the most a stamp's slam turns it past its own rotation (components.mjs: -9° → 4°)
const STAGE_CX = (STAGE.left + STAGE.right) / 2, STAGE_CY = (STAGE.top + STAGE.bottom) / 2;
const GRAIN = "data:image/svg+xml;utf8," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240"><filter id="n"><feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed="7" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/></filter><rect width="240" height="240" filter="url(#n)"/></svg>`);

const KINDS = { ...COMPONENTS, ...CARDS, ...SOCIAL };
const px = (v) => `${v.toFixed(1)}px`;
const switchAt = (beat) => beat.startMs - (TRANSITION_LEN[beat.transition] ?? 0);

// ------------------------------------------------------------ build

function buildBeat(ui, beat, index, doc) {
  const heading = el("div", "heading", ui.headings);
  const kicker = beat.kicker ? el("div", "kicker", heading, beat.kicker) : null;
  const htitle = beat.heading.length ? el("div", "htitle", heading) : null;
  const words = htitle ? wordSpans(htitle, beat.heading) : [];
  if (!kicker && !htitle) heading.style.display = "none";

  const stage = el("div", "layer beat-stage", ui.stageLayer);
  const over = el("div", "layer beat-over", ui.overLayer);
  const blocks = [], overlays = [];
  for (const v of beat.visuals) {
    const comp = KINDS[v.kind];
    if (!comp) { console.warn(`techreel: no component for visual kind "${v.kind}", skipped`); continue; }
    const node = comp.build(v, doc);
    if (v.class === "overlay") {
      const wrap = el("div", "over", over);
      const rot = el("div", null, wrap);
      rot.appendChild(node);
      overlays.push({ v, comp, wrap, rot, node, w: 0, h: 0 });
    } else {
      const slot = el("div", "slot", stage);
      const anim = el("div", "anim", slot);
      anim.appendChild(node);
      blocks.push({ v, comp, slot, anim, node });
    }
  }
  return { beat, index, heading, kicker, htitle, words, stage, over, blocks, overlays };
}

function build(doc, root) {
  for (const [key, value] of Object.entries(doc.look || {})) document.documentElement.style.setProperty(`--${key}`, value);
  // the hero's states as authored; fit() adds the automatic small-top moves once the blocks are measured
  const ui = { root, sizes: new Map(), states: doc.heroStates || [] };
  const bg = el("div", "layer bg", root);
  ui.blobs = [0, 1, 2].map((i) => el("div", `blob b${i}`, bg));
  el("div", "layer vignette", root);
  const grain = el("div", "layer grain", root);
  grain.style.backgroundImage = `url("${GRAIN}")`;
  ui.seed = seedOf(doc.beats.map((b) => b.id).join("|"));

  ui.headings = el("div", "layer headings", root);
  // the hero sits under the stage's cards: its rings and glow show through their glass, never over a card
  ui.heroLayer = el("div", "layer", root);
  ui.stageLayer = el("div", "layer", root);
  ui.overLayer = el("div", "layer", root);
  ui.flash = el("div", "layer flash", root);
  ui.captionBox = el("div", "captions", root);
  const bar = el("div", "progress", root);
  ui.bar = el("div", "fill", bar);

  ui.beats = doc.beats.map((beat, i) => buildBeat(ui, beat, i, doc));
  if (doc.hero) {
    // kinds the runtime cannot draw yet fall back to the orb rather than crash
    if (!HERO_SIZE[doc.hero.kind]) console.warn(`techreel: hero kind "${doc.hero.kind}" is not drawn yet, using orb`);
    ui.heroKind = HERO_SIZE[doc.hero.kind] ? doc.hero.kind : "orb";
    // only the orb and the robot split into dots; the rest stay whole, in the centre
    if (!SPLITS.has(ui.heroKind)) ui.states = ui.states.map((s) => (s.state === "split" ? { ...s, state: "center" } : s));
    ui.hero = buildHero(ui.heroKind, doc);
    ui.heroSize = HERO_SIZE[ui.heroKind];
    ui.heroLayer.appendChild(ui.hero);
  }
  ui.captions = (doc.captions || []).map((c) => {
    const node = el("div", "cap", ui.captionBox);
    if (doc.language && /^(ar|he|fa|ur)\b/.test(doc.language)) node.dir = "rtl";
    // one inline run inside the pill, so the pill can centre it as a block
    const text = el("span", "ct", node);
    const words = c.words.map((w, i) => {
      const span = el("span", w.a ? "w accent" : "w", text, w.t);
      if (i < c.words.length - 1) text.appendChild(document.createTextNode(" "));
      return span;
    });
    return { c, node, text, words, sizes: [], runs: [] };
  });
  return ui;
}

/** Sizes that need the fonts: headings and captions shrink to their bands, components fit themselves, and
    every block's natural size is measured for the stack. Runs once, after fonts load. */
function fit(ui) {
  for (const b of ui.beats) {
    if (b.htitle) {
      const room = HEADING_ROOM - (b.kicker ? b.kicker.offsetHeight + HEADING_GAP : 0);
      let size = HEADING_PX;
      const widest = () => Math.max(...b.words.map((w) => w.getBoundingClientRect().width));
      while ((b.htitle.offsetHeight > room || widest() > TEXT_W) && size > 36) {
        size -= 4;
        b.htitle.style.fontSize = `${size}px`;
      }
    }
    for (const blk of b.blocks) {
      if (blk.comp.fit) blk.comp.fit(blk.node, blk.v);
      ui.sizes.set(blk.v.id, { w: blk.node.offsetWidth, h: blk.node.offsetHeight });
    }
    for (const o of b.overlays) {
      if (o.comp.fit) o.comp.fit(o.node, o.v);
      o.w = o.node.offsetWidth;
      o.h = o.node.offsetHeight;
    }
  }
  // while other blocks are on the stage the centred hero sits small at the top (spec §5.2); heroAt and heroSpan
  // both read this one timeline, so what the hero does and whether it stacks agree. A block of no size takes
  // no room, so it makes none
  const lives = ui.beats.flatMap((b) => b.blocks.map((blk) => blk.v)).filter((v) => {
    const s = ui.sizes.get(v.id);
    return s && (s.w > 0 || s.h > 0);
  });
  ui.states = heroTimeline(ui.states, lives);
  for (const cap of ui.captions) {
    let size = 54;
    while (cap.node.offsetHeight > CAPTION_ROOM && size > 30) {
      size -= 2;
      cap.node.style.fontSize = `${size}px`;
    }
    // the pill grows with the words said so far: measure it once per revealed word (it needs the words laid out
    // to wrap), then applyCaptions eases between those sizes. Rounded up (offsetWidth rounds down: a fractional
    // width would push the last word onto a line the height has no room for), plus a pixel of slack for the
    // flex layout's own rounding
    cap.node.classList.add("held");
    cap.node.style.width = "";
    cap.node.style.height = "";
    cap.sizes = [];
    cap.runs = [];
    cap.words.forEach((_, i) => {
      cap.words.forEach((span, j) => span.classList.toggle("future", j > i));
      cap.text.style.width = "";
      const box = cap.node.getBoundingClientRect();
      cap.sizes.push({ w: Math.ceil(box.width) + 1, h: Math.ceil(box.height) + 1 });
      cap.runs.push(Math.ceil(cap.text.getBoundingClientRect().width) + 1);
    });
  }
}

// ------------------------------------------------------------ the stack and the hero

const heroScale = (state) => (state === "small-top" ? SMALL_TOP : 1);

/** Beat `k`'s stack at `ms`, the hero in it (when it stacks) at `k` times its natural size. */
function layoutOf(ui, k, ms, kScale) {
  // a block of no size (a follow card with no creator) takes no row, so its neighbours do not shift
  const blocks = ui.beats[k].blocks.map((b) => b.v).filter((v) => {
    const s = ui.sizes.get(v.id);
    return !s || s.w > 0 || s.h > 0;
  });
  const span = ui.hero ? heroSpan(ui.states, ms) : null;
  let sizes = ui.sizes;
  if (span) {
    blocks.push(span);
    sizes = new Map(ui.sizes).set("hero", { w: ui.heroSize.w * kScale, h: ui.heroSize.h * kScale });
  }
  const layout = stackLayout(blocks, sizes, ms);
  layout.sizes = sizes; // what it was laid out with, for the overlays' anchor boxes
  return layout;
}

/** The hero's centre and scale in its stack slot at `ms`; across a beat change it glides from the old
    beat's stack to the new one's over the transition (at least GLIDE_MS), so it never pops. */
function heroSlot(ui, doc, ms, kScale) {
  const slotIn = (k) => {
    const pos = layoutOf(ui, k, ms, kScale).get("hero");
    if (!pos) return null;
    const { w, h } = ui.heroSize;
    return { cx: pos.x + (w * kScale * pos.scale) / 2, cy: pos.y + (h * kScale * pos.scale) / 2, s: kScale * pos.scale };
  };
  const i = beatAt(doc.beats, ms);
  const cur = slotIn(i);
  if (i === 0) return cur;
  const beat = doc.beats[i];
  const blend = smooth(clamp01((ms - switchAt(beat)) / Math.max(TRANSITION_LEN[beat.transition] ?? 0, GLIDE_MS)));
  const prev = blend < 1 ? slotIn(i - 1) : null;
  if (!prev || !cur) return cur || prev;
  return { cx: lerp(prev.cx, cur.cx, blend), cy: lerp(prev.cy, cur.cy, blend), s: lerp(prev.s, cur.s, blend) };
}

/** Where a state puts the hero: its stack slot, the stage's top-right corner, or (hidden) shrunk to nothing
    where `other` is. */
function poseOf(ui, doc, state, ms, kScale, other) {
  const { w, h } = ui.heroSize;
  if (state === "corner") return { cx: STAGE.right - (w * CORNER) / 2, cy: STAGE.top + (h * CORNER) / 2, s: CORNER, o: 1 };
  if (state === "hidden") return { ...(other || { cx: STAGE_CX, cy: STAGE_CY }), s: 0, o: 0 };
  const slot = heroSlot(ui, doc, ms, kScale) || { cx: STAGE_CX, cy: STAGE_CY, s: kScale };
  return { ...slot, o: state === "sleep" ? 0.45 : 1 };
}

/** The split state the hero is in or leaving at `ms` (its count and labels), or null. */
function splitInPlay(states, ms) {
  let i = -1;
  for (let k = 0; k < states.length; k++) if (states[k].atMs <= ms) i = k;
  if (i < 0) return null;
  if (states[i].state === "split") return states[i];
  return i > 0 && states[i - 1].state === "split" ? states[i - 1] : null;
}

function applyHero(ui, doc, ms) {
  if (!ui.hero) return;
  const h = heroAt(ui.states, ms);
  if (!h) {
    ui.hero.style.display = "none";
    return;
  }
  const e = backOut(h.p);
  const stacks = (s) => HERO_STACKING.has(s);
  let pose;
  if (!h.from || h.p >= 1 || (stacks(h.from) && stacks(h.state))) {
    // between two stacking states the slot itself changes size, so the stack makes room as the hero grows
    pose = poseOf(ui, doc, h.state, ms, currentHeroScale(ui, ms), null);
    if (h.from && h.p < 1) pose.o = lerp(h.from === "sleep" ? 0.45 : 1, pose.o, clamp01(h.p));
  } else {
    const atMs = ms - h.p * HERO_MOVE_MS;
    const fromBase = stacks(h.from) ? poseOf(ui, doc, h.from, atMs - 1, heroScale(h.from), null) : null;
    const to0 = poseOf(ui, doc, h.state, ms, heroScale(h.state), fromBase);
    const from = fromBase || poseOf(ui, doc, h.from, atMs, heroScale(h.from), to0);
    const to = h.state === "hidden" ? { ...from, s: 0, o: 0 } : to0;
    pose = { cx: lerp(from.cx, to.cx, e), cy: lerp(from.cy, to.cy, e), s: Math.max(0, lerp(from.s, to.s, e)), o: lerp(from.o, to.o, clamp01(h.p)) };
  }
  if (pose.o <= 0.001 || pose.s <= 0.001) {
    ui.hero.style.display = "none";
    return;
  }
  ui.hero.style.display = "block";
  const { w, h: hh } = ui.heroSize;
  ui.hero.style.opacity = pose.o.toFixed(3);
  ui.hero.style.transform = `translate(${px(pose.cx - w / 2)}, ${px(pose.cy - hh / 2)}) scale(${pose.s.toFixed(4)})`;
  updateHero(ui.hero, h, ms, doc, splitInPlay(ui.states, ms));
}

// ------------------------------------------------------------ one beat's stage

function applyStage(ui, doc, b, ms) {
  const layout = layoutOf(ui, b.index, ms, ui.hero ? currentHeroScale(ui, ms) : 1);
  for (const blk of b.blocks) {
    const pos = layout.get(blk.v.id);
    if (!pos || !alive(blk.v, ms)) {
      blk.slot.style.display = "none";
      continue;
    }
    blk.slot.style.display = "block";
    blk.slot.style.transform = `translate(${px(pos.x)}, ${px(pos.y)}) scale(${pos.scale.toFixed(4)})`;
    const e = enterP(blk.v, ms), x = exitP(blk.v, ms);
    let opacity = 1 - x, scale = 1 - 0.08 * x, y = 0;
    if (!blk.comp.ownEnter) {
      opacity *= clamp01(e * 2);
      scale *= lerp(0.82, 1, backOut(e));
      y = 40 * (1 - easeOut(e));
    }
    blk.anim.style.opacity = opacity.toFixed(3);
    blk.anim.style.transform = `translateY(${px(y)}) scale(${scale.toFixed(4)})`;
    blk.anim.style.filter = x > 0 ? `blur(${(8 * x).toFixed(2)}px)` : "none";
    blk.comp.update(blk.node, blk.v, ms, doc);
  }
  for (const o of b.overlays) {
    if (!alive(o.v, ms)) {
      o.wrap.style.display = "none";
      continue;
    }
    const box = anchorBox(ui, b, o.v, layout, ms);
    let cx, cy;
    if (o.v.kind === "emoji") {
      // perched just above the block's top corner, so it covers none of the block's content
      cx = o.v.place === "left" ? box.x + o.w / 2 : box.x + box.w - o.w / 2;
      cy = box.y - o.h / 2 + 12;
    } else if (o.v.kind === "flash") {
      // full-bleed: the whole frame, under the captions and the progress bar (main.mjs's layer order)
      o.wrap.style.display = "block";
      o.wrap.style.transform = "none";
      o.wrap.style.opacity = "1";
      o.rot.style.transform = "none";
      o.comp.update(o.node, o.v, ms, doc);
      continue;
    } else if (o.v.kind === "error") {
      // an OS error box pops up over the middle of the stage, whatever is under it
      cx = STAGE_CX;
      cy = STAGE_CY;
    } else {
      cx = box.x + box.w / 2;
      cy = box.y + box.h / 2;
    }
    // the whole overlay stays inside the stage: the side margins and the heading/caption bands. A stamp is
    // measured as its tilted box, and slams down from no bigger than the room round it allows
    let bw = o.w, bh = o.h;
    if (o.v.kind === "stamp") {
      const a = ((Math.abs(o.v.rotate ?? -8) + STAMP_TILT) * Math.PI) / 180;
      bw = o.w * Math.cos(a) + o.h * Math.sin(a);
      bh = o.w * Math.sin(a) + o.h * Math.cos(a);
    }
    cx = Math.min(STAGE.right - bw / 2, Math.max(STAGE.left + bw / 2, cx));
    cy = Math.min(STAGE.bottom - bh / 2, Math.max(STAGE.top + bh / 2, cy));
    if (o.v.kind === "stamp") {
      const room = Math.min(2 * (cx - STAGE.left) / bw, 2 * (STAGE.right - cx) / bw,
        2 * (cy - STAGE.top) / bh, 2 * (STAGE.bottom - cy) / bh);
      o.node._from = Math.max(1, Math.min(STAMP_FROM, room));
    }
    o.wrap.style.display = "block";
    o.wrap.style.transform = `translate(${px(cx - o.w / 2)}, ${px(cy - o.h / 2)})`;
    o.wrap.style.opacity = (1 - exitP(o.v, ms)).toFixed(3);
    o.rot.style.transform = o.v.kind === "stamp" ? `rotate(${o.v.rotate ?? -8}deg)` : "none";
    o.comp.update(o.node, o.v, ms, doc);
  }
}

/** The hero's slot size at `ms` as the stack sees it (it grows/shrinks between centre and small-top). */
function currentHeroScale(ui, ms) {
  const h = heroAt(ui.states, ms);
  if (!h) return 1;
  const both = h.from && HERO_STACKING.has(h.from) && HERO_STACKING.has(h.state);
  return both && h.p < 1 ? lerp(heroScale(h.from), heroScale(h.state), backOut(h.p)) : heroScale(h.state);
}

/** The box an overlay sits on: the newest block live when it entered (it then follows that block's glide),
    else the hero's slot, else the stage's middle. */
function anchorBox(ui, b, v, layout, ms) {
  let best = null;
  for (const blk of b.blocks) {
    if (alive(blk.v, v.enterMs) && layout.has(blk.v.id) && (!best || blk.v.enterMs >= best.v.enterMs)) best = blk;
  }
  if (best) {
    const pos = layout.get(best.v.id), size = ui.sizes.get(best.v.id);
    return { x: pos.x, y: pos.y, w: size.w * pos.scale, h: size.h * pos.scale };
  }
  const hero = layout.get("hero");
  if (hero) {
    const size = layout.sizes.get("hero");
    return { x: hero.x, y: hero.y, w: size.w * hero.scale, h: size.h * hero.scale };
  }
  return { x: STAGE_CX - 200, y: STAGE_CY - 200, w: 400, h: 400 };
}

// ------------------------------------------------------------ the frame

function applyHeading(b, ms, next) {
  if (!b.kicker && !b.htitle) return;
  const start = switchAt(b.beat);
  const end = next ? switchAt(next) : Infinity;
  const out = clamp01((ms - (end - HEADING_OUT_MS)) / HEADING_OUT_MS);
  b.heading.style.display = ms >= start && ms < end ? "flex" : "none";
  b.heading.style.opacity = (1 - out).toFixed(3);
  b.heading.style.filter = out > 0 ? `blur(${(16 * out).toFixed(2)}px)` : "none";
  b.heading.style.transform = out > 0 ? `scale(${(1 + 0.05 * out).toFixed(4)})` : "none";
  if (b.kicker) {
    const p = clamp01((ms - start) / ENTER_MS);
    b.kicker.style.opacity = p.toFixed(3);
    b.kicker.style.transform = `translateY(${px(14 * (1 - easeOut(p)))})`;
  }
  b.words.forEach((w, k) => slam(w, ms, start + (b.kicker ? 80 : 0) + k * HEADING_STAGGER_MS));
}

/** The incoming (or outgoing) beat's layers under its transition at progress p. */
function applyTransition(b, kind, p, incoming) {
  let transform = "none", opacity = 1, filter = "none";
  if (p < 1) {
    if (kind === "slide") {
      const e = easeInOut(p);
      transform = `translateX(${px(incoming ? 1080 * (1 - e) : -1080 * e)})`;
    } else if (kind === "flash") {
      opacity = incoming ? (p >= 0.5 ? 1 : 0) : (p < 0.5 ? 1 : 0);
    } else if (kind === "zoom") {
      const e = easeOut(p);
      if (incoming) {
        transform = `scale(${(1.15 - 0.15 * e).toFixed(4)})`;
        filter = `blur(${(12 * (1 - e)).toFixed(2)}px)`;
        opacity = e;
      } else {
        transform = `scale(${(1 - 0.06 * p).toFixed(4)})`;
        opacity = 1 - p;
      }
    }
  }
  for (const layer of [b.stage, b.over]) {
    layer.style.display = opacity > 0 ? "block" : "none";
    layer.style.transform = transform;
    layer.style.transformOrigin = `${STAGE_CX}px ${STAGE_CY}px`;
    layer.style.opacity = opacity.toFixed(3);
    layer.style.filter = filter;
  }
}

function applyCaptions(ui, doc, ms) {
  const index = captionAt(doc.captions || [], ms);
  ui.captions.forEach((cap, k) => {
    cap.node.classList.toggle("on", k === index);
    if (k !== index) return;
    const p = clamp01((ms - cap.c.startMs) / CAPTION_POP_MS);
    cap.node.style.opacity = clamp01(p * 2).toFixed(3);
    cap.node.style.transform = `translateY(${px(10 * (1 - easeOut(p)))}) scale(${lerp(0.94, 1, backOut(p)).toFixed(4)})`;
    const pill = pillAt(cap.sizes, cap.c.words, ms);
    if (pill) {
      cap.node.style.width = `${pill.w.toFixed(2)}px`;
      cap.node.style.height = `${pill.h.toFixed(2)}px`;
      // the text keeps the width it wraps at for the newest word, centred in the pill as it grows
      cap.text.style.width = `${cap.runs[pill.k]}px`;
      cap.text.style.flexShrink = "0";
    }
    cap.words.forEach((span, i) => {
      const phase = wordPhase(cap.c.words[i], ms);
      span.classList.toggle("future", phase === "future");
      span.classList.toggle("now", phase === "now");
      span.classList.toggle("past", phase === "past");
    });
  });
}

function renderFrame(ui, doc, seconds) {
  const ms = seconds * 1000;
  ui.blobs.forEach((node, i) => {
    const b = blob(i, ms, ui.seed);
    node.style.transform = `translate(${px(b.x - 450)}, ${px(b.y - 450)}) scale(${(b.r / 450).toFixed(4)})`;
  });

  const beats = doc.beats;
  const i = beatAt(beats, ms);
  const p = beats.length ? transitionP(beats, i, ms) : 1;
  const kind = beats[i]?.transition;
  ui.beats.forEach((b, k) => {
    applyHeading(b, ms, beats[k + 1]);
    const shown = k === i || (k === i - 1 && p < 1);
    if (!shown) {
      b.stage.style.display = "none";
      b.over.style.display = "none";
      return;
    }
    applyTransition(b, kind, p, k === i);
    applyStage(ui, doc, b, ms);
  });
  applyHero(ui, doc, ms);

  // a flash peaks at its midpoint, covering the swap of the stages under it
  const flash = kind === "flash" && p < 1 ? Math.pow(1 - Math.abs(2 * p - 1), 0.6) : 0;
  ui.flash.style.opacity = flash.toFixed(3);

  applyCaptions(ui, doc, ms);
  ui.bar.style.transform = `scaleX(${progress(ms, doc.duration * 1000).toFixed(5)})`;
}

try {
  const doc = JSON.parse(document.getElementById("reel-data").textContent);
  const root = document.getElementById("root");
  const ui = build(doc, root);
  if (document.fonts) {
    // ask for each caption's face with its own text (a face is only fetched for text that uses it), then wait
    // for everything the page laid out, so fit() measures real metrics
    const text = (doc.captions || []).flatMap((c) => c.words.map((w) => w.t)).join(" ");
    // the display face at the heavy weight the headings, titles and numbers ask for, with their own text
    const display = doc.beats.flatMap((b) => [...b.heading.map((w) => w.t), ...b.visuals.flatMap((v) => [
      v.text, v.label, v.result, ...(v.rows || []), v.left?.value, v.right?.value, v.kind === "versus" ? "VS" : null,
      v.kind === "follow" ? "FOLLOWING ✓" : null])])
      .filter(Boolean).join(" ").toUpperCase() + " 0123456789";
    const css = getComputedStyle(document.documentElement);
    const face = (name) => css.getPropertyValue(name);
    await Promise.all([
      ...[face("--sans"), face("--display"), face("--mono")].map((family) => document.fonts.load(`800 54px ${family}`, text || "a")),
      document.fonts.load(`900 96px ${face("--display")}`, display),
    ].map((p) => p.catch(() => null)));
    if (document.fonts.ready) await document.fonts.ready;
  }
  // images first: fit() measures an image card by its pixels, and no frame is captured before they are decoded
  const pending = [ui.hero?._ready, ...ui.beats.flatMap((b) => [...b.blocks, ...b.overlays].map((x) => x.node._ready))];
  await Promise.all(pending.filter(Boolean));
  fit(ui);
  const renderAt = (t) => renderFrame(ui, doc, t);
  window.addEventListener("hf-seek", (event) => {
    try {
      renderAt(event.detail.time);
    } catch (error) {
      console.error(`techreel: frame at ${event.detail.time}s could not be drawn`, error);
    }
  });
  window.__reelRenderAt = renderAt;
  const preview = new URLSearchParams(window.location.search).get("t");
  renderAt(preview !== null && Number.isFinite(Number(preview)) ? Number(preview) : window.__hfThreeTime || 0);
} catch (error) {
  // the first line only (the error's name and message): the rest of a stack names local paths
  window.__reelError = String((error && error.stack) || error).split("\n")[0];
  console.error("techreel: the reel could not be built", error);
} finally {
  // HyperFrames reads window.__hfStartupError before capture and fails the render with its text
  if (window.__reelError) window.__hfStartupError = window.__reelError;
  done();
}
