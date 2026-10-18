// reelmachine/recipes/dialoguereel/runtime/pure.mjs
// Everything a frame depends on, as pure functions of time (ms). No DOM, no clock, no unseeded randomness:
// the same time always gives the same frame, which is what lets HyperFrames seek anywhere.

export const clamp01 = (x) => (x <= 0 ? 0 : x >= 1 ? 1 : x);
const smooth = (p) => p * p * (3 - 2 * p);
const lerp = (a, b, p) => a + (b - a) * p;

export const backOut = (t) => {
  const c1 = 1.70158, c3 = c1 + 1;
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
};

/** The index of the last line whose startMs <= ms, else -1. */
export function activeLine(lines, ms) {
  let index = -1;
  for (let i = 0; i < lines.length; i++) if (lines[i].startMs <= ms) index = i;
  return index;
}

// a character's element id on the page; must match svg.part_id (character ids have no ".")
export const partId = (charId, name) => `c_${charId}.${name}`;

export const DROP_MS = 450;
export const DROP_PX = 500;

/** A character rising into place: 500 px below to rest over 450 ms, overshooting a little (backOut). */
export function dropIn(ms, delayMs) {
  const p = clamp01((ms - delayMs) / DROP_MS);
  if (p >= 1) return { y: 0, scale: 1 };
  if (p <= 0) return { y: DROP_PX, scale: 0.8 };
  const e = backOut(p);
  return { y: DROP_PX * (1 - e), scale: lerp(0.8, 1, e) };
}

export const HOP_MS = 280;
export const HOP_PX = 36;

/** A squash-and-stretch hop at a line's start: crouch, leap (stretched), land (squashed), rest. */
export function hop(ms, startMs) {
  const d = ms - startMs;
  if (d < 0 || d >= HOP_MS) return { y: 0, sx: 1, sy: 1 };
  if (d < 50) {
    const k = smooth(d / 50);
    return { y: 0, sx: 1 + 0.1 * k, sy: 1 - 0.12 * k };
  }
  if (d < 220) {
    const u = (d - 50) / 170;
    const c = Math.cos(Math.PI * u);
    return { y: -HOP_PX * Math.sin(Math.PI * u), sx: 1 - 0.07 * c, sy: 1 + 0.1 * c };
  }
  const k = smooth((d - 220) / 60);
  return { y: 0, sx: lerp(1.07, 1, k), sy: lerp(0.9, 1, k) };
}

export const FLAP_MS = 90;

/** Whether the mouth is open: inside a spoken span, open on even 90 ms ticks (`flapMs`) from the span's start. */
export function mouthOpen(ms, words, flapMs = FLAP_MS) {
  for (const w of words) {
    if (ms >= w.startMs && ms < w.endMs) return Math.floor((ms - w.startMs) / flapMs) % 2 === 0;
  }
  return false;
}

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

export const BLINK_MS = 120;

/** Eyes closed for 120 ms, then open for 2000–5000 ms, the intervals drawn from `seed`. */
export function blinking(ms, seed) {
  const rand = mulberry32(seed);
  let at = 700 + rand() * 2300; // the first blink comes once the character has landed
  while (at <= ms) {
    if (ms < at + BLINK_MS) return true;
    at += BLINK_MS + 2000 + rand() * 3000;
  }
  return false;
}

/** How many of `total` characters are lit: the spans share them in proportion to their text length. */
export function litChars(ms, total, words) {
  const sizes = words.map((w) => Math.max(1, [...w.text].length));
  const all = sizes.reduce((a, b) => a + b, 0);
  if (!all) return 0;
  let lit = 0;
  words.forEach((w, i) => {
    const span = w.endMs - w.startMs;
    const p = span > 0 ? clamp01((ms - w.startMs) / span) : ms >= w.startMs ? 1 : 0;
    lit += sizes[i] * p;
  });
  return Math.min(total, Math.floor((total * lit) / all + 1e-9));
}

/** 0 before `atMs`, 1 after `atMs + durMs`, eased in between. */
export function fade(ms, atMs, durMs = 250) {
  return smooth(clamp01((ms - atMs) / durMs));
}

/** Up to two line indices before `index`, oldest first. */
export function history(lines, index) {
  const out = [];
  for (let i = Math.max(0, index - 2); i < index && i < lines.length; i++) out.push(i);
  return out;
}

