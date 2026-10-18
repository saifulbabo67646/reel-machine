import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { activeLine, dropIn, hop, mouthOpen, blinking, litChars, fade, history, progress, bob, seedOf, fitFont, flag, partId,
  keyVisible, emotionParts, emojiPop, slowActive, slowWords, rowFit, SLOW_FLAP_MS,
  section, countdown, recapCard, RECAP_CARD_PX, shiftLine, bridgeSlide, nod, loopBack, waveLevel, WAVE_BARS, WAVE_REST } from "../pure.mjs";

test("a line is active from its start until the next starts", () => {
  const lines = [{ startMs: 1500 }, { startMs: 2850 }];
  assert.deepEqual([1000, 1500, 2849, 2850, 9000].map((t) => activeLine(lines, t)), [-1, 0, 0, 1, 1]);
  assert.equal(activeLine([], 5000), -1);
});

test("one long span still lights characters one by one", () => {
  const words = [{ text: "てつだいましょうか。", startMs: 0, endMs: 1000 }];
  assert.equal(litChars(0, 10, words), 0);
  assert.equal(litChars(500, 10, words), 5);
  assert.equal(litChars(1000, 10, words), 10);
});

test("spans light in proportion to their length, and none before the first", () => {
  const words = [{ text: "ab", startMs: 100, endMs: 200 }, { text: "cdef", startMs: 300, endMs: 400 }];
  assert.equal(litChars(50, 6, words), 0);
  assert.equal(litChars(200, 6, words), 2);
  assert.equal(litChars(250, 6, words), 2);
  assert.equal(litChars(350, 6, words), 4);
  assert.equal(litChars(5000, 6, words), 6);
  assert.equal(litChars(5000, 6, []), 0);
});

test("the mouth flaps only while a word is spoken", () => {
  const words = [{ text: "a", startMs: 100, endMs: 400 }];
  assert.equal(mouthOpen(50, words), false);
  assert.equal(mouthOpen(100, words), true);
  assert.equal(mouthOpen(190, words), false);
  assert.equal(mouthOpen(280, words), true);
  assert.equal(mouthOpen(400, words), false);
});

test("blinks are deterministic, brief and spaced", () => {
  const frames = (seed) => Array.from({ length: 2000 }, (_, i) => blinking(i * 10, seed));
  assert.deepEqual(frames(7), frames(7));
  assert.notDeepEqual(frames(7), frames(8));
  const runs = [];
  let start = null;
  frames(7).forEach((closed, i) => {
    if (closed && start === null) start = i * 10;
    if (!closed && start !== null) { runs.push([start, i * 10]); start = null; }
  });
  assert.ok(runs.length >= 3, `only ${runs.length} blinks in 20 s`);
  for (const [a, b] of runs) assert.ok(b - a <= 120, `a blink of ${b - a} ms`);
  for (let i = 1; i < runs.length; i++) assert.ok(runs[i][0] - runs[i - 1][1] >= 2000);
});

test("a hop rises to its peak and returns to rest", () => {
  assert.deepEqual(hop(280, 0), { y: 0, sx: 1, sy: 1 });
  assert.deepEqual(hop(-10, 0), { y: 0, sx: 1, sy: 1 });
  const ys = Array.from({ length: 28 }, (_, i) => hop(i * 10, 0).y);
  assert.ok(Math.abs(Math.min(...ys) + 36) < 1.5, `peak ${Math.min(...ys)}`);
  assert.ok(hop(20, 0).sy < 1, "it squashes before it leaves the ground");
});

test("characters drop in after their delay and settle", () => {
  assert.equal(dropIn(150, 150).y, 500);
  assert.ok(dropIn(450, 150).y < 0, "the back-out overshoots");
  assert.deepEqual(dropIn(600, 150), { y: 0, scale: 1 });
  assert.deepEqual(dropIn(5000, 300), { y: 0, scale: 1 });
});

test("history keeps the two lines before the active one", () => {
  assert.deepEqual(history([{}, {}, {}, {}], 3), [1, 2]);
  assert.deepEqual(history([{}, {}], 1), [0]);
  assert.deepEqual(history([{}, {}], 0), []);
  assert.deepEqual(history([{}, {}], -1), []);
});

test("fades and progress are clamped to 0..1", () => {
  assert.equal(fade(0, 100), 0);
  assert.equal(fade(225, 100), 0.5);
  assert.equal(fade(400, 100), 1);
  assert.equal(progress(-5, 1000), 0);
  assert.equal(progress(250, 1000), 0.25);
  assert.equal(progress(2000, 1000), 1);
});

