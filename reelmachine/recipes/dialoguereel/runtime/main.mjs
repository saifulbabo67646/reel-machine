// reelmachine/recipes/dialoguereel/runtime/main.mjs
// Builds the reel's DOM once from #reel-data, then sets every transform, opacity and class from the time
// HyperFrames seeks to (pure.mjs decides; this file only applies). HyperFrames waits on
// window.__hf.buildReady (set inline in index.html) before capturing. Opened directly in a browser,
// index.html?t=3.2 shows the frame at 3.2 s.
import { activeLine, dropIn, hop, mouthOpen, blinking, litChars, fade, history, progress, bob, seedOf, fitFont, flag, clamp01, DROP_MS, partId,
  backOut, keyVisible, emotionParts, emojiPop, slowActive, slowWords, rowFit, SLOW_FLAP_MS } from "./pure.mjs";

const done = () => window.__reelResolve && window.__reelResolve();
const DROP_DELAY = { left: 150, right: 300 };
const TILT_DEG = 4;
const EXIT_MS = 140; // the previous bubble shrinks up into the history ...
const ENTER_DELAY_MS = 70, ENTER_MS = 220; // ... and the new one pops in just after
const SLIDE_MS = 300; // history rows move up one slot
// history rows are their line at 70 % size: the bubble's fitted text size and its meaning size, times 0.7
const ROW_SCALE = 0.7, MEANING_PX = 56, ROW_LEADING = 1.3, ROW_GAP = 10, ROW_AIR = 14;
// what a row's two texts have to share: its 936 px less 2 × 28 px padding, the 18 px gap and a little slack
const ROW_ROOM = 936 - 56 - 18 - 6;
const BUBBLE_TOP = 372; // two rows at the largest size fit between the badge (bottom 148) and the bubble
// the key chips sit in the band between the bubble's tail and the characters' heads
const TAIL_PX = 62, CHIP_AIR = 22, CHIP_TOP = 1000, CHIP_BOTTOM = 1250;
const SLOW_TAG_MS = 240;

/** Each history row's top when line `a` is active: the newest sits just above the bubble, older ones above it. */
function rowTops(rows, a) {
  const tops = new Map();
  let bottom = BUBBLE_TOP - ROW_AIR;
  for (let k = a - 1; k >= Math.max(0, a - 3); k--) {
    tops.set(k, bottom - rows[k].height);
    bottom -= rows[k].height + ROW_GAP;
  }
  return tops;
}

function el(tag, className, parent, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  if (parent) parent.appendChild(node);
  return node;
}

const show = (node, on) => { if (node) node.style.display = on ? "inline" : "none"; };
const lerp = (a, b, p) => a + (b - a) * p;

function buildCharacter(root, char, index) {
  const box = document.getElementById(`char-${char.id}`);
  if (!box) throw new Error(`dialoguereel: character ${char.id} is missing from the page`);
  const part = (name) => document.getElementById(partId(char.id, name));
  const arm = (name) => {
    const node = part(name);
    if (!node) return null;
    const [x, y] = (node.getAttribute("data-pivot") || "200,300").split(",").map(Number);
    node.style.transformOrigin = `${x}px ${y}px`;
    node.style.transformBox = "view-box";
    return node;
  };
  const shadow = el("div", `shadow ${char.side}`);
  root.insertBefore(shadow, box);
  const inner = char.side === "left" ? "arm-right" : "arm-left";
  return {
    ...char, index, box, shadow, seed: seedOf(char.id),
    eyesOpen: part("eyes-open"), eyesClosed: part("eyes-closed"), eyesHappy: part("eyes-happy"),
    eyesSurprised: part("eyes-surprised"), sweat: part("sweat"),
    mouthClosed: part("mouth-closed"), mouthOpen: part("mouth-open"), mouthSmile: part("mouth-smile"),
    arm: arm(inner), armSign: inner === "arm-left" ? 1 : -1,
    emoji: el("div", `emoji ${char.side}`, root), emojiText: "",
  };
}