export const progress = (ms, durationMs) => (durationMs > 0 ? clamp01(ms / durationMs) : 0);

export const BOB_PX = 6;
export const BOB_MS = 1600;

/** The idle bob: ±6 px over 1.6 s; character 1 moves opposite to character 0. */
export function bob(ms, index) {
  const y = BOB_PX * Math.sin((2 * Math.PI * ms) / BOB_MS + index * Math.PI);
  return Math.abs(y) < 1e-12 ? 0 : y;
}

const WIDE = /[ᄀ-ᇿ⺀-鿿가-힯豈-﫿＀-￯]/u;

/** The bubble's text size in px: one line when it fits at >= 72 px, else two (or three) balanced lines.
    `width` is the bubble's inner width (832 px) less a margin for letter-spacing and the text shadow. */
export function fitFont(text, width = 800) {
  let units = 0;
  for (const ch of text) units += WIDE.test(ch) ? 1 : ch === " " ? 0.3 : 0.56;
  if (units <= 0) return 100;
  for (const rows of [1, 2, 3]) {
    const size = Math.floor(width / ((units / rows) * (rows > 1 ? 1.08 : 1)));
    if (size >= 72 || rows === 3) return Math.max(60, Math.min(100, size));
  }
  return 60;
}

const FLAGS = { ja: "🇯🇵", ko: "🇰🇷", zh: "🇨🇳", es: "🇪🇸", fr: "🇫🇷", de: "🇩🇪", it: "🇮🇹", pt: "🇧🇷", en: "🇬🇧", vi: "🇻🇳", th: "🇹🇭", id: "🇮🇩" };

export const flag = (language) => FLAGS[String(language || "").toLowerCase().split(/[-_]/)[0]] || "";

// ------------------------------------------------------------ key words, emotions and slow replays

/** A line's spoken spans, or one span over the whole line when TTS gave none. */
export const spokenOf = (line) => (line.words && line.words.length ? line.words : [{ text: "", startMs: line.startMs, endMs: line.endMs }]);

export const KEY_POP_MS = 220;

/** A key chip's entry progress, 0..1: from the moment the karaoke lights the key's first character, over
    220 ms (the caller eases it with backOut). It stays at 1; the chip leaves with its bubble. */
export function keyVisible(ms, line, key, total) {
  const words = spokenOf(line);
  let lo = Math.min(...words.map((w) => w.startMs)), hi = Math.max(...words.map((w) => w.endMs));
  if (ms < lo || litChars(hi, total, words) <= key.start) return 0;
  // litChars only grows with time: the first whole ms at which it passes key.start
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (litChars(mid, total, words) > key.start) hi = mid;
    else lo = mid + 1;
  }
  return clamp01((ms - lo) / KEY_POP_MS);
}

const EMOTIONS = {
  neutral: { eyes: "open", smile: false, extras: [], emoji: "", tiltDeg: 0 },
  happy: { eyes: "happy", smile: true, extras: [], emoji: "❤️", tiltDeg: 0 },
  surprised: { eyes: "surprised", smile: false, extras: [], emoji: "❗", tiltDeg: 0 },
  sad: { eyes: "open", smile: false, extras: ["sweat"], emoji: "💦", tiltDeg: 0 },
  thinking: { eyes: "open", smile: false, extras: [], emoji: "❓", tiltDeg: 8 },
};

/** What a speaker's face shows for an emotion. `eyes` is open|happy|surprised and `mouth` open|closed|smile
    (a talking mouth is always open); `extras` are optional parts shown as well. */
export function emotionParts(emotion, talking) {
  const e = EMOTIONS[emotion] || EMOTIONS.neutral;
  return { eyes: e.eyes, mouth: talking ? "open" : e.smile ? "smile" : "closed", extras: [...e.extras], emoji: e.emoji, tiltDeg: e.tiltDeg };
}

export const EMOJI_MS = 600;
export const EMOJI_RISE_PX = 130;

