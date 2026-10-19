import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ENTER_MS, EXIT_MS, GLIDE_MS, COUNTER_MS, HERO_MOVE_MS, TYPE_CPS, STAGE, STACK_GAP, MIN_SCALE, HERO_STACKING,
  backOut, clamp01, enterP, exitP, alive, stackLayout, counterValue, typed, terminalStarts, captionAt, wordPhase,
  heroAt, heroSpan, beatAt, transitionP, progress, blob, seedOf, CAPTION_HOLD_MS,
} from "../pure.mjs";

const block = (id, enterMs, exitMs = 99999, place = "stack") => ({ id, kind: "title", class: "block", enterMs, exitMs, place });
const sized = (entries) => new Map(entries.map(([id, w, h]) => [id, { w, h }]));
const bottom = (pos, size) => pos.y + size.h * pos.scale;

test("the constants are the spec's", () => {
  assert.deepEqual([ENTER_MS, EXIT_MS, GLIDE_MS, COUNTER_MS, HERO_MOVE_MS, TYPE_CPS, STACK_GAP, MIN_SCALE],
    [260, 200, 300, 900, 450, 28, 32, 0.6]);
  assert.deepEqual(STAGE, { top: 440, bottom: 1380, left: 64, right: 1016 });
  assert.deepEqual([...HERO_STACKING].sort(), ["center", "pulse", "rings", "screen", "shake", "sleep", "small-top"]);
});

test("easing and clamping", () => {
  assert.equal(clamp01(-1), 0);
  assert.equal(clamp01(2), 1);
  assert.equal(clamp01(0.3), 0.3);
  assert.ok(Math.abs(backOut(0)) < 1e-12);
  assert.ok(Math.abs(backOut(1) - 1) < 1e-12);
  assert.ok(backOut(0.7) > 1, "it overshoots");
});

test("a visual enters, lives and exits on its times", () => {
  const v = block("a", 1000, 2000);
  assert.equal(enterP(v, 900), 0);
  assert.equal(enterP(v, 1130), 0.5);
  assert.equal(enterP(v, 1260), 1);
  assert.equal(exitP(v, 1999), 0);
  assert.equal(exitP(v, 2100), 0.5);
  assert.equal(exitP(v, 2200), 1);
  assert.deepEqual([999, 1000, 2199, 2200].map((t) => alive(v, t)), [false, true, true, false]);
});

test("four 400-px blocks scale to the minimum and the last one stays inside the stage", () => {
  const blocks = ["a", "b", "c", "d"].map((id, i) => block(id, i * 100));
  const sizes = sized(blocks.map((b) => [b.id, 800, 400]));
  const layout = stackLayout(blocks, sizes, 5000);
  for (const b of blocks) assert.equal(layout.get(b.id).scale, 0.6);
  assert.ok(bottom(layout.get("d"), sizes.get("d")) <= STAGE.bottom, `bottom ${bottom(layout.get("d"), sizes.get("d"))}`);
  const ys = blocks.map((b) => layout.get(b.id).y);
  assert.deepEqual([...ys].sort((p, q) => p - q), ys, "they stack in enter order");
});

test("one block alone is centred in the stage", () => {
  const layout = stackLayout([block("a", 0)], sized([["a", 600, 300]]), 1000);
  assert.deepEqual(layout.get("a"), { x: 540 - 300, y: 440 + (940 - 300) / 2, scale: 1 });
});

test("a stack that fits is centred as a whole, 32 px apart, and nothing not yet entered is laid out", () => {
  const blocks = [block("a", 0), block("b", 2000)];
  const sizes = sized([["a", 400, 200], ["b", 400, 300]]);
  const early = stackLayout(blocks, sizes, 1000);
  assert.equal(early.has("b"), false);
  const late = stackLayout(blocks, sizes, 5000);
  const top = 440 + (940 - 532) / 2;
  assert.equal(late.get("a").y, top);
  assert.equal(late.get("b").y, top + 232);
  assert.equal(stackLayout(blocks, sizes, -1).size, 0);
});