/** A key word's chip: the word (its reading above it, as in the bubble), a dot, the meaning and its JLPT level. */
function buildChip(parent, key) {
  const chip = el("div", "chip", parent);
  const word = el("span", "chip-word", chip);
  if (key.reading && key.reading !== key.text) {
    const ruby = el("ruby", null, word, key.text);
    el("rt", null, ruby, key.reading);
  } else {
    word.textContent = key.text;
  }
  el("span", "chip-dot", chip, "·");
  el("span", "chip-meaning", chip, key.meaning);
  if (Number.isInteger(key.level)) el("span", "chip-level", chip, `N${key.level}`);
  return chip;
}

function buildBubble(stage, line, side) {
  const bubble = el("div", `bubble ${side}`, stage);
  el("div", "tail", bubble);
  const body = el("div", "body", bubble);
  const text = el("div", "text", body);
  const plain = line.runs.map((r) => r.t).join("");
  text.style.fontSize = `${fitFont(plain)}px`;
  const chars = [];
  const rts = [];
  for (const run of line.runs) {
    const host = run.r ? el("ruby", null, text) : text;
    const first = chars.length;
    for (const ch of run.t) chars.push(el("span", "ch", host, ch));
    if (run.r) rts.push({ node: el("rt", null, host, run.r), first });
  }
  const keys = line.keys || [];
  for (const key of keys) for (let i = key.start; i < key.end && i < chars.length; i++) chars[i].classList.add("key");
  const reading = line.reading ? el("div", "reading", body, line.reading) : null;
  const meaning = el("div", "meaning", body, line.meaning);
  const spoken = line.words.length ? line.words : [{ text: plain, startMs: line.startMs, endMs: line.endMs }];
  const slow = line.slowStartMs != null ? { words: slowWords(line), tag: el("div", "slow-tag", bubble, "🐢 0.75×") } : null;
  const chipBox = el("div", "chips", stage);
  // in the order the karaoke reaches them
  const chips = [...keys].sort((x, y) => x.start - y.start).map((key) => ({ key, node: buildChip(chipBox, key) }));
  return { bubble, body, chars, rts, reading, meaning, spoken, slow, chipBox, chips, lit: -1 };
}

function build(doc, root) {
  root.style.setProperty("--bg", doc.look.bg);
  root.style.setProperty("--text", doc.look.text);
  root.style.setProperty("--reading", doc.look.reading);
  root.style.setProperty("--meaning", doc.look.meaning);
  root.style.setProperty("--accent", doc.look.accent);
  root.style.setProperty("--bubble", doc.look.bubble);
  root.prepend(el("div", "floor"));
  root.prepend(el("div", "glow"));
  const ui = { root, mode: doc.mode };
  ui.chars = doc.characters.map((c, i) => buildCharacter(root, c, i));
  ui.bySide = Object.fromEntries(ui.chars.map((c) => [c.id, c.side]));
  if (doc.mode !== "reel") return ui;

  const bar = el("div", "progress", root);
  ui.bar = el("div", "fill", bar);
  const badgeText = [doc.level, flag(doc.language)].filter(Boolean).join(" · ");
  ui.badge = badgeText ? el("div", "badge", root, badgeText) : null;

  ui.hook = el("div", "hook", root);
  ui.scene = doc.scene ? el("div", "scene", ui.hook, doc.scene) : null;
  el("div", "title", ui.hook, doc.title);
  if (doc.prompt) el("div", "sub", ui.hook, doc.prompt);

  const stage = el("div", "stage", root);
  ui.rows = doc.lines.map((line) => {
    const node = el("div", `row ${ui.bySide[line.speaker] || "left"}`, stage);
    const plain = line.runs.map((r) => r.t).join("");
    const base = { textPx: Math.round(ROW_SCALE * fitFont(plain)), meaningPx: Math.round(ROW_SCALE * MEANING_PX) };
    return { node, base, text: el("span", "row-text", node, plain), meaning: el("span", "row-meaning", node, line.meaning), height: 0 };
  });
  ui.bubbles = doc.lines.map((line) => buildBubble(stage, line, ui.bySide[line.speaker] || "left"));
  return ui;
}

