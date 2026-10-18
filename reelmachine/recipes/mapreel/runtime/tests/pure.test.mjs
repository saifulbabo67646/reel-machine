import { test } from "node:test";
import assert from "node:assert/strict";
import { zoomPath, cameraAt, popState, fillAlpha, activeWord, backOut, fitFontPx, badgeLines, ghostCentre, ringsBox, countText, cardLayout, luminance, textOn, readableOnWhite, contrast, slideOffset, tagCentre, leaderLine, worldBands, legendY, LEGEND_Y, LEGEND_H, LEGEND_AIR, badgeLayout, panelNameY } from "../pure.mjs";

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

test("the stat card sits right of the rank badge and its text shrinks to fit", () => {
  const short = cardLayout(300, 0);
  assert.deepEqual(short, { cx: 689, width: 380, valuePx: 104, labelPx: 40 });
  for (const [v, l] of [[300, 0], [900, 200], [400, 1200], [1500, 1500]]) {
    const c = cardLayout(v, l);
    assert.ok(c.cx - c.width / 2 >= 330 - 1e-9 && c.cx + c.width / 2 <= 1048 + 1e-9);
    assert.ok(v * c.valuePx / 104 <= c.width - 80 + 1e-9 && l * c.labelPx / 40 <= c.width - 80 + 1e-9);
  }
  assert.ok(cardLayout(900, 0).valuePx < 104 && cardLayout(0, 1200).labelPx < 40);
});

test("a versus panel slides in from its side and settles", () => {
  assert.equal(slideOffset(120, 0, -1), -1080);   // before it enters: a whole screen off to the left
  assert.equal(slideOffset(120, 120, 1), 1080);   // entering from the right
  const mid = slideOffset(0, 175, -1);
  assert.ok(mid > -1080 && mid < 0 && Math.abs(mid + 540) < 1e-9); // eased: half way at half time
  assert.ok(slideOffset(0, 350, -1) === 0);   // home (-0 counts)
  assert.equal(slideOffset(120, 2000, 1), 0);
});

test("a camera's screen centre can be a panel's", () => {
  const cam = { x: 0.5, y: 0.5, s: 1000 };
  const rings = [[[0.49, 0.49], [0.51, 0.51]]];
  const box = ringsBox(rings, cam, { cx: 540, cy: 480 });
  assert.ok(Math.abs(box.x0 - 530) < 1e-6 && Math.abs(box.y0 - 470) < 1e-6 && Math.abs(box.y1 - 490) < 1e-6);
});

test("a card can be laid out across any span", () => {
  assert.deepEqual(cardLayout(300, 0), cardLayout(300, 0, 330, 1048));
  const c = cardLayout(2000, 0, 60, 1020);
  assert.equal(c.cx, 540);
  assert.ok(c.width <= 960 + 1e-9 && 2000 * c.valuePx / 104 <= 960 - 80 + 1e-9);
});

test("a region's name tag stays on screen and off the caption band", () => {
  const W = 1080, H = 1920, band = { y0: 1320, y1: 1406 };
  const inside = (at, w, h) => at.x - w / 2 >= 0 && at.x + w / 2 <= W && at.y - h / 2 >= 0 && at.y + h / 2 <= H;
  for (const [px, py] of [[-50, 400], [1100, 400], [540, -30], [540, 1990]]) {
    const at = tagCentre(px, py, 300, 52, W, H, band);
    assert.ok(inside(at, 300, 52), `${px},${py} -> ${at.x},${at.y}`);
  }
  assert.deepEqual(tagCentre(540, 900, 300, 52, W, H, band), { x: 540, y: 900 }); // already fine
  // on the band: to its nearer side
  assert.equal(tagCentre(540, 1340, 300, 52, W, H, band).y, 1320 - 26);
  assert.equal(tagCentre(540, 1390, 300, 52, W, H, band).y, 1406 + 26);
});

test("a tag far from its region gets a leader from its box edge to the region", () => {
  const box = { x: 540, y: 800, hw: 100, hh: 26 };
  assert.equal(leaderLine(box, 560, 820), null); // on its region: none
  assert.equal(leaderLine(box, 540, 860), null); // 34 px below the box: still close enough
  const down = leaderLine(box, 540, 1000);
  assert.deepEqual(down, { x0: 540, y0: 826, x1: 540, y1: 1000 });
  const side = leaderLine(box, 300, 800);
  assert.deepEqual(side, { x0: 440, y0: 800, x1: 300, y1: 800 });
});

test("a world view too wide for the screen's height has a band above and below; a filled one has none", () => {
  assert.deepEqual(worldBands({ x: 0.5, y: 0.5, s: 1920 }), { top: 0, bottom: 0, fadeTop: 0, fadeBottom: 0 });
  assert.deepEqual(worldBands({ x: 0.5, y: 0.5, s: 1080 }), { top: 420, bottom: 420, fadeTop: 96, fadeBottom: 96 });
  const sliver = worldBands({ x: 0.5, y: 0.5, s: 1880 });
  assert.equal(sliver.top, 20);
  assert.equal(sliver.fadeTop, 20); // the fade never runs deeper than the band is tall
  assert.deepEqual(worldBands({ x: 0.3, y: 0.4, s: 8000 }), { top: 0, bottom: 0, fadeTop: 0, fadeBottom: 0 }); // zoomed in
});

