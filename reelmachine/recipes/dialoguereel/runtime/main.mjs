// reelmachine/recipes/dialoguereel/runtime/main.mjs
// Builds the reel's DOM once from #reel-data, then sets every transform, opacity and class from the time
// HyperFrames seeks to (pure.mjs decides; this file only applies). HyperFrames waits on
// window.__hf.buildReady (set inline in index.html) before capturing. Opened directly in a browser,
// index.html?t=3.2 shows the frame at 3.2 s.
import { activeLine, dropIn, hop, mouthOpen, blinking, litChars, fade, history, progress, bob, seedOf, fitFont, flag, clamp01, DROP_MS, partId,
  backOut, keyVisible, emotionParts, emojiPop, slowActive, slowWords, rowFit, SLOW_FLAP_MS,
  section, shiftLine, spokenOf, bridgeSlide, countdown, RING_IN_MS, recapCard, nod, loopBack, waveLevel, WAVE_BARS, BRIDGE_MS, RECAP_MS, LOOP_MS, DROP_PX } from "./pure.mjs";

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
const STAGE_OUT_MS = 250; // the dialogue leaves in the gap before the recap (or the quiz) ...
const STAGE_LEAD_MS = 300; // ... starting this long before it (the gap is 350 ms)
const POP_MS = 300; // a section's title pops in
const CTA_AT_MS = 500, FOLLOW_AT_MS = 850; // after the quiz reveal
const RING_BURST_MS = 260;
const CARD_ROOM = 467 - 2 * 28 - 10; // a recap card's text width: less its padding and its accent edge
const LEVEL_ROOM = 96; // and less the level tag
const QUIZ_MEANING_MAX = 300;
const easeIn = (p) => p * p;

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

/** A key's word with the ruby it was written with (a recap card, a chip). */
function buildRuns(word, runs) {
  for (const run of runs) {
    if (run.r) el("rt", null, el("ruby", null, word, run.t), run.r);
    else el("span", null, word, run.t);
  }
}

const hasRuby = (key) => (key.runs || []).some((run) => run.r);

/** A key word's chip: the word (its own ruby, else its reading above it, as in the bubble), a dot, the meaning
    and its JLPT level. */