/** Sizes that need the fonts: each history row shrinks to fit on one line, and each line's chips sit just
    under its bubble's tail (smaller, when there are too many for the band). Runs once, after fonts load. */
function fit(ui) {
  if (ui.mode !== "reel") return;
  // a text's natural width in em (flex: none, so the row's own width cannot squeeze it first)
  const em = (node) => {
    Object.assign(node.style, { fontSize: "100px", flex: "none" });
    const width = node.getBoundingClientRect().width / 100;
    node.style.flex = "";
    return width;
  };
  for (const row of ui.rows) {
    const size = rowFit(em(row.text), em(row.meaning), ROW_ROOM, row.base);
    row.height = Math.round(size.textPx * ROW_LEADING);
    row.text.style.fontSize = `${size.textPx}px`;
    row.meaning.style.fontSize = `${size.meaningPx}px`;
    Object.assign(row.node.style, { height: `${row.height}px`, lineHeight: `${row.height}px`, borderRadius: `${row.height / 2}px` });
  }
  for (const b of ui.bubbles) {
    if (!b.chips.length) continue;
    // from y 1000 down; a stack too tall for that rises towards the tail, and only then shrinks
    const highest = BUBBLE_TOP + b.body.offsetHeight + TAIL_PX + CHIP_AIR, height = b.chipBox.offsetHeight;
    const top = Math.max(highest, Math.min(CHIP_TOP, CHIP_BOTTOM - height));
    b.chipBox.style.top = `${top}px`;
    b.chipScale = Math.max(0.4, Math.min(1, (CHIP_BOTTOM - top) / height));
  }
}

function applyCharacter(c, ms, doc, line, prevLine) {
  const delay = DROP_DELAY[c.side] ?? 150;
  const drop = dropIn(ms, delay);
  const landed = delay + DROP_MS;
  const idle = bob(ms, c.index) * fade(ms, landed, 300);
  const speaking = line && line.speaker === c.id;
  const replay = speaking && line.slowStartMs != null && ms >= line.slowStartMs;
  const h = speaking ? hop(ms, replay ? line.slowStartMs : line.startMs) : { y: 0, sx: 1, sy: 1 };
  // a listener leans towards the speaker; a thinking speaker tilts its head away
  const inward = c.side === "left" ? 1 : -1;
  const towards = (l) => (!l ? 0 : l.speaker !== c.id ? inward * TILT_DEG : -inward * emotionParts(l.emotion, false).tiltDeg);
  const tilt = line ? lerp(towards(prevLine), towards(line), fade(ms, line.startMs, 300)) : 0;
  const y = drop.y + idle + h.y;
  c.box.style.visibility = ms < delay ? "hidden" : "visible";
  c.box.style.transform =
    `translateY(${y.toFixed(2)}px) rotate(${tilt.toFixed(3)}deg) scale(${(drop.scale * h.sx).toFixed(4)}, ${(drop.scale * h.sy).toFixed(4)})`;
  // the shadow stays on the floor: it shrinks and pales while the character is in the air
  const air = clamp01(-Math.min(0, h.y + idle) / 60);
  const there = clamp01((ms - delay) / DROP_MS);
  c.shadow.style.opacity = (there * (1 - 0.5 * air)).toFixed(3);
  c.shadow.style.transform = `scale(${(1 - 0.25 * air).toFixed(3)})`;

  const blink = blinking(ms, c.seed);
  const talking = speaking && (replay
    ? mouthOpen(ms, slowWords(line), SLOW_FLAP_MS)
    : mouthOpen(ms, line.words.length ? line.words : [{ startMs: line.startMs, endMs: line.endMs }]));
  const face = emotionParts(speaking ? line.emotion : "neutral", talking);
  // the hook's greeting (and the cast sheet) is happy too
  if ((doc.mode === "reel" && ms >= landed && ms < (doc.hookMs || 0)) || (!line && doc.mode === "cast" && ms >= landed)) {
    if (doc.mode === "reel") face.eyes = "happy";
    if (!talking) face.mouth = "smile";
  }
  // a part the character does not have falls back to the neutral one
  const eyes = { happy: c.eyesHappy, surprised: c.eyesSurprised }[face.eyes] || c.eyesOpen;
  for (const node of [c.eyesOpen, c.eyesHappy, c.eyesSurprised]) show(node, !blink && node === eyes);
  show(c.eyesClosed, blink);
  const mouth = { open: c.mouthOpen, smile: c.mouthSmile }[face.mouth] || c.mouthClosed;
  for (const node of [c.mouthOpen, c.mouthSmile, c.mouthClosed]) show(node, node === mouth);
  show(c.sweat, face.extras.includes("sweat"));

  const pop = speaking && face.emoji ? emojiPop(ms, line.startMs) : { y: 0, scale: 0, alpha: 0 };
  if (pop.alpha > 0 && c.emojiText !== face.emoji) c.emoji.textContent = c.emojiText = face.emoji;
  c.emoji.style.opacity = pop.alpha.toFixed(3);
  c.emoji.style.transform = `translateY(${(pop.y + y).toFixed(1)}px) scale(${pop.scale.toFixed(4)})`;
  if (c.arm) {
    const wave = speaking ? Math.sin(Math.PI * clamp01((ms - line.startMs) / 600)) : 0;
    c.arm.style.transform = `rotate(${(c.armSign * 24 * wave).toFixed(2)}deg)`;
  }
}