test("a left/right pair shares one row, side by side", () => {
  const blocks = [block("l", 0, 99999, "left"), block("r", 0, 99999, "right")];
  const layout = stackLayout(blocks, sized([["l", 400, 300], ["r", 400, 200]]), 1000);
  assert.ok(layout.get("l").x < layout.get("r").x);
  assert.equal(layout.get("l").x, 540 - (832 / 2));
  assert.equal(layout.get("r").x, 540 - 832 / 2 + 432);
  assert.equal(layout.get("l").y, 440 + (940 - 300) / 2, "the row is as tall as its taller block");
});

test("a row wider than the stage shrinks the stack until it fits between the margins", () => {
  const blocks = [block("l", 0, 99999, "left"), block("r", 0, 99999, "right")];
  const sizes = sized([["l", 600, 300], ["r", 600, 300]]);
  const layout = stackLayout(blocks, sizes, 1000);
  const scale = (STAGE.right - STAGE.left) / 1232;
  assert.ok(Math.abs(layout.get("l").scale - scale) < 1e-12);
  assert.ok(Math.abs(layout.get("l").x - STAGE.left) < 1e-9);
  assert.ok(Math.abs(layout.get("r").x + 600 * scale - STAGE.right) < 1e-9);
});

test("the hero's stack span follows its runs of stacking states", () => {
  const states = [{ atMs: 0, state: "center" }, { atMs: 1000, state: "pulse" }, { atMs: 2000, state: "corner" },
    { atMs: 3000, state: "small-top" }, { atMs: 4000, state: "hidden" }];
  assert.deepEqual(heroSpan(states, 500), { id: "hero", exitMs: 2000 }, "the first run has no enter time");
  assert.deepEqual(heroSpan(states, 1500), { id: "hero", exitMs: 2000 });
  assert.deepEqual(heroSpan(states, 2500), { id: "hero", exitMs: 2000 }, "a run that ended is kept, for the glide");
  assert.deepEqual(heroSpan(states, 3500), { id: "hero", enterMs: 3000, exitMs: 4000 });
  assert.deepEqual(heroSpan(states, 9000), { id: "hero", enterMs: 3000, exitMs: 4000 });
  assert.equal(heroSpan([{ atMs: 0, state: "corner" }, { atMs: 500, state: "center" }], 200), null);
  assert.deepEqual(heroSpan([{ atMs: 0, state: "corner" }, { atMs: 500, state: "center" }], 800), { id: "hero", enterMs: 500 });
  assert.equal(heroSpan([], 0), null);
});

test("the others glide when the hero joins the stack and when it leaves", () => {
  const states = [{ atMs: 0, state: "corner" }, { atMs: 2000, state: "small-top" }, { atMs: 5000, state: "hidden" }];
  const sizes = sized([["hero", 160, 160], ["a", 400, 200]]);
  const at = (t) => stackLayout([block("a", 0), heroSpan(states, t)].filter(Boolean), sizes, t);
  const alone = at(1999).get("a").y;
  assert.equal(at(1999).has("hero"), false);
  const joined = at(2000 + GLIDE_MS).get("a").y;
  const mid = at(2000 + GLIDE_MS / 2).get("a").y;
  assert.ok(joined > alone, "a moves down under the hero");
  assert.ok(mid > alone && mid < joined, `mid ${mid}`);
  assert.equal(at(2000).get("a").y, alone, "no jump when the hero joins");
  assert.ok(at(2000 + GLIDE_MS).get("hero").y < joined, "the hero is on top");
  const left = 5000 + EXIT_MS;
  assert.equal(at(left - 1).get("a").y, joined);
  assert.equal(at(left).get("a").y, joined, "no jump when the hero has left");
  assert.equal(at(left).has("hero"), false);
  assert.equal(at(left + GLIDE_MS).get("a").y, alone, "a glides back to the centre");
});