test("the idle bob stays within six pixels and the two characters move in opposite phase", () => {
  for (let ms = 0; ms < 3200; ms += 50) {
    assert.ok(Math.abs(bob(ms, 0)) <= 6 + 1e-9);
    assert.ok(Math.abs(bob(ms, 0) + bob(ms, 1)) < 1e-9);
  }
});

test("seeds are stable per id", () => {
  assert.equal(seedOf("kid"), seedOf("kid"));
  assert.notEqual(seedOf("kid"), seedOf("mom"));
});

test("short lines are big, long lines shrink or wrap but stay legible", () => {
  assert.equal(fitFont("おかあさん。"), 100);
  assert.ok(fitFont("せんたくをしてくれる？") >= 72 && fitFont("せんたくをしてくれる？") < 100);
  for (const text of ["あ".repeat(22), "Could you please do the laundry for me today?"]) {
    assert.ok(fitFont(text) >= 60 && fitFont(text) <= 100, `${text}: ${fitFont(text)}`);
  }
});

test("a language gets its flag", () => {
  assert.equal(flag("ja"), "🇯🇵");
  assert.equal(flag("xx"), "");
});

test("part ids are the scoped ids compose writes, and never a page id", () => {
  assert.equal(partId("kid", "mouth-open"), "c_kid.mouth-open");
  assert.equal(partId("a-b", "x"), "c_a-b.x");
  for (const id of ["root", "reel-data", "char-kid"]) assert.ok(!partId("kid", "x").startsWith(id));
});

const LINE = { startMs: 1000, endMs: 2000, words: [{ text: "abcdefghij", startMs: 1000, endMs: 2000 }] };

test("a key chip is hidden until its first character is lit, then pops in over 220 ms", () => {
  const key = { start: 4, end: 6 };
  // character 4 lights once 5 of 10 are lit: at 1500 ms
  assert.equal(keyVisible(500, LINE, key, 10), 0);
  assert.equal(keyVisible(1499, LINE, key, 10), 0);
  assert.equal(keyVisible(1500, LINE, key, 10), 0);
  const mid = keyVisible(1610, LINE, key, 10);
  assert.ok(mid > 0 && mid < 1, `mid-pop ${mid}`);
  assert.equal(keyVisible(1720, LINE, key, 10), 1);
  assert.equal(keyVisible(9000, LINE, key, 10), 1, "it stays (the bubble's exit hides it)");
  assert.equal(keyVisible(1000, LINE, { start: 0, end: 2 }, 10) >= 0, true);
  assert.equal(keyVisible(1320, LINE, { start: 0, end: 2 }, 10), 1);
});

test("a line without word timings lights its keys across the whole line", () => {
  const line = { startMs: 0, endMs: 1000, words: [] };
  assert.equal(keyVisible(400, line, { start: 5, end: 6 }, 10), 0);
  assert.equal(keyVisible(600 + 220, line, { start: 5, end: 6 }, 10), 1);
});

test("emotions pick their parts, and talking always wins the mouth", () => {
  assert.deepEqual(emotionParts("neutral", false), { eyes: "open", mouth: "closed", extras: [], emoji: "", tiltDeg: 0 });
  assert.deepEqual(emotionParts("happy", false), { eyes: "happy", mouth: "smile", extras: [], emoji: "❤️", tiltDeg: 0 });
  assert.equal(emotionParts("happy", true).mouth, "open");
  assert.equal(emotionParts("neutral", true).mouth, "open");
  assert.deepEqual(emotionParts("surprised", false), { eyes: "surprised", mouth: "closed", extras: [], emoji: "❗", tiltDeg: 0 });
  assert.deepEqual(emotionParts("sad", false), { eyes: "open", mouth: "closed", extras: ["sweat"], emoji: "💦", tiltDeg: 0 });
  assert.equal(emotionParts("thinking", false).tiltDeg, 8);
  assert.equal(emotionParts("thinking", false).emoji, "❓");
  assert.deepEqual(emotionParts("bogus", false), emotionParts("neutral", false));
});