function applyBubble(b, line, ms, state) {
  const { bubble } = b;
  if (state === null) {
    bubble.style.opacity = "0";
    return;
  }
  bubble.style.opacity = state.opacity.toFixed(3);
  bubble.style.transformOrigin = state.origin || "";
  bubble.style.transform = `translateY(${state.y.toFixed(1)}px) scale(${state.scale.toFixed(4)})`;
  // the slow replay runs the karaoke again from the start, at its own pace
  const replay = b.slow && ms >= line.slowStartMs;
  const lit = litChars(ms, b.chars.length, replay ? b.slow.words : b.spoken);
  if (lit !== b.lit) {
    b.chars.forEach((node, i) => node.classList.toggle("lit", i < lit));
    for (const rt of b.rts) rt.node.classList.toggle("lit", rt.first < lit);
    b.lit = lit;
  }
  const m = fade(ms, line.meaningMs, 250);
  b.meaning.style.opacity = m.toFixed(3);
  b.meaning.style.transform = `translateY(${((1 - m) * 14).toFixed(1)}px)`;
  if (b.slow) {
    // it pops in as the replay starts and fades in the 200 ms after it ends
    const on = backOut(clamp01((ms - line.slowStartMs) / SLOW_TAG_MS));
    const alpha = slowActive(ms, line) ? clamp01(on * 1.5) : ms >= line.slowEndMs ? 1 - fade(ms, line.slowEndMs, 200) : 0;
    b.slow.tag.style.opacity = alpha.toFixed(3);
    b.slow.tag.style.transform = `scale(${lerp(0.4, 1, on).toFixed(4)}) rotate(-4deg)`;
  }
}

/** The line's key chips pop in as the karaoke reaches each word, and leave with the bubble. */
function applyChips(b, line, ms, state) {
  if (!b.chips.length) return;
  b.chipBox.style.opacity = state === null ? "0" : state.opacity.toFixed(3);
  if (state === null) return;
  b.chipBox.style.transform = `translateY(${(state.y < 0 ? state.y * 0.5 : 0).toFixed(1)}px) scale(${(b.chipScale || 1).toFixed(4)})`;
  for (const { key, node } of b.chips) {
    const p = keyVisible(ms, line, key, b.chars.length);
    const e = backOut(p);
    node.style.opacity = clamp01(p * 2.5).toFixed(3);
    node.style.transform = `translateY(${((1 - e) * 26).toFixed(1)}px) scale(${lerp(0.55, 1, e).toFixed(4)})`;
  }
}

