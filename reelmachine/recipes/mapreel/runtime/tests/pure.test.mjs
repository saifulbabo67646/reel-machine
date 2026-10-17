import { test } from "node:test";
import assert from "node:assert/strict";
import { zoomPath, cameraAt, popState, fillAlpha, activeWord, backOut, fitFontPx, badgeLines, ghostCentre, ringsBox, countText } from "../pure.mjs";

const A = { x: 0.2, y: 0.4, s: 1080 };
const B = { x: 0.55, y: 0.35, s: 20000 };

test("a zoom path starts and ends on its frames", () => {
  const path = zoomPath(A, B);
  for (const [t, f] of [[0, A], [1, B]]) {
    const p = path(t);
    assert.ok(Math.abs(p.x - f.x) < 1e-9 && Math.abs(p.y - f.y) < 1e-9 && Math.abs(p.s - f.s) / f.s < 1e-9);
  }
});

test("a long flight pulls back before it lands", () => {
  const path = zoomPath({ x: 0.1, y: 0.4, s: 20000 }, { x: 0.8, y: 0.4, s: 20000 });
  assert.ok(path(0.5).s < 20000 / 3);
});

test("the camera lands at flyMs and then drifts in slightly", () => {
  const cam = { from: A, to: B, flyMs: 900, drift: 0.04 };
  assert.deepEqual(cameraAt(cam, 0), A);
  const landed = cameraAt(cam, 900);
  assert.ok(Math.abs(landed.s - B.s) / B.s < 1e-6);
  assert.ok(cameraAt(cam, 4000).s > B.s && cameraAt(cam, 4000).s <= B.s * 1.04 + 1e-6);
});

test("a held camera does not move", () => {
  const cam = { from: B, to: B, flyMs: 0, drift: 0 };
  assert.deepEqual(cameraAt(cam, 0), B);
  assert.deepEqual(cameraAt(cam, 3000), B);
});

test("an item is hidden before it pops, overshoots, then settles", () => {
  assert.equal(popState(1000, 900).visible, false);
  const mid = popState(1000, 1150);
  assert.ok(mid.visible && mid.alpha > 0 && mid.blur > 0);
  const done = popState(1000, 1600);
  assert.equal(done.alpha, 1);
  assert.equal(done.blur, 0);
  assert.ok(Math.abs(done.scale - 1) < 1e-6);
  assert.ok(backOut(0.7) > 1); // the overshoot
});

test("fills fade in from their time", () => {
  assert.equal(fillAlpha(700, 600), 0);
  assert.ok(fillAlpha(700, 900) > 0 && fillAlpha(700, 900) < 1);
  assert.equal(fillAlpha(700, 2000), 1);
});

test("the active word is the one being said, held briefly after", () => {
  const words = [{ start: 0.2, end: 0.5 }, { start: 0.6, end: 0.9 }];
  assert.equal(activeWord(words, 0.1), -1);
  assert.equal(activeWord(words, 0.3), 0);
  assert.equal(activeWord(words, 0.55), 0);
  assert.equal(activeWord(words, 0.7), 1);
  assert.equal(activeWord(words, 2), -1);
});

test("a long ghosted name shrinks to fit; a short one keeps its size", () => {
  assert.equal(fitFontPx(173, 600, 994), 173);
  const px = fitFontPx(173, 2600, 994);
  assert.ok(px < 173 && (2600 * px) / 173 <= 994);
});

test("a long multi-word badge breaks near its middle; short or single words stay whole", () => {
  assert.deepEqual(badgeLines("Comme des Garçons"), ["Comme des", "Garçons"]);
  assert.deepEqual(badgeLines("Calvin Klein"), ["Calvin", "Klein"]);
  assert.deepEqual(badgeLines("Fjällräven"), ["Fjällräven"]);
  assert.deepEqual(badgeLines("H&M"), ["H&M"]);
});

test("a ghosted name near the edge is pulled on screen, drift and rotation included", () => {
  const W = 1080, H = 1920, angle = -0.14, w = 900, h = 120, drift = 60;
  const c = Math.cos(angle), s = Math.abs(Math.sin(angle));
  const hx = (c * w) / 2 + (s * h) / 2, hy = (s * w) / 2 + (c * h) / 2;
  for (const [px, py] of [[1000, 900], [40, 900], [540, 20], [540, 1900]]) {
    const at = ghostCentre(px, py, w, h, angle, drift, W, H);
    assert.ok(at.x - hx >= 0 && at.x + drift + hx <= W, `x ${at.x} from ${px}`);
    assert.ok(at.y - hy >= 0 && at.y + hy <= H, `y ${at.y} from ${py}`);
  }
  assert.deepEqual(ghostCentre(500, 900, 300, 120, angle, drift, W, H), { x: 500, y: 900 }); // already fine
});

test("a place's on-screen box spans all its rings", () => {
  const cam = { x: 0.5, y: 0.5, s: 1000 };
  const rings = [[[0.5, 0.5], [0.51, 0.5], [0.51, 0.52]], [[0.49, 0.53], [0.495, 0.53], [0.495, 0.535]]];
  const box = ringsBox(rings, cam);
  const near = (a, b) => Math.abs(a - b) < 1e-6;
  assert.ok(near(box.x0, 530) && near(box.x1, 550) && near(box.y0, 960) && near(box.y1, 995));
  assert.ok(near(box.w, 20) && near(box.h, 35));
});

test("a stat counts up from zero, keeping its decimals, commas, prefix and suffix", () => {
  const pct = { prefix: "", number: 70, decimals: 0, comma: false, suffix: "%" };
  assert.equal(countText(pct, 0, "70%"), "0%");
  assert.equal(countText(pct, 0.5, "70%"), "61%"); // eased out: most of the way by half time
  assert.equal(countText(pct, 1, "70%"), "70%");
  const money = { prefix: "$", number: 1200.5, decimals: 2, comma: true, suffix: " bn" };
  assert.equal(countText(money, 0, "$1,200.50 bn"), "$0.00 bn");
  assert.equal(countText(money, 0.5, "$1,200.50 bn"), "$1,050.44 bn");
  assert.equal(countText(money, 1, "$1,200.50 bn"), "$1,200.50 bn");
  for (const p of [0, 0.5, 1]) assert.equal(countText(null, p, "Most"), "Most");
});
