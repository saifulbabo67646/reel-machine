import { test } from "node:test";
import assert from "node:assert/strict";
import { activeLine, dropIn, hop, mouthOpen, blinking, litChars, fade, history, progress, bob, seedOf, fitFont, flag } from "../pure.mjs";

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