function renderFrame(ui, doc, t) {
  const ms = Math.max(0, t * 1000);
  const lines = doc.lines || [];
  const a = activeLine(lines, ms);
  const line = a >= 0 ? lines[a] : null;
  const prevLine = a > 0 ? lines[a - 1] : null;
  for (const c of ui.chars) applyCharacter(c, ms, doc, line, prevLine);
  if (doc.mode !== "reel") return;

  ui.bar.style.transform = `scaleX(${progress(ms, doc.duration * 1000).toFixed(5)})`;
  const out = fade(ms, (doc.hookMs || 0) - 300, 300);
  const pop = clamp01(ms / 400);
  ui.hook.style.opacity = (1 - out).toFixed(3);
  ui.hook.style.transform = `translateY(${(-50 * out).toFixed(1)}px) scale(${(lerp(0.9, 1, pop >= 1 ? 1 : 1 - Math.pow(1 - pop, 3)) * (1 - 0.08 * out)).toFixed(4)})`;
  if (ui.scene) ui.scene.style.transform = `rotate(${(7 * Math.sin((2 * Math.PI * ms) / 1200)).toFixed(2)}deg)`;

  // the active bubble pops in; the one before it shrinks up into the history as it goes
  // (the old one is nearly gone before the new one shows, so the two never read on top of each other)
  const exit = line ? fade(ms, line.startMs, EXIT_MS) : 0;
  const enter = line ? fade(ms, line.startMs + (a > 0 ? ENTER_DELAY_MS : 0), ENTER_MS) : 0;
  ui.bubbles.forEach((b, k) => {
    let state = null;
    if (k === a) state = { opacity: enter, y: 28 * (1 - enter), scale: lerp(0.9, 1, enter) };
    else if (k === a - 1 && exit < 1) state = { opacity: (1 - exit) ** 2, y: -60 * exit, scale: lerp(1, 0.7, exit), origin: "50% 0" };
    applyBubble(b, lines[k], ms, state);
    applyChips(b, lines[k], ms, state && k === a ? { opacity: 1, y: 0 } : state);
  });

  const slide = line ? fade(ms, line.startMs, SLIDE_MS) : 0;
  const shown = new Set(history(lines, a));
  if (a >= 3) shown.add(a - 3); // leaving through the top
  const before = rowTops(ui.rows, a - 1), after = rowTops(ui.rows, a);
  ui.rows.forEach(({ node }, k) => {
    if (!shown.has(k)) {
      node.style.opacity = "0";
      return;
    }
    const slot = a - 1 - k;
    const from = slot === 0 ? BUBBLE_TOP + 10 : before.get(k);
    const top = lerp(from, after.get(k), slide);
    const alpha = slot === 0 ? 0.45 * slide : slot === 2 ? 0.45 * (1 - slide) : 0.45;
    node.style.opacity = alpha.toFixed(3);
    node.style.transform = `translateY(${top.toFixed(1)}px)`;
  });
}

try {
  const doc = JSON.parse(document.getElementById("reel-data").textContent);
  const root = document.getElementById("root");
  const ui = build(doc, root);
  if (document.fonts && document.fonts.ready) await document.fonts.ready;
  fit(ui);
  const renderAt = (t) => renderFrame(ui, doc, t);
  window.addEventListener("hf-seek", (event) => {
    try {
      renderAt(event.detail.time);
    } catch (error) {
      console.error(`dialoguereel: frame at ${event.detail.time}s could not be drawn`, error);
    }
  });
  window.__reelRenderAt = renderAt;
  const preview = new URLSearchParams(window.location.search).get("t");
  renderAt(preview !== null && Number.isFinite(Number(preview)) ? Number(preview) : window.__hfThreeTime || 0);
} catch (error) {
  // the first line only (the error's name and message): the rest of a stack names local paths
  window.__reelError = String((error && error.stack) || error).split("\n")[0];
  console.error("dialoguereel: the reel could not be built", error);
} finally {
  // HyperFrames reads window.__hfStartupError before capture and fails the render with its text
  if (window.__reelError) window.__hfStartupError = window.__reelError;
  done();
}