test("when a block exits the others glide to their new slots", () => {
  const blocks = [block("a", 0, 1000), block("b", 100), block("c", 200)];
  const sizes = sized(blocks.map((b) => [b.id, 400, 200]));
  const change = 1000 + EXIT_MS;
  const before = stackLayout(blocks, sizes, change - 1).get("b").y;
  const after = stackLayout(blocks, sizes, change + GLIDE_MS).get("b").y;
  const mid = stackLayout(blocks, sizes, change + GLIDE_MS / 2).get("b").y;
  assert.ok(after < before, "b moves up into a's slot");
  assert.ok(mid > after && mid < before, `mid ${mid} between ${after} and ${before}`);
  assert.equal(stackLayout(blocks, sizes, change).get("b").y, before, "the glide starts where it was");
  assert.equal(stackLayout(blocks, sizes, change).has("a"), false);
});

test("a new block makes room: the others glide, the newcomer lands in its slot", () => {
  const blocks = [block("a", 0), block("b", 1000)];
  const sizes = sized([["a", 400, 200], ["b", 400, 200]]);
  const alone = stackLayout(blocks, sizes, 999).get("a").y;
  const final = stackLayout(blocks, sizes, 1000 + GLIDE_MS);
  const mid = stackLayout(blocks, sizes, 1000 + GLIDE_MS / 2);
  assert.ok(mid.get("a").y < alone && mid.get("a").y > final.get("a").y);
  assert.deepEqual(mid.get("b"), final.get("b"));
});

test("the others are mostly out of the way once a newcomer is opaque", () => {
  // a newcomer is fully opaque half way through its enter (main.mjs: opacity = 2 × enter progress)
  const blocks = [{ id: "hero" }, block("b", 1000)];
  const sizes = sized([["hero", 160, 160], ["b", 400, 300]]);
  const alone = stackLayout(blocks, sizes, 999).get("hero").y;
  const final = stackLayout(blocks, sizes, 1000 + GLIDE_MS).get("hero").y;
  const opaque = stackLayout(blocks, sizes, 1000 + ENTER_MS / 2).get("hero").y;
  assert.ok((alone - opaque) / (alone - final) >= 0.75, `only ${((alone - opaque) / (alone - final)).toFixed(2)} of the way`);
});

test("the hero stacks first, with no times of its own", () => {
  const blocks = [block("a", 500), { id: "hero" }];
  const layout = stackLayout(blocks, sized([["hero", 300, 300], ["a", 400, 200]]), 1000);
  assert.ok(layout.get("hero").y < layout.get("a").y);
});

test("a counter rolls from its start to its end over 900 ms", () => {
  const v = { from: 0, to: 176, decimals: 0, enterMs: 0 };
  assert.equal(counterValue(v, 0), "0");
  assert.equal(counterValue(v, 900), "176");
  assert.equal(counterValue(v, 5000), "176");
  const mid = Number(counterValue(v, 450));
  assert.ok(mid > 88 && mid < 176, "ease-out: past half way at half time");
  assert.equal(counterValue({ from: 0, to: 19.9, decimals: 1, suffix: "K", enterMs: 0 }, 900), "19.9K");
  assert.equal(counterValue({ from: 0, to: 9, prefix: "$", enterMs: 100 }, 1000), "$9");
});

test("only counters reaching ten thousand get thin grouping", () => {
  assert.equal(counterValue({ from: 0, to: 4096, enterMs: 0 }, 900), "4096");
  assert.equal(counterValue({ from: 0, to: 19900, enterMs: 0 }, 900), "19 900");
  assert.equal(counterValue({ from: 0, to: 1234567.5, decimals: 1, enterMs: 0 }, 900), "1 234 567.5");
  assert.equal(counterValue({ from: 0, to: 19900, enterMs: 0 }, 0), "0");
});

test("text types at 28 characters a second", () => {
  assert.equal(typed("abc", 0, (1000 / 28) * 2), "ab");
  assert.equal(typed("abc", 0, 0), "");
  assert.equal(typed("abc", 100, 50), "");
  assert.equal(typed("abc", 0, 5000), "abc");
  assert.equal(typed("😀ab", 0, 1000 / 28), "😀", "characters, not UTF-16 halves");
});

