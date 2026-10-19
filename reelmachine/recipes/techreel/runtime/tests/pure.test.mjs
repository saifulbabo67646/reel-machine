import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ENTER_MS, EXIT_MS, GLIDE_MS, COUNTER_MS, HERO_MOVE_MS, TYPE_CPS, STAGE, STACK_GAP, MIN_SCALE, HERO_STACKING,
  backOut, clamp01, enterP, exitP, alive, stackLayout, counterValue, typed, terminalStarts, captionAt, wordPhase,
  heroAt, heroSpan, beatAt, transitionP, progress, blob, seedOf, CAPTION_HOLD_MS, staggerP, winner, numberParts,
  gridOf, litSet, nodeLayout, nodeLinks, emptySpot, effectiveHeroState, heroTimeline, splitPoints, blinkAt,
  PILL_EASE_MS, pillAt, isUnspaced, accentUnits, accentPieces,
} from "../pure.mjs";

const block = (id, enterMs, exitMs = 99999, place = "stack") => ({ id, kind: "title", class: "block", enterMs, exitMs, place });
const sized = (entries) => new Map(entries.map(([id, w, h]) => [id, { w, h }]));
const bottom = (pos, size) => pos.y + size.h * pos.scale;

test("the constants are the spec's", () => {
  assert.deepEqual([ENTER_MS, EXIT_MS, GLIDE_MS, COUNTER_MS, HERO_MOVE_MS, TYPE_CPS, STACK_GAP, MIN_SCALE],
    [260, 200, 300, 900, 450, 28, 32, 0.6]);
  assert.deepEqual(STAGE, { top: 440, bottom: 1380, left: 64, right: 1016 });
  assert.deepEqual([...HERO_STACKING].sort(),
    ["center", "locked", "pulse", "rings", "screen", "shake", "sleep", "small-top", "split"]);
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

test("staggered items: item i starts gapMs after item i-1 and takes durMs", () => {
  assert.equal(staggerP(0, 1000, 999, 200, 250), 0);
  assert.equal(staggerP(0, 1000, 1125, 200, 250), 0.5);
  assert.equal(staggerP(0, 1000, 1250, 200, 250), 1);
  assert.equal(staggerP(2, 1000, 1400, 200, 250), 0);
  assert.equal(staggerP(2, 1000, 1525, 200, 250), 0.5);
  assert.equal(staggerP(2, 1000, 99999, 200, 250), 1);
  assert.equal(staggerP(1, 0, 300, 100, 0), 1, "a zero duration is a step");
  assert.equal(staggerP(1, 0, 99, 100, 0), 0);
});

test("the winner is the higher number, else the right", () => {
  assert.equal(winner("1", "0"), "left");
  assert.equal(winner("0", "1"), "right");
  assert.equal(winner("19.9K", "0"), "left");
  assert.equal(winner("₹2,000", "₹500"), "left");
  assert.equal(winner("-3", "-1"), "right");
  assert.equal(winner("5", "5"), "right", "a tie goes right");
  assert.equal(winner("lots", "1"), "right", "a side that is no number never wins");
  assert.equal(winner("9", "many"), "right");
  assert.equal(winner("", ""), "right");
});

test("K, M and B suffixes scale a score before it is compared", () => {
  assert.equal(winner("1.2M", "900K"), "left");
  assert.equal(winner("900k", "1.2m"), "right", "lowercase scales too");
  assert.equal(winner("2B", "1999M"), "left");
  assert.equal(winner("$3b", "$40m"), "left");
  assert.equal(winner("5 bytes", "4"), "left", "a word starting with b is not a suffix");
});

test("byte units scale a score too", () => {
  assert.equal(winner("2MB", "900KB"), "left");
  assert.equal(winner("1.5GB", "800MB"), "left");
  assert.equal(winner("800 MB", "1.5 GB"), "right");
  assert.equal(winner("1TB", "999GB"), "left");
  assert.equal(winner("2G", "900M"), "left", "a bare G is giga");
  assert.equal(winner("1T", "999G"), "left", "a bare T is tera");
  assert.equal(winner("24gb", "12GB"), "left", "any case");
  assert.equal(winner("3 GBs", "900MB"), "left", "a byte unit may be plural");
  assert.equal(winner("3 GBps", "2"), "left", "a word after the unit is no unit: 3 against 2");
  assert.equal(winner("5 bs", "6"), "right", "only a byte unit takes the s");
  assert.equal(winner("12x", "9"), "left", "a letter after the number leaves the number whole");
});

test("a number with a prefix and suffix splits for counterValue, which then gives it back", () => {
  assert.deepEqual(numberParts("176B"), { prefix: "", to: 176, decimals: 0, suffix: "B" });
  assert.deepEqual(numberParts("$19.99/mo"), { prefix: "$", to: 19.99, decimals: 2, suffix: "/mo" });
  assert.deepEqual(numberParts("-4%"), { prefix: "", to: -4, decimals: 0, suffix: "%" });
  for (const text of ["176B", "$19.99/mo", "-4%", "= 172B", "6B / 125B"]) {
    assert.equal(counterValue({ ...numberParts(text), enterMs: 0 }, Infinity), text);
  }
  // text counterValue cannot give back (grouping it would rewrite, no digits, too many decimals) is not a number
  for (const text of ["2,000", "12 000", "free", "", "1.234", "19900"]) assert.equal(numberParts(text), null, text);
});

// ------------------------------------------------------------ social and analogy helpers

test("a people grid is a wide strip, about five columns per row, and fits its width", () => {
  assert.deepEqual(gridOf(125, 888, 34), { cols: 25, rows: 5, size: 34 }, "the reference's 25 × 5 office");
  const big = gridOf(200, 888, 34);
  assert.equal(big.cols, 32);
  assert.equal(big.rows, 7);
  assert.ok(big.cols * big.size <= 888, "200 people fit in 888 px");
  assert.deepEqual(gridOf(1, 888, 34), { cols: 1, rows: 1, size: 34 }, "never wider than the people in it");
  for (let n = 1; n <= 200; n++) {
    const g = gridOf(n, 888, 34);
    assert.ok(g.cols * g.rows >= n && g.cols * g.size <= 888 && (g.rows - 1) * g.cols < n, `total ${n}`);
  }
});

test("the lit set is seeded, of its size and spread over the grid", () => {
  const a = litSet(125, 6, 42);
  assert.equal(a.size, 6);
  assert.deepEqual([...a], [...litSet(125, 6, 42)], "the same seed, the same set");
  assert.notDeepEqual([...a].sort(), [...litSet(125, 6, 43)].sort(), "another seed, another set");
  for (const i of a) assert.ok(Number.isInteger(i) && i >= 0 && i < 125);
  const sorted = [...a].sort((x, y) => x - y);
  // one in each sixth of the grid: never bunched up
  sorted.forEach((i, k) => assert.ok(i >= Math.floor((k * 125) / 6) && i < Math.floor(((k + 1) * 125) / 6)));
  assert.equal(litSet(10, 0, 1).size, 0);
  assert.equal(litSet(10, 10, 1).size, 10);
  assert.equal(litSet(200, 77, 9).size, 77);
});

test("node layouts place n nodes inside the unit box", () => {
  const near = (a, b) => Math.abs(a - b) < 1e-9;
  for (const layout of ["row", "flow", "triangle", "ring"]) {
    for (let n = 2; n <= 6; n++) {
      const pts = nodeLayout(n, layout);
      assert.equal(pts.length, n, `${layout} ${n}`);
      for (const p of pts) assert.ok(p.x >= 0 && p.x <= 1 && p.y >= 0 && p.y <= 1, `${layout} ${n} in the box`);
      const keys = new Set(pts.map((p) => `${p.x.toFixed(3)},${p.y.toFixed(3)}`));
      assert.equal(keys.size, n, `${layout} ${n}: no two nodes on one spot`);
    }
  }
  const row = nodeLayout(4, "row");
  assert.ok(row.every((p) => near(p.y, 0.5)) && row.every((p, i) => i === 0 || p.x > row[i - 1].x));
  const tri = nodeLayout(3, "triangle");
  assert.ok(near(tri[0].x, 0.5) && tri[0].y < tri[1].y && near(tri[1].y, tri[2].y) && tri[1].x < tri[2].x);
  const ring = nodeLayout(4, "ring");
  assert.ok(near(ring[0].x, 0.5) && ring[0].y < 0.5, "a ring starts at the top");
  assert.ok(ring.every((p) => near(Math.hypot(p.x - 0.5, p.y - 0.5), Math.hypot(ring[0].x - 0.5, ring[0].y - 0.5))));
});

test("node links: a chain for row and flow, a closed loop for ring and triangle", () => {
  assert.deepEqual(nodeLinks(4, "row"), [[0, 1], [1, 2], [2, 3]]);
  assert.deepEqual(nodeLinks(3, "flow"), [[0, 1], [1, 2]]);
  assert.deepEqual(nodeLinks(4, "ring"), [[0, 1], [1, 2], [2, 3], [3, 0]]);
  assert.deepEqual(nodeLinks(3, "triangle"), [[0, 1], [1, 2], [2, 0]]);
  assert.deepEqual(nodeLinks(2, "ring"), [[0, 1]], "two nodes are linked once");
  // a triangle's links run round its edge: every node has two neighbours
  for (let n = 3; n <= 6; n++) {
    const deg = new Array(n).fill(0);
    for (const [a, b] of nodeLinks(n, "triangle")) { deg[a]++; deg[b]++; }
    assert.ok(deg.every((d) => d === 2), `triangle ${n}`);
  }
});

test("the badge finds a spot with no lit person under it", () => {
  const lit = litSet(125, 6, 42);
  const { cols, rows } = gridOf(125, 888, 34);
  const spot = emptySpot(cols, rows, 125, lit, 5, 2);
  assert.ok(spot);
  for (let r = spot.row; r < spot.row + 2; r++) {
    for (let c = spot.col; c < spot.col + 5; c++) assert.ok(!lit.has(r * cols + c), `cell ${r},${c} is dim`);
  }
  assert.ok(spot.col >= 0 && spot.col + 5 <= cols && spot.row >= 0 && spot.row + 2 <= rows);
  assert.deepEqual(emptySpot(cols, rows, 125, lit, 5, 2), spot, "the same every frame");
  assert.equal(emptySpot(4, 1, 4, new Set([0, 1, 2, 3]), 2, 1), null, "nowhere free");
});

test("the hero in the centre makes room once anything else is on the stage; pinned states keep their scale", () => {
  assert.equal(effectiveHeroState("center", true), "small-top");
  assert.equal(effectiveHeroState("center", false), "center");
  for (const s of ["pulse", "shake", "rings", "screen", "sleep", "locked"]) {
    assert.equal(effectiveHeroState(s, true), s);
  }
  for (const s of ["small-top", "corner", "hidden", "split"]) assert.equal(effectiveHeroState(s, true), s);
});

test("the hero timeline gains small-top while blocks are live, and centre again after", () => {
  const states = [{ atMs: 0, state: "center", text: null }, { atMs: 5000, state: "pulse", text: null },
    { atMs: 8000, state: "center", text: null }];
  const lives = [{ enterMs: 1000, exitMs: 2000 }, { enterMs: 6000, exitMs: 9000 }];
  const got = heroTimeline(states, lives).map((s) => [s.atMs, s.state]);
  assert.deepEqual(got, [[0, "center"], [1000, "small-top"], [2000 + EXIT_MS, "center"], [5000, "pulse"],
    [8000, "small-top"], [9000 + EXIT_MS, "center"]]);
  // what the hero does and whether it stacks are read from the same timeline
  const tl = heroTimeline(states, lives);
  assert.equal(heroAt(tl, 1500).state, "small-top");
  assert.deepEqual(heroSpan(tl, 1500), { id: "hero" });
});

test("a short gap between blocks does not bounce the hero back to the centre", () => {
  const states = [{ atMs: 0, state: "center" }];
  const lives = [{ enterMs: 100, exitMs: 1000 }, { enterMs: 1000 + EXIT_MS + HERO_MOVE_MS - 1, exitMs: 3000 }];
  assert.deepEqual(heroTimeline(states, lives).map((s) => [s.atMs, s.state]),
    [[0, "center"], [100, "small-top"], [3000 + EXIT_MS, "center"]]);
  const apart = [{ enterMs: 100, exitMs: 1000 }, { enterMs: 1000 + EXIT_MS + HERO_MOVE_MS, exitMs: 3000 }];
  assert.equal(heroTimeline(states, apart).length, 5);
});

test("the hero timeline keeps each state's fields, starts with blocks already live and leaves no-block reels alone", () => {
  const states = [{ atMs: 0, state: "center", text: "x", count: 4, labels: ["a"] }];
  assert.deepEqual(heroTimeline(states, [{ enterMs: 0 }]), [{ atMs: 0, state: "small-top", text: "x", count: 4, labels: ["a"] }]);
  assert.deepEqual(heroTimeline(states, []), states);
  assert.deepEqual(heroTimeline([], [{ enterMs: 0, exitMs: 10 }]), []);
  // a block before the hero's first state changes nothing before it
  assert.deepEqual(heroTimeline([{ atMs: 500, state: "corner" }], [{ enterMs: 0, exitMs: 1000 }]).map((s) => s.state), ["corner"]);
});

test("split dots fly out from the hero's centre to a triangle (a row for two)", () => {
  assert.deepEqual(splitPoints(3, 600, 400, 0), [{ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 0, y: 0 }]);
  const at1 = splitPoints(3, 600, 400, 1);
  const tri = nodeLayout(3, "triangle");
  at1.forEach((p, i) => {
    assert.ok(Math.abs(p.x - (tri[i].x - 0.5) * 600) < 1e-9);
    assert.ok(Math.abs(p.y - (tri[i].y - 0.5) * 400) < 1e-9);
  });
  assert.ok(at1[0].y < 0 && at1[1].x < 0 && at1[2].x > 0, "top, bottom-left, bottom-right");
  const two = splitPoints(2, 600, 400, 1);
  assert.deepEqual(two.map((p) => p.y), [0, 0]);
  assert.deepEqual(two.map((p) => p.x), [-150, 150]);
  assert.equal(splitPoints(6, 600, 400, 1).length, 6);
  const mid = splitPoints(3, 600, 400, 0.5)[1];
  assert.ok(mid.x < 0 && mid.x > at1[1].x * 1.2, "on its way, overshooting a little at most");
});

test("the robot blinks every 3 to 5 seconds, from the seed alone", () => {
  const closed = [];
  for (let t = 0; t < 30000; t += 10) if (blinkAt(t, 7) < 0.5) closed.push(t);
  const starts = closed.filter((t, i) => i === 0 || t - closed[i - 1] > 10);
  assert.ok(starts.length >= 5 && starts.length <= 10, `${starts.length} blinks`);
  for (let i = 1; i < starts.length; i++) {
    const gap = starts[i] - starts[i - 1];
    assert.ok(gap >= 3000 - 20 && gap <= 5000 + 20, `gap ${gap}`);
  }
  assert.equal(blinkAt(0, 7), 1);
  assert.equal(blinkAt(12345, 7), blinkAt(12345, 7));
  assert.notDeepEqual(starts, (() => {
    const c = [];
    for (let t = 0; t < 30000; t += 10) if (blinkAt(t, 8) < 0.5) c.push(t);
    return c.filter((t, i) => i === 0 || t - c[i - 1] > 10);
  })());
});

test("a pill ease that starts mid-ease continues from the current size, not the target", () => {
  const words = [{ startMs: 0 }, { startMs: 1000 }, { startMs: 1060 }];
  const sizes = [{ w: 100, h: 50 }, { w: 200, h: 50 }, { w: 300, h: 50 }];
  const before = pillAt(sizes, words, 1059.999);
  const at = pillAt(sizes, words, 1060);
  assert.ok(before.w > 100 && before.w < 200);
  assert.ok(Math.abs(at.w - before.w) < 0.01, `${at.w} vs ${before.w}`);   // no jump at the second word
  assert.equal(pillAt(sizes, words, 1180).w, 300);
});

test("the caption pill grows with the revealed words, eased over 120 ms", () => {
  const words = [{ startMs: 1000 }, { startMs: 1300 }, { startMs: 1600 }];
  const sizes = [{ w: 100, h: 50 }, { w: 200, h: 50 }, { w: 300, h: 100 }];
  assert.equal(PILL_EASE_MS, 120);
  assert.deepEqual(pillAt(sizes, words, 500), { w: 100, h: 50, k: 0 });
  assert.deepEqual(pillAt(sizes, words, 1100), { w: 100, h: 50, k: 0 });
  const start = pillAt(sizes, words, 1300);
  assert.deepEqual(start, { w: 100, h: 50, k: 1 });
  const mid = pillAt(sizes, words, 1360);
  assert.ok(mid.w > 100 && mid.w < 200 && mid.k === 1);
  assert.deepEqual(pillAt(sizes, words, 1420), { w: 200, h: 50, k: 1 });
  assert.deepEqual(pillAt(sizes, words, 9000), { w: 300, h: 100, k: 2 });
  assert.deepEqual(pillAt(sizes, words, 1360), pillAt(sizes, words, 1360));
  assert.equal(pillAt([], [], 5), null);
});

test("unspaced scripts are told from the language, like markup.is_unspaced", () => {
  assert.deepEqual(["ja", "ja-JP", "zh", "th", "ZH-hant"].map(isUnspaced), [true, true, true, true, true]);
  assert.deepEqual(["en", "hi", "ko", "", undefined, null].map(isUnspaced), [false, false, false, false, false, false]);
});

test("accent markup splits into words; a partly accented word stays one word with its accented runs", () => {
  assert.deepEqual(accentUnits("desi *jugaad* 😎"), [{ t: "desi", a: false }, { t: "jugaad", a: true }, { t: "😎", a: false }]);
  assert.deepEqual(accentUnits("*R*etrieval *A*ugmented"),
    [{ t: "Retrieval", a: false, accentSpans: [[0, 1]] }, { t: "Augmented", a: false, accentSpans: [[0, 1]] }]);
  assert.deepEqual(accentUnits("*two words* here"), [{ t: "two", a: true }, { t: "words", a: true }, { t: "here", a: false }]);
  // punctuation outside the stars keeps a whole-word accent; a lone `*` is literal
  assert.deepEqual(accentUnits("*ChatGPT*, 5 * 3"),
    [{ t: "ChatGPT,", a: true }, { t: "5", a: false }, { t: "*", a: false }, { t: "3", a: false }]);
  // Japanese has no spaces: one word, the accented run inside it
  assert.deepEqual(accentUnits("小さい*モデル*"), [{ t: "小さいモデル", a: false, accentSpans: [[3, 6]] }]);
  assert.deepEqual(accentUnits(""), []);
});

test("a word's pieces: plain and accented runs in order", () => {
  assert.deepEqual(accentPieces({ t: "Retrieval", a: false, accentSpans: [[0, 1]] }),
    [{ t: "R", a: true }, { t: "etrieval", a: false }]);
  assert.deepEqual(accentPieces({ t: "remarkable", a: false, accentSpans: [[2, 6]] }),
    [{ t: "re", a: false }, { t: "mark", a: true }, { t: "able", a: false }]);
  assert.deepEqual(accentPieces({ t: "zinda", a: true }), [{ t: "zinda", a: true }]);
  assert.deepEqual(accentPieces({ t: "hai", a: false }), [{ t: "hai", a: false }]);
});