/** An emotion emoji over the speaker's head: it pops up from nothing, rises 130 px and fades, in 600 ms. */
export function emojiPop(ms, startMs) {
  const p = (ms - startMs) / EMOJI_MS;
  if (!(p >= 0 && p < 1)) return { y: 0, scale: 0, alpha: 0 };
  const grow = clamp01(p / 0.4);
  return {
    y: -EMOJI_RISE_PX * (1 - Math.pow(1 - p, 2)),
    scale: lerp(0.3, 1, backOut(grow)),
    alpha: p < 0.12 ? p / 0.12 : p > 0.6 ? 1 - smooth((p - 0.6) / 0.4) : 1,
  };
}

export const SLOW_RATE = 0.75;
export const SLOW_FLAP_MS = Math.round(FLAP_MS / SLOW_RATE);

/** Whether `line`'s slow replay is playing. */
export const slowActive = (ms, line) =>
  line.slowStartMs != null && line.slowEndMs != null && ms >= line.slowStartMs && ms < line.slowEndMs;

/** The line's spoken spans moved into its slow replay, stretched by 1 / 0.75. */
export function slowWords(line) {
  if (line.slowStartMs == null) return [];
  if (!line.words || !line.words.length) return [{ text: "", startMs: line.slowStartMs, endMs: line.slowEndMs }];
  const at = (t) => line.slowStartMs + Math.round((t - line.startMs) / SLOW_RATE);
  return line.words.map((w) => ({ text: w.text, startMs: at(w.startMs), endMs: at(w.endMs) }));
}

export const ROW_MIN = 0.5;

/** A history row's two font sizes. `textLen` and `meaningLen` are the texts' widths in em; both shrink by the
    same factor until the row fits `widthPx`, but never below half their `base` size (past that, CSS ellipsis). */
export function rowFit(textLen, meaningLen, widthPx, base = { textPx: 70, meaningPx: 39 }) {
  const need = textLen * base.textPx + meaningLen * base.meaningPx;
  const k = need > 0 ? Math.max(ROW_MIN, Math.min(1, widthPx / need)) : 1;
  const size = (px) => (k >= 1 ? px : Math.max(Math.round(px * ROW_MIN), Math.floor(px * k)));
  return { textPx: size(base.textPx), meaningPx: size(base.meaningPx) };
}

// ------------------------------------------------------------ listen-first, bridge, recap and quiz

// schedule.py's section lengths
export const BRIDGE_MS = 700;
export const RECAP_MS = 3000;

const easeOut = (p) => 1 - Math.pow(1 - p, 3);
const easeIn = (p) => p * p * p;

/** When the dialogue's last line (its slow replay included) has been spoken; the hook's end without lines. */
export function dialogueEndMs(doc) {
  let end = doc.hookMs || 0;
  for (const line of doc.lines || []) end = Math.max(end, line.slowEndMs ?? line.endMs);
  return end;
}

/** When the tail starts: after the quiz, else the recap, else the dialogue. */
export function tailMs(doc) {
  if (doc.quiz) return doc.quiz.endMs;
  if (doc.recapMs != null) return doc.recapMs + RECAP_MS;
  return dialogueEndMs(doc);
}

/** Which part of the reel `ms` falls in. A gap before the recap or the quiz still belongs to the dialogue. */
export function section(ms, doc) {
  if (ms < (doc.hookMs || 0)) return "hook";
  if (ms >= tailMs(doc)) return "tail";
  if (doc.quiz && ms >= doc.quiz.startMs) return "quiz";
  if (doc.recapMs != null && ms >= doc.recapMs) return "recap";
  if (doc.bridgeMs != null && ms >= doc.bridgeMs && ms < doc.bridgeMs + BRIDGE_MS) return "bridge";
  if (doc.listen && doc.listen.length && doc.bridgeMs != null && ms < doc.bridgeMs) return "listen";
  return "dialogue";
}

/** A line moved to start at `atMs` (the listen pass, the quiz answer): its spans keep their offsets; no replay. */
export function shiftLine(line, atMs) {
  const d = atMs - line.startMs;
  return {
    ...line, startMs: atMs, endMs: line.endMs + d, meaningMs: line.meaningMs != null ? line.meaningMs + d : undefined,
    slowStartMs: null, slowEndMs: null,
    words: (line.words || []).map((w) => ({ text: w.text, startMs: w.startMs + d, endMs: w.endMs + d })),
  };
}