test("an emotion emoji rises and fades over 600 ms from the line start", () => {
  assert.equal(emojiPop(999, 1000).alpha, 0);
  assert.equal(emojiPop(1600, 1000).alpha, 0);
  const a = emojiPop(1150, 1000), b = emojiPop(1450, 1000);
  assert.ok(a.alpha > 0 && b.alpha > 0);
  assert.ok(b.y < a.y && a.y < 0, "it rises");
  assert.ok(emojiPop(1580, 1000).alpha < b.alpha, "it fades at the end");
  assert.deepEqual(emojiPop(1000, 1000), emojiPop(1000, 1000));
});

test("a slow replay is active only inside its window", () => {
  const line = { startMs: 1000, endMs: 2000, slowStartMs: 2350, slowEndMs: 3683 };
  assert.deepEqual([2349, 2350, 3000, 3682, 3683].map((ms) => slowActive(ms, line)), [false, true, true, true, false]);
  assert.equal(slowActive(3000, { startMs: 0, endMs: 1, slowStartMs: null, slowEndMs: null }), false);
});

test("the slow replay's words keep their order at 1 / 0.75 the pace", () => {
  const line = { startMs: 1000, endMs: 1600, slowStartMs: 2000, slowEndMs: 2800,
    words: [{ text: "ab", startMs: 1000, endMs: 1300 }, { text: "cd", startMs: 1300, endMs: 1600 }] };
  assert.deepEqual(slowWords(line), [{ text: "ab", startMs: 2000, endMs: 2400 }, { text: "cd", startMs: 2400, endMs: 2800 }]);
  assert.deepEqual(slowWords({ ...line, words: [] }), [{ text: "", startMs: 2000, endMs: 2800 }]);
  assert.equal(SLOW_FLAP_MS, 120);
  assert.equal(mouthOpen(2000 + 100, slowWords(line), SLOW_FLAP_MS), true);
  assert.equal(mouthOpen(2000 + 130, slowWords(line), SLOW_FLAP_MS), false);
});

test("a history row shrinks both texts to fit, but never below half size", () => {
  assert.deepEqual(rowFit(3, 4, 862, { textPx: 70, meaningPx: 39 }), { textPx: 70, meaningPx: 39 });
  const fit = rowFit(10, 12, 862, { textPx: 70, meaningPx: 39 });
  assert.ok(fit.textPx < 70 && fit.textPx >= 35);
  assert.ok(10 * fit.textPx + 12 * fit.meaningPx <= 862, `${JSON.stringify(fit)} overflows`);
  assert.ok(Math.abs(fit.textPx / 70 - fit.meaningPx / 39) < 0.03, "both shrink together");
  assert.deepEqual(rowFit(100, 100, 862, { textPx: 70, meaningPx: 39 }), { textPx: 35, meaningPx: 20 });
  assert.deepEqual(rowFit(3, 4, 862), { textPx: 70, meaningPx: 39 });
});

// ------------------------------------------------------------ listen-first, bridge, recap and quiz

const SECTIONS = JSON.parse(readFileSync(new URL("./sections.json", import.meta.url), "utf8"));

test("each section starts and ends where the schedule put it", () => {
  const at = (ms) => section(ms, SECTIONS);
  const expected = [
    [0, "hook"], [1499, "hook"], [1500, "listen"], [2600, "listen"], [4199, "listen"],
    [4200, "bridge"], [4899, "bridge"], [4900, "dialogue"], [6000, "dialogue"], [7599, "dialogue"],
    [7600, "recap"], [10599, "recap"], [10600, "quiz"], [13600, "quiz"], [15399, "quiz"],
    [15400, "tail"], [16299, "tail"], [99999, "tail"],
  ];
  assert.deepEqual(expected.map(([ms]) => at(ms)), expected.map(([, name]) => name));
});

test("a plain reel has only the hook, the dialogue and the tail", () => {
  const doc = { hookMs: 1500, lines: [{ startMs: 1500, endMs: 2500 }, { startMs: 2850, endMs: 3850, slowStartMs: 4200, slowEndMs: 5533 }],
    listen: [], bridgeMs: null, recapMs: null, quiz: null };
  assert.deepEqual([1499, 1500, 3900, 5532, 5533].map((ms) => section(ms, doc)), ["hook", "dialogue", "dialogue", "dialogue", "tail"]);
  // a quiz without a recap: the gap before it is still the dialogue
  const quiz = { ...doc, quiz: { line: 0, startMs: 5883, revealMs: 8883, endMs: 10683 } };
  assert.deepEqual([5600, 5883, 10683].map((ms) => section(ms, quiz)), ["dialogue", "quiz", "tail"]);
});