function buildChip(parent, key) {
  const chip = el("div", "chip", parent);
  const word = el("span", "chip-word", chip);
  if (hasRuby(key)) {
    buildRuns(word, key.runs);
  } else if (key.reading && key.reading !== key.text) {
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

/** A line's text as one span per character (for the karaoke), ruby above its kanji runs, its keys underlined. */
function buildRubyText(parent, line, width) {
  const text = el("div", "text rtext", parent);
  const plain = line.runs.map((r) => r.t).join("");
  text.style.fontSize = `${fitFont(plain, width)}px`;
  const chars = [];
  const rts = [];
  for (const run of line.runs) {
    const host = run.r ? el("ruby", null, text) : text;
    const first = chars.length;
    for (const ch of run.t) chars.push(el("span", "ch", host, ch));
    if (run.r) rts.push({ node: el("rt", null, host, run.r), first });
  }
  for (const key of line.keys || []) for (let i = key.start; i < key.end && i < chars.length; i++) chars[i].classList.add("key");
  return { text, plain, chars, rts };
}

/** Lights the first `lit` characters (and the ruby over them); `state.lit` remembers what is lit. */
function lightChars(state, lit) {
  if (lit === state.lit) return;
  state.chars.forEach((node, i) => node.classList.toggle("lit", i < lit));
  for (const rt of state.rts) rt.node.classList.toggle("lit", rt.first < lit);
  state.lit = lit;
}

function buildBubble(stage, line, side) {
  const bubble = el("div", `bubble ${side}`, stage);
  el("div", "tail", bubble);
  const body = el("div", "body", bubble);
  const { chars, rts } = buildRubyText(body, line);
  const keys = line.keys || [];
  const reading = line.reading ? el("div", "reading", body, line.reading) : null;
  const meaning = el("div", "meaning", body, line.meaning);
  const slow = line.slowStartMs != null ? { words: slowWords(line), tag: el("div", "slow-tag", bubble, "🐢 0.75×") } : null;
  const chipBox = el("div", "chips", stage);
  // in the order the karaoke reaches them
  const chips = [...keys].sort((x, y) => x.start - y.start).map((key) => ({ key, node: buildChip(chipBox, key) }));
  return { bubble, body, chars, rts, reading, meaning, slow, chipBox, chips, lit: -1, listening: false };
}

/** A recap card: the word (with its ruby), its reading, its meaning and, for Japanese, its JLPT level. */
function buildCard(parent, entry) {
  const card = el("div", "card", parent);
  const word = el("div", "card-word", card);
  buildRuns(word, entry.runs);
  if (entry.reading && entry.reading !== entry.text) el("div", "card-reading", card, entry.reading);
  el("div", "card-meaning", card, entry.meaning);
  const level = Number.isInteger(entry.level) ? el("div", "card-level", card, `N${entry.level}`) : null;
  return { node: card, word, level };
}

/** The screens around the dialogue: the listen-first banner, the bridge card, the recap and the quiz. */
function buildSections(ui, doc, root) {
  const strings = doc.strings || {};
  const lines = doc.lines || [];
  // the listen pass replays each line at its listen window; the quiz answer at the reveal
  ui.listenLines = (doc.listen || []).slice(0, lines.length).map((w, i) => shiftLine(lines[i], w.startMs));
  if (ui.listenLines.length) {
    ui.banner = el("div", "listen-banner", root, strings.listen || "");
    ui.wave = el("div", "wave", root);
    ui.bars = Array.from({ length: WAVE_BARS }, () => el("div", "wave-bar", ui.wave));
  }
  if (doc.bridgeMs != null) {
    ui.bridge = el("div", "bridge", root);
    ui.bridgeText = el("div", "bridge-text", ui.bridge, strings.bridge || "");
  }
  if (doc.recapMs != null) {
    ui.recap = el("div", "recap", root);
    ui.recapTitle = el("div", "recap-title", ui.recap, strings.recap || "");
    const grid = el("div", "cards", ui.recap);
    ui.cards = (doc.recap || []).map((entry) => buildCard(grid, entry));
  }
  const quiz = doc.quiz && lines[doc.quiz.line] ? doc.quiz : null;
  if (quiz) {
    const line = lines[quiz.line];
    const q = { window: quiz, line: shiftLine(line, quiz.revealMs) };
    q.box = el("div", "quiz", root);
    q.label = el("div", "quiz-label", q.box, strings.quiz || "");
    q.meaning = el("div", "quiz-meaning", q.box, line.meaning);
    const slot = el("div", "quiz-slot", q.box);
    q.count = el("div", "quiz-count", slot);
    q.ring = el("div", "quiz-ring", q.count);
    q.num = el("div", "quiz-num", q.count, "3");
    q.n = null;
    q.answer = el("div", "quiz-answer", slot);
    q.card = el("div", "answer-card", q.answer);
    Object.assign(q, buildRubyText(q.card, line, 820), { lit: -1 });
    if (line.reading) el("div", "reading", q.card, line.reading);
    q.cta = el("div", "cta", q.box, strings.cta || "");
    q.follow = el("div", "follow", q.box, strings.follow || "");
    ui.quiz = q;
  }
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
  ui.listenLines = [];
  if (doc.mode !== "reel") return ui;

  const bar = el("div", "progress", root);
  ui.bar = el("div", "fill", bar);
  const badgeText = [doc.level, flag(doc.language)].filter(Boolean).join(" · ");
  ui.badge = badgeText ? el("div", "badge", root, badgeText) : null;

  ui.hook = el("div", "hook", root);
  ui.scene = doc.scene ? el("div", "scene", ui.hook, doc.scene) : null;
  el("div", "title", ui.hook, doc.title);
  if (doc.prompt) el("div", "sub", ui.hook, doc.prompt);

  const stage = ui.stage = el("div", "stage", root);
  ui.rows = doc.lines.map((line) => {
    const node = el("div", `row ${ui.bySide[line.speaker] || "left"}`, stage);
    const plain = line.runs.map((r) => r.t).join("");
    const base = { textPx: Math.round(ROW_SCALE * fitFont(plain)), meaningPx: Math.round(ROW_SCALE * MEANING_PX) };
    return { node, base, text: el("span", "row-text", node, plain), meaning: el("span", "row-meaning", node, line.meaning), height: 0 };
  });
  ui.bubbles = doc.lines.map((line) => buildBubble(stage, line, ui.bySide[line.speaker] || "left"));
  buildSections(ui, doc, root);
  return ui;
}

/** Sizes that need the fonts: each history row shrinks to fit on one line, and each line's chips sit just
    under its bubble's tail (smaller, when there are too many for the band). Runs once, after fonts load. */
function fit(ui) {
  if (ui.mode !== "reel") return;
  // a text's natural width in em (flex: none, so the row's own width cannot squeeze it first)
  const em = (node) => {
    Object.assign(node.style, { fontSize: "100px", flex: "none", maxWidth: "none" });
    const width = node.getBoundingClientRect().width / 100;
    Object.assign(node.style, { flex: "", maxWidth: "" });
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
  // a recap word is as big as its card allows (at most 84 px, at least 34 px, past that an ellipsis)
  for (const card of ui.cards || []) {
    const room = CARD_ROOM - (card.level ? LEVEL_ROOM : 0);
    card.word.style.fontSize = `${Math.max(34, Math.min(84, Math.floor(room / em(card.word))))}px`;
  }
  // the quiz question fits in four lines' worth of height
  if (ui.quiz) {
    let size = 104;
    ui.quiz.meaning.style.fontSize = `${size}px`;
    while (ui.quiz.meaning.offsetHeight > QUIZ_MEANING_MAX && size > 44) {
      size -= 4;
      ui.quiz.meaning.style.fontSize = `${size}px`;
    }
  }
}

/** `pose` is what the reel around the line asks of the characters: `restFrom` (the line they turn back from,
    at `restMs`, once no one is speaking), `nodMs` (the recap's start), `happy`, and `loop` (0..1, sinking back
    below the floor for the first frame). */
function applyCharacter(c, ms, doc, line, prevLine, pose = {}) {
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
  const tilt = line ? lerp(towards(prevLine), towards(line), fade(ms, line.startMs, 300))
    : pose.restFrom ? towards(pose.restFrom) * (1 - fade(ms, pose.restMs, 300)) : 0;
  const n = pose.nodMs != null ? nod(ms, pose.nodMs, c.index) : { y: 0, sy: 1 };
  const loop = pose.loop || 0;
  const sink = DROP_PX * easeIn(loop), shrink = lerp(1, 0.8, loop);
  const y = drop.y + idle + h.y + n.y + sink;
  c.box.style.visibility = ms < delay || loop >= 1 ? "hidden" : "visible";
  c.box.style.transform = `translateY(${y.toFixed(2)}px) rotate(${tilt.toFixed(3)}deg) ` +
    `scale(${(drop.scale * shrink * h.sx).toFixed(4)}, ${(drop.scale * shrink * h.sy * n.sy).toFixed(4)})`;
  // the shadow stays on the floor: it shrinks and pales while the character is in the air
  const air = clamp01(-Math.min(0, h.y + idle) / 60);
  const there = clamp01((ms - delay) / DROP_MS) * (1 - loop);
  c.shadow.style.opacity = (there * (1 - 0.5 * air)).toFixed(3);
  c.shadow.style.transform = `scale(${(1 - 0.25 * air).toFixed(3)})`;

  const blink = blinking(ms, c.seed);
  const talking = speaking && (replay
    ? mouthOpen(ms, slowWords(line), SLOW_FLAP_MS)
    : mouthOpen(ms, line.words.length ? line.words : [{ startMs: line.startMs, endMs: line.endMs }]));
  const face = emotionParts(speaking ? line.emotion : "neutral", talking);
  // the hook's greeting (and the cast sheet) is happy too
  if ((doc.mode === "reel" && ms >= landed && (ms < (doc.hookMs || 0) || pose.happy)) || (!line && doc.mode === "cast" && ms >= landed)) {
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

/** `line` carries the times of the pass being played: the dialogue, or the listen pass (`listening`: the text
    blurred, no reading, meaning or slow tag). */
function applyBubble(b, line, ms, state, listening = false) {
  const { bubble } = b;
  if (state === null) {
    bubble.style.opacity = "0";
    return;
  }
  if (b.listening !== listening) bubble.classList.toggle("listening", (b.listening = listening));
  bubble.style.opacity = state.opacity.toFixed(3);
  bubble.style.transformOrigin = state.origin || "";
  bubble.style.transform = `translateY(${state.y.toFixed(1)}px) scale(${state.scale.toFixed(4)})`;
  // the slow replay runs the karaoke again from the start, at its own pace
  const replay = b.slow && line.slowStartMs != null && ms >= line.slowStartMs;
  lightChars(b, litChars(ms, b.chars.length, replay ? b.slow.words : spokenOf(line)));
  if (listening) return;
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

const px = (v) => `${v.toFixed(1)}px`;

/** Shows `node` only while `alpha` > 0. */
function reveal(node, alpha, transform) {
  node.style.visibility = alpha > 0 ? "visible" : "hidden";
  node.style.opacity = alpha.toFixed(3);
  if (transform !== undefined) node.style.transform = transform;
}

/** A pop-in: 0..1 progress from `atMs` over `durMs` to an alpha and a back-out scale from `from`. */
function popIn(ms, atMs, from = 0.6, durMs = POP_MS) {
  const p = clamp01((ms - atMs) / durMs);
  return { p, alpha: clamp01(p * 2), scale: lerp(from, 1, backOut(p)) };
}

/** The listen-first banner over the blurred pass with a sound wave that moves with the voice (`line` is the
    listen line being played), and the "now with subtitles" card that wipes across after it. */
function applyListen(ui, doc, ms, line) {
  if (ui.banner) {
    const first = ui.listenLines[0].startMs;
    const p = clamp01((ms - first) / POP_MS);
    const alpha = fade(ms, first, POP_MS) * (1 - fade(ms, doc.bridgeMs, 200));
    reveal(ui.banner, alpha,
      `translateY(${px(-40 * (1 - backOut(p)))}) scale(${(1 + 0.012 * Math.sin((2 * Math.PI * ms) / 900)).toFixed(4)})`);
    reveal(ui.wave, alpha, `scale(${lerp(0.6, 1, backOut(p)).toFixed(4)})`);
    const words = line ? spokenOf(line) : [];
    ui.bars.forEach((bar, i) => { bar.style.transform = `scaleY(${waveLevel(ms, words, i).toFixed(4)})`; });
  }
  if (ui.bridge) {
    const x = bridgeSlide(ms, doc.bridgeMs);
    reveal(ui.bridge, Math.abs(x) < 1 ? 1 : 0, `translateX(${px(x * 1240)}) rotate(-4deg)`);
    ui.bridgeText.style.transform = `translateX(${px(x * 260)})`;
  }
}

/** The recap: its title pops in, the cards rise in one by one, and all of it leaves before the quiz. */
function applyRecap(ui, doc, ms) {
  if (!ui.recap) return;
  const at = doc.recapMs;
  const on = ms >= at && ms < at + RECAP_MS;
  ui.recap.style.visibility = on ? "visible" : "hidden";
  if (!on) return;
  const title = popIn(ms, at, 0.7), out = fade(ms, at + RECAP_MS - 260, 260);
  reveal(ui.recapTitle, title.alpha * (1 - out), `translateY(${px(-40 * out)}) scale(${title.scale.toFixed(4)})`);
  ui.cards.forEach((card, i) => {
    const s = recapCard(ms, at, i);
    reveal(card.node, s.alpha, `translateY(${px(s.y)}) scale(${lerp(0.9, 1, s.alpha).toFixed(4)})`);
  });
}

/** The quiz: the question and its meaning, the 3-2-1 ring; at the reveal the answer (spoken, with its karaoke),
    then the call to comment and to follow. It all shrinks away as the reel loops. */
function applyQuiz(ui, ms, loop) {
  const q = ui.quiz;
  if (!q) return;
  const w = q.window;
  const on = ms >= w.startMs && loop < 0.5;
  q.box.style.visibility = on ? "visible" : "hidden";
  if (!on) return;
  q.box.style.opacity = (1 - clamp01(loop * 2)).toFixed(3);
  q.box.style.transform = `scale(${lerp(1, 0.9, loop).toFixed(4)})`;
  const label = popIn(ms, w.startMs), meaning = popIn(ms, w.startMs + 120, 0.8);
  reveal(q.label, label.alpha, `scale(${label.scale.toFixed(4)})`);
  reveal(q.meaning, meaning.alpha, `translateY(${px(30 * (1 - meaning.p))}) scale(${meaning.scale.toFixed(4)})`);

  const cd = countdown(ms, w);
  const shown = Math.max(1, cd.n); // the 1 bursts away with the ring at the reveal
  if (shown !== q.n) q.num.textContent = String((q.n = shown));
  q.ring.style.setProperty("--p", cd.ring.toFixed(4));
  // the number pops on every second; at the reveal the ring bursts outwards and goes
  const tick = popIn(ms, w.startMs + Math.floor(Math.max(0, ms - w.startMs) / 1000) * 1000, 1.45, 220);
  q.num.style.transform = `scale(${tick.scale.toFixed(4)})`;
  const count = popIn(ms, w.startMs + RING_IN_MS, 0.5), burst = clamp01((ms - w.revealMs) / RING_BURST_MS);
  reveal(q.count, count.alpha * (1 - burst), `scale(${(count.scale * lerp(1, 1.5, burst)).toFixed(4)})`);

  const answer = popIn(ms, w.revealMs, 0.55, 320);
  reveal(q.answer, answer.alpha);
  q.card.style.transform = `scale(${answer.scale.toFixed(4)})`;
  lightChars(q, ms >= w.revealMs ? litChars(ms, q.chars.length, spokenOf(q.line)) : 0);
  const sway = 5 * Math.sin((2 * Math.PI * ms) / 700);
  const cta = popIn(ms, w.revealMs + CTA_AT_MS, 0.6), follow = popIn(ms, w.revealMs + FOLLOW_AT_MS, 0.8);
  reveal(q.cta, cta.alpha, `translateY(${px(36 * (1 - cta.p) + sway * cta.p)}) scale(${cta.scale.toFixed(4)})`);
  reveal(q.follow, follow.alpha, `translateY(${px(24 * (1 - follow.p))}) scale(${follow.scale.toFixed(4)})`);
}

/** The lines the bubbles and the speaker follow at `ms`: the listen pass until its bridge has gone, then the dialogue. */
function passAt(ui, doc, ms) {
  if (ui.listenLines.length && ms < doc.bridgeMs + BRIDGE_MS) return { pass: ui.listenLines, listening: true };
  return { pass: doc.lines || [], listening: false };
}

function renderFrame(ui, doc, t) {
  const ms = Math.max(0, t * 1000);
  if (doc.mode !== "reel") {
    for (const c of ui.chars) applyCharacter(c, ms, doc, null, null);
    return;
  }
  const durationMs = doc.duration * 1000;
  const loop = loopBack(ms, durationMs);
  const { pass, listening } = passAt(ui, doc, ms);
  const a = activeLine(pass, ms);
  // the dialogue's first line turns from the listen pass's last
  const prevOf = (k) => (k > 0 ? pass[k - 1] : !listening && ui.listenLines.length ? ui.listenLines[ui.listenLines.length - 1] : null);
  // the dialogue leaves for the recap or the quiz; without them it stays until the loop
  const next = doc.recapMs ?? (doc.quiz ? doc.quiz.startMs : null);
  const stageOutMs = next != null ? next - STAGE_LEAD_MS : durationMs - LOOP_MS;
  let line = a >= 0 ? pass[a] : null;
  let prevLine = a >= 0 ? prevOf(a) : null;
  const pose = { loop };
  if (!listening && ms >= stageOutMs) {
    Object.assign(pose, { restFrom: line, restMs: stageOutMs });
    line = prevLine = null;
  }
  if (section(ms, doc) === "recap") Object.assign(pose, { nodMs: doc.recapMs, happy: true });
  if (ui.quiz && ms >= ui.quiz.window.revealMs) {
    line = ui.quiz.line; // the answer's speaker says it
    prevLine = null;
  }
  for (const c of ui.chars) applyCharacter(c, ms, doc, line, prevLine, pose);

  ui.bar.style.transform = `scaleX(${progress(ms, durationMs).toFixed(5)})`;
  // the hook card; in the loop's last half second it comes back as the first frame shows it
  const out = fade(ms, (doc.hookMs || 0) - 300, 300);
  const pop = clamp01(ms / 400);
  if (loop > 0) {
    // after the quiz has gone, so the two never read on top of each other
    ui.hook.style.opacity = clamp01((loop - 0.5) / 0.5).toFixed(3);
    ui.hook.style.transform = `translateY(0px) scale(${lerp(0.8, 0.9, 1 - Math.pow(1 - loop, 3)).toFixed(4)})`;
  } else {
    ui.hook.style.opacity = (1 - out).toFixed(3);
    ui.hook.style.transform = `translateY(${(-50 * out).toFixed(1)}px) scale(${(lerp(0.9, 1, pop >= 1 ? 1 : 1 - Math.pow(1 - pop, 3)) * (1 - 0.08 * out)).toFixed(4)})`;
  }
  // the scene's wobble is in phase with the first frame's as the reel loops
  if (ui.scene) ui.scene.style.transform = `rotate(${(7 * Math.sin((2 * Math.PI * (loop > 0 ? ms - durationMs : ms)) / 1200)).toFixed(2)}deg)`;

  const gone = fade(ms, listening ? doc.bridgeMs : stageOutMs, listening || next == null ? 200 : STAGE_OUT_MS);
  ui.stage.style.opacity = (1 - gone).toFixed(3);
  ui.stage.style.transform = `translateY(${px(listening ? 0 : -40 * gone)})`;
  applyListen(ui, doc, ms, listening && a >= 0 ? pass[a] : null);
  applyRecap(ui, doc, ms);
  applyQuiz(ui, ms, loop);
  if (gone >= 1) return;

  // the active bubble pops in; the one before it shrinks up into the history as it goes
  // (the old one is nearly gone before the new one shows, so the two never read on top of each other)
  const current = a >= 0 ? pass[a] : null;
  const exit = current ? fade(ms, current.startMs, EXIT_MS) : 0;
  const enter = current ? fade(ms, current.startMs + (a > 0 ? ENTER_DELAY_MS : 0), ENTER_MS) : 0;
  ui.bubbles.forEach((b, k) => {
    let state = null;
    if (k === a) state = { opacity: enter, y: 28 * (1 - enter), scale: lerp(0.9, 1, enter) };
    else if (k === a - 1 && exit < 1) state = { opacity: (1 - exit) ** 2, y: -60 * exit, scale: lerp(1, 0.7, exit), origin: "50% 0" };
    applyBubble(b, pass[k], ms, state, listening);
    // the listen pass shows no chips and no history
    applyChips(b, pass[k], ms, listening ? null : state && k === a ? { opacity: 1, y: 0 } : state);
  });

  const slide = current ? fade(ms, current.startMs, SLIDE_MS) : 0;
  const shown = new Set(listening ? [] : history(pass, a));
  if (!listening && a >= 3) shown.add(a - 3); // leaving through the top
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