const BRIDGE_IN_MS = 220, BRIDGE_OUT_MS = 220;

/** The bridge card's x offset in card widths: 1 (off to the right) → 0, held, → -1 (off to the left). */
export function bridgeSlide(ms, bridgeMs) {
  const d = ms - bridgeMs;
  if (d <= 0) return 1;
  if (d >= BRIDGE_MS) return -1;
  if (d < BRIDGE_IN_MS) return 1 - easeOut(d / BRIDGE_IN_MS);
  const out = d - (BRIDGE_MS - BRIDGE_OUT_MS);
  return out > 0 ? -easeIn(out / BRIDGE_OUT_MS) : 0;
}

/** When the countdown ring and its first number pop in, after the quiz starts: with the first countdown pop
    (stages.COUNTDOWN_MS[0]), so the sound and the picture land together. */
export const RING_IN_MS = 0;

/** The quiz countdown: 3, 2, 1 for a second each (`ring` drains from 1 to 0 every second), then 0 at the reveal. */
export function countdown(ms, quiz) {
  if (ms >= quiz.revealMs) return { n: 0, ring: 0 };
  const d = Math.max(0, ms - quiz.startMs);
  const seconds = Math.round((quiz.revealMs - quiz.startMs) / 1000);
  return { n: Math.max(1, seconds - Math.floor(d / 1000)), ring: 1 - (d % 1000) / 1000 };
}

export const RECAP_CARD_PX = 70;
export const CARD_DELAY_MS = 250, CARD_STAGGER_MS = 120, CARD_IN_MS = 380, CARD_OUT_MS = 260;

/** Recap card `index`: after the title (250 ms), each rises 70 px into place 120 ms after the one before,
    overshooting a little; all of them leave together in the recap's last 260 ms. */
export function recapCard(ms, recapMs, index) {
  const p = clamp01((ms - recapMs - CARD_DELAY_MS - index * CARD_STAGGER_MS) / CARD_IN_MS);
  const out = fade(ms, recapMs + RECAP_MS - CARD_OUT_MS, CARD_OUT_MS);
  if (p <= 0) return { y: RECAP_CARD_PX, alpha: 0 };
  const rise = p >= 1 ? 0 : RECAP_CARD_PX * (1 - backOut(p));
  return { y: rise - 40 * out, alpha: clamp01(p * 2) * (1 - out) };
}

export const NOD_MS = 750, NOD_PX = 10;

/** The characters nod along to the recap: dips of 10 px every 750 ms, character 1 half a nod behind;
    only whole nods, so both are at rest when the recap ends. */
export function nod(ms, startMs, index) {
  const offset = index * (NOD_MS / 2);
  const d = ms - startMs - offset;
  if (d < 0 || d >= Math.floor((RECAP_MS - offset) / NOD_MS) * NOD_MS) return { y: 0, sy: 1 };
  const k = Math.pow(Math.sin((Math.PI * (d % NOD_MS)) / NOD_MS), 2);
  return { y: NOD_PX * k, sy: 1 - 0.04 * k };
}

export const LOOP_MS = 500, LOOP_HOLD_MS = 100;

/** The loop: 0 until the last 500 ms, then 1 by 100 ms before the end, when the frame matches the first one
    (the hook card, the characters not yet risen). */
export function loopBack(ms, durationMs) {
  return clamp01((ms - (durationMs - LOOP_MS)) / (LOOP_MS - LOOP_HOLD_MS));
}

export const WAVE_BARS = 7, WAVE_REST = 0.15;

/** The listen pass's sound wave: bar `i`'s height, 0..1. At rest 0.15; while a word is spoken the bars dance
    (taller in the middle), easing in and out over 60 ms at the word's edges. */
export function waveLevel(ms, words, i) {
  let on = 0;
  for (const w of words) {
    if (ms >= w.startMs && ms < w.endMs) {
      on = Math.min(1, (ms - w.startMs) / 60, (w.endMs - ms) / 60);
      break;
    }
  }
  const middle = 1 - Math.abs(i - (WAVE_BARS - 1) / 2) / WAVE_BARS;
  const dance = 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(ms / (55 + 9 * i) + i * 1.7));
  return WAVE_REST + (1 - WAVE_REST) * on * middle * dance;
}