test("terminal lines appear as the commands before them finish typing", () => {
  const cps = 1000 / TYPE_CPS;
  const v = { enterMs: 1000, lines: [{ cmd: "ls" }, { out: "a b" }, { out: "c" }, { cmd: "cat a" }, { out: "hi" }] };
  const first = 1000 + 2 * cps;
  const second = first + 250;
  assert.deepEqual(terminalStarts(v), [1000, first, first, second, second + 5 * cps]);
  assert.deepEqual(terminalStarts({ enterMs: 400, lines: [{ out: "booting" }, { cmd: "x" }] }), [400, 400]);
  const done = terminalStarts(v)[1];
  assert.equal(typed("ls", 1000, done), "ls", "an output shows once its command is fully typed");
});

test("captions are picked by time", () => {
  const captions = [{ startMs: 0, endMs: 900 }, { startMs: 1000, endMs: 1800 }, { startMs: 3000, endMs: 3500 }];
  assert.deepEqual([-10, 0, 899, 950, 1000, 2000, 2100, 3200, 3500].map((t) => captionAt(captions, t)),
    [-1, 0, 0, 0, 1, 1, 1, 2, -1]);
  assert.equal(captionAt([], 100), -1);
});

test("a caption is held at most 300 ms past its end, so none hangs over a pause", () => {
  const captions = [{ startMs: 0, endMs: 1000 }, { startMs: 3000, endMs: 3500 }];
  assert.equal(CAPTION_HOLD_MS, 300);
  assert.equal(captionAt(captions, 1300), 0);
  assert.equal(captionAt(captions, 1301), -1);
  assert.equal(captionAt(captions, 2999), -1);
  assert.equal(captionAt(captions, 3000), 1);
});

test("a word is future, now, then past", () => {
  const w = { startMs: 100, endMs: 300 };
  assert.deepEqual([99, 100, 299, 300].map((t) => wordPhase(w, t)), ["future", "now", "now", "past"]);
});

test("the hero moves between states over 450 ms", () => {
  const states = [{ atMs: 0, state: "center", text: null }, { atMs: 1000, state: "corner", text: "hi" }];
  assert.deepEqual(heroAt(states, 500), { state: "center", from: null, p: 1, text: null });
  assert.deepEqual(heroAt(states, 1000), { state: "corner", from: "center", p: 0, text: "hi" });
  assert.equal(heroAt(states, 1225).p, 0.5);
  assert.equal(heroAt(states, 1000 + HERO_MOVE_MS).p, 1);
  assert.equal(heroAt([], 100), null);
});

test("beats change at their transition's start, and transitions run across the gap into the beat", () => {
  const beats = [{ startMs: 0, transition: "cut" }, { startMs: 2000, transition: "slide" }, { startMs: 5000, transition: "cut" }];
  assert.deepEqual([0, 1749, 1750, 4999, 5000, 9000].map((t) => beatAt(beats, t)), [0, 0, 1, 1, 2, 2]);
  assert.equal(transitionP(beats, 1, 1750), 0);
  assert.equal(transitionP(beats, 1, 1875), 0.5);
  assert.equal(transitionP(beats, 1, 2000), 1);
  assert.equal(transitionP(beats, 2, 4999), 1, "a cut is always done");
  assert.equal(transitionP(beats, 0, 0), 1);
});

test("progress is clamped to 0..1", () => {
  assert.equal(progress(-5, 1000), 0);
  assert.equal(progress(250, 1000), 0.25);
  assert.equal(progress(2000, 1000), 1);
  assert.equal(progress(10, 0), 0);
});

test("background blobs drift deterministically and slowly", () => {
  assert.deepEqual(blob(1, 4321, 99), blob(1, 4321, 99));
  assert.notDeepEqual(blob(0, 4321, 99), blob(1, 4321, 99));
  assert.notDeepEqual(blob(1, 4321, 99), blob(1, 4321, 100));
  const a = blob(0, 0, 7), b = blob(0, 33, 7);
  assert.ok(Math.hypot(a.x - b.x, a.y - b.y) < 8, "one frame moves a blob a few px at most");
  assert.ok(a.r > 0);
});

test("seeds are stable per string", () => {
  assert.equal(seedOf("hook"), seedOf("hook"));
  assert.notEqual(seedOf("hook"), seedOf("cta"));
  assert.equal(seedOf(""), 0x811c9dc5);
});