test("the legend sits in the top band when the band holds it, else in its usual place", () => {
  assert.equal(legendY(0), LEGEND_Y);
  assert.equal(legendY(200), LEGEND_Y); // too thin: the legend stays over the map
  assert.equal(legendY(340), LEGEND_Y); // brands.json's opening: the usual place is already inside the band
  assert.equal(legendY(420), 210);      // centred in a full-width world's band
  for (const band of [276, 300, 340, 420]) {
    assert.ok(legendY(band) + LEGEND_H / 2 + LEGEND_AIR <= band, `${band}`); // clear of the map's edge
  }
});

test("a badge stacks its emoji over its plate about its anchor; the half heights geometry.py spaces by", () => {
  const close = (a, b) => Math.abs(a - b) < 1e-9;
  // size 200 at the nominal font (0.24 x size = 48 px): the numbers tests/test_mapreel_geometry.py pins
  const two = badgeLayout(200, 48, 2, true);
  assert.ok(close(two.halfH, 99.8), `${two.halfH}`);
  assert.ok(close(badgeLayout(200, 48, 1, true).halfH, 74.6));
  assert.ok(close(badgeLayout(200, 48, 1, false).halfH, 37.2));
  // the emoji's glyph ends on the plate's top edge, overlapping it a little; the plate ends at the half height
  assert.ok(close(two.emojiY + 0.6 * 0.34 * 200, two.plateY + 0.1 * 0.34 * 200));
  assert.ok(close(two.plateY + two.plateH, two.halfH));
  assert.ok(close(two.emojiY - 0.6 * 0.34 * 200, -two.halfH));
  // lines are centred in the plate
  assert.ok(close((two.lineYs[0] + two.lineYs[1]) / 2, two.plateY + two.plateH / 2));
});

test("the third versus panel's name drops above its stat card, well clear of the caption", () => {
  const band = { y0: 1320, y1: 1406 };
  const third = [0, 1280, 1080, 640];
  const y = panelNameY(third, 72, -Infinity, band, 1732);
  assert.equal(y, 1732 - 14 - 36);
  assert.ok(y - 36 >= band.y1 + 40, `${y}`);
  // panels whose name is clear of the caption keep it at their top
  assert.equal(panelNameY([0, 0, 1080, 640], 72, -Infinity, band, 452), 0.12 * 640);
  assert.equal(panelNameY([0, 640, 1080, 640], 72, 640 + 95 + 14, band, 1092), 640 + 95 + 14 + 36);
});

test("a finished count shows the value exactly as written", () => {
  // what a count spec might make of these: the end of the count is always the raw text
  const odd = { prefix: "", number: 105, decimals: 0, comma: false, suffix: "%" };
  assert.equal(countText(odd, 1, "10,5%"), "10,5%");
  const indian = { prefix: "", number: 100000, decimals: 0, comma: true, suffix: "" };
  assert.equal(countText(indian, 1, "1,00,000"), "1,00,000");
  const trailing = { prefix: "$", number: 1200, decimals: 0, comma: true, suffix: "" };
  assert.equal(countText(trailing, 1, "$1,200,"), "$1,200,");
  assert.equal(countText(trailing, 2, "$1,200,"), "$1,200,");
  assert.equal(countText(trailing, 0.5, "$1,200,"), "$1,050");
});

const PALETTE = ["#8a2be2", "#e0201b", "#3333ff", "#f28c0f", "#e01ec8", "#35b52a",
                 "#00b3c7", "#ffcc00", "#ff5a7a", "#1e90ff", "#7cdf00", "#b8005c"];

test("relative luminance follows WCAG", () => {
  assert.equal(luminance("#000000"), 0);
  assert.equal(luminance("#ffffff"), 1);
  assert.ok(Math.abs(luminance("#808080") - 0.2159) < 1e-3);
  assert.equal(luminance("#fff"), 1);
  assert.ok(Math.abs(contrast("#ffffff", "#000000") - 21) < 1e-9);
});

test("a light plate gets dark text and a dark plate white", () => {
  for (const light of ["#ffcc00", "#7cdf00", "#00b3c7"]) assert.equal(textOn(light), "#111111", light);
  for (const dark of ["#8a2be2", "#e0201b", "#3333ff", "#b8005c", "#222222"]) assert.equal(textOn(dark), "#ffffff", dark);
  // whichever it picks, the text reads: at least 3:1 on every palette colour
  for (const c of PALETTE) assert.ok(contrast(textOn(c), c) >= 3, c);
});

test("a stat value on the white card is darkened until it reads", () => {
  for (const c of PALETTE) assert.ok(contrast(readableOnWhite(c), "#ffffff") >= 3, c);
  assert.equal(readableOnWhite("#8a2be2"), "#8a2be2"); // dark enough already: kept
  const gold = readableOnWhite("#ffcc00");
  assert.notEqual(gold, "#ffcc00");
  // the same hue, only darker
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(gold.slice(i, i + 2), 16));
  assert.ok(r > g && g > b && Math.abs(r / g - 255 / 204) < 0.05);
  assert.equal(readableOnWhite(null), "#111111");
});