test("the countdown shows 3, 2, 1 a second each, its ring draining every second, then 0", () => {
  const quiz = SECTIONS.quiz;
  assert.deepEqual(countdown(10600, quiz), { n: 3, ring: 1 });
  assert.equal(countdown(11099, quiz).n, 3);
  assert.ok(Math.abs(countdown(11100, quiz).ring - 0.5) < 1e-9);
  assert.equal(countdown(11600, quiz).n, 2);
  assert.equal(countdown(12599, quiz).n, 2);
  assert.equal(countdown(12600, quiz).n, 1);
  assert.deepEqual(countdown(13600, quiz), { n: 0, ring: 0 });
  assert.deepEqual(countdown(20000, quiz), { n: 0, ring: 0 });
  assert.deepEqual(countdown(9000, quiz), { n: 3, ring: 1 });
});

test("recap cards rise in one after another, 120 ms apart, and all leave together", () => {
  const recapMs = 7600;
  assert.deepEqual(recapCard(7000, recapMs, 0), { y: RECAP_CARD_PX, alpha: 0 });
  const first = (index) => {
    for (let ms = recapMs; ms < recapMs + 3000; ms++) if (recapCard(ms, recapMs, index).alpha > 0) return ms;
    return null;
  };
  assert.equal(first(1) - first(0), 120);
  assert.equal(first(5) - first(0), 600);
  const settled = recapCard(recapMs + 1800, recapMs, 5);
  assert.deepEqual(settled, { y: 0, alpha: 1 });
  assert.ok(recapCard(recapMs + 2950, recapMs, 0).alpha < 1, "they fade at the end");
  assert.equal(recapCard(recapMs + 3000, recapMs, 0).alpha, 0);
});

test("a line moved to another start keeps its spans' offsets", () => {
  const line = { startMs: 4900, endMs: 5900, meaningMs: 5500, slowStartMs: 6250, slowEndMs: 7583,
    words: [{ text: "ab", startMs: 4900, endMs: 5300 }, { text: "cd", startMs: 5400, endMs: 5900 }] };
  const moved = shiftLine(line, 1500);
  assert.deepEqual(moved.words, [{ text: "ab", startMs: 1500, endMs: 1900 }, { text: "cd", startMs: 2000, endMs: 2500 }]);
  assert.deepEqual([moved.startMs, moved.endMs, moved.meaningMs, moved.slowStartMs, moved.slowEndMs], [1500, 2500, 2100, null, null]);
  assert.equal(line.startMs, 4900, "the original is untouched");
  assert.equal(mouthOpen(1550, moved.words), true);
});

test("the bridge card slides in from the right and out to the left", () => {
  assert.equal(bridgeSlide(4100, 4200), 1);
  assert.equal(bridgeSlide(4550, 4200), 0);
  assert.ok(bridgeSlide(4800, 4200) < 0 && bridgeSlide(4800, 4200) > -1);
  assert.equal(bridgeSlide(4900, 4200), -1);
});

test("the characters nod through the recap and are back at rest when it ends", () => {
  for (const index of [0, 1]) {
    assert.deepEqual(nod(7599, 7600, index), { y: 0, sy: 1 });
    assert.deepEqual(nod(10600, 7600, index), { y: 0, sy: 1 });
  }
  const ys = Array.from({ length: 300 }, (_, i) => nod(7600 + i * 10, 7600, 0).y);
  assert.ok(Math.max(...ys) > 6, "it dips");
});

test("the loop: the last frames return to the first", () => {
  assert.equal(loopBack(15000, 16300), 0);
  assert.equal(loopBack(16300 - 100, 16300), 1);
  const mid = loopBack(16000, 16300);
  assert.ok(mid > 0 && mid < 1);
});

test("the listen wave rests in silence and dances while a word is spoken", () => {
  const words = [{ text: "a", startMs: 1000, endMs: 2000 }];
  for (let i = 0; i < WAVE_BARS; i++) {
    assert.equal(waveLevel(500, words, i), WAVE_REST);
    assert.equal(waveLevel(2000, words, i), WAVE_REST);
  }
  const levels = Array.from({ length: 100 }, (_, k) => waveLevel(1060 + k * 9, words, 3));
  assert.ok(Math.max(...levels) > 0.7 && Math.min(...levels) > WAVE_REST, `${Math.min(...levels)}..${Math.max(...levels)}`);
  assert.ok(levels.every((v) => v <= 1));
  assert.equal(waveLevel(1500, words, 2), waveLevel(1500, words, 2));
});
