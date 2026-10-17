// reelmachine/recipes/mapreel/runtime/draw.mjs
// Draws one frame of a map segment on a 2D canvas.
import {
  cameraAt, fillAlpha, popState, activeWord, fitFontPx, badgeLines, ghostCentre, ringsBox, countText, clamp01, POP_MS, cardLayout,
  slideOffset, tagCentre, leaderLine,
} from "./pure.mjs";

const W = 1080, H = 1920;
const FONT = '"Arial Black", "Helvetica Neue", Arial, sans-serif';

// `view` is where the camera's centre lands on screen: the screen's centre, or a versus panel's
const SCREEN = { cx: W / 2, cy: H / 2 };

function toScreen(cam, x, y, view = SCREEN) {
  return [(x - cam.x) * cam.s + view.cx, (y - cam.y) * cam.s + view.cy];
}

function tracePlace(ctx, cam, rings, view = SCREEN) {
  ctx.beginPath();
  for (const ring of rings) {
    ring.forEach(([x, y], i) => {
      const [px, py] = toScreen(cam, x, y, view);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    ctx.closePath();
  }
}

function drawLayers(ctx, cam, layers, images, view = SCREEN) {
  ctx.fillStyle = "#0b2236";
  ctx.fillRect(0, 0, W, H);
  for (const layer of layers) {
    const img = images.get(layer.url);
    if (!img) continue;
    const [x0, y0, x1, y1] = layer.bbox;
    for (const shift of [-1, 0, 1]) { // the world repeats east and west
      if (layer.bbox[2] - layer.bbox[0] < 0.999 && shift !== 0) continue;
      const [px0, py0] = toScreen(cam, x0 + shift, y0, view);
      const [px1, py1] = toScreen(cam, x1 + shift, y1, view);
      if (px1 < 0 || px0 > W) continue;
      ctx.drawImage(img, px0, py0, px1 - px0, py1 - py0);
    }
  }
}

const MARK_BELOW = 70, MARK_R = 46; // px: a place smaller than this on both sides gets a ring

function fillPlace(ctx, cam, place, alpha, seconds = 0, view = SCREEN) {
  if (alpha <= 0) return;
  const box = ringsBox(place.rings, cam, view);
  if (box.w < MARK_BELOW && box.h < MARK_BELOW) markPlace(ctx, box, place.colour, alpha, seconds);
  ctx.save();
  ctx.lineJoin = "round"; // a mitred join on a jagged coast (Brittany, Chesapeake) is a long bright spike
  ctx.globalAlpha = 0.8 * alpha;
  tracePlace(ctx, cam, place.rings, view);
  ctx.fillStyle = place.colour;
  ctx.fill("evenodd");
  ctx.globalAlpha = alpha;
  ctx.clip("evenodd"); // inner glow: a soft, bright stroke kept inside the border
  ctx.shadowColor = "rgba(255,255,255,0.9)";
  ctx.shadowBlur = 28;
  ctx.lineWidth = 10;
  ctx.strokeStyle = "rgba(255,255,255,0.55)";
  ctx.stroke();
  ctx.restore();
  ctx.save();
  ctx.lineJoin = "round";
  ctx.globalAlpha = 0.7 * alpha;
  tracePlace(ctx, cam, place.rings, view);
  ctx.lineWidth = 2;
  ctx.strokeStyle = "rgba(255,255,255,0.8)";
  ctx.stroke();
  ctx.restore();
}

// A tiny place on a world view (Singapore, Fiji's islands) would be a speck: ring it, pulsing.
function markPlace(ctx, box, colour, alpha, seconds) {
  const r = MARK_R * (1 + 0.08 * Math.sin(seconds * 6));
  const cx = (box.x0 + box.x1) / 2, cy = (box.y0 + box.y1) / 2;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.lineWidth = 7 + 2 * 2; // the white outline shows 2 px either side of the coloured ring
  ctx.strokeStyle = "#ffffff";
  ctx.stroke();
  ctx.lineWidth = 7;
  ctx.strokeStyle = colour;
  ctx.stroke();
  ctx.restore();
}

const CAPTION_Y = H * 0.71, CAPTION_HALF = 43; // the caption band: 60 px text and its 10 px stroke, plus air

// Every state's border in a regions segment: thin white lines over the faint country.
function borders(ctx, cam, rings, alpha) {
  if (alpha <= 0 || !rings || !rings.length) return;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.lineJoin = "round";
  tracePlace(ctx, cam, rings);
  ctx.lineWidth = 2;
  ctx.strokeStyle = "rgba(255,255,255,0.55)";
  ctx.stroke();
  ctx.restore();
}

const TAG_PX = 44, TAG_STROKE = 8;

// A region's name at its tag point as it fills: white, dark-stroked, kept on screen and off the captions. A tag
// moved off its region (clear of an item or another tag) points back at it: a thin leader and a dot.
function regionTag(ctx, cam, region, alpha) {
  if (alpha <= 0) return;
  const text = region.tag || region.name;
  const [px, py] = toScreen(cam, region.label.x, region.label.y);
  ctx.save();
  ctx.font = `900 ${TAG_PX}px ${FONT}`;
  const w = ctx.measureText(text).width + TAG_STROKE, h = TAG_PX + TAG_STROKE;
  const at = tagCentre(px, py, w, h, W, H, { y0: CAPTION_Y - CAPTION_HALF, y1: CAPTION_Y + CAPTION_HALF });
  ctx.globalAlpha = alpha;
  const anchor = region.anchor ? toScreen(cam, region.anchor.x, region.anchor.y) : null;
  const lead = anchor && leaderLine({ x: at.x, y: at.y, hw: w / 2, hh: h / 2 }, anchor[0], anchor[1]);
  if (lead) {
    ctx.lineCap = "round";
    for (const [width, colour] of [[6, "rgba(0,0,0,0.7)"], [2.5, "#ffffff"]]) {
      ctx.beginPath();
      ctx.moveTo(lead.x0, lead.y0);
      ctx.lineTo(lead.x1, lead.y1);
      ctx.lineWidth = width;
      ctx.strokeStyle = colour;
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.arc(lead.x1, lead.y1, 7, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff";
    ctx.fill();
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(0,0,0,0.7)";
    ctx.stroke();
  }
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  ctx.lineWidth = TAG_STROKE;
  ctx.strokeStyle = "rgba(0,0,0,0.85)";
  ctx.strokeText(text, at.x, at.y);
  ctx.fillStyle = "#ffffff";
  ctx.fillText(text, at.x, at.y);
  ctx.restore();
}

const GHOST_ANGLE = -0.14, GHOST_DRIFT = 12; // radians; px per second

function ghostName(ctx, cam, place, alpha, seconds, duration) {
  if (alpha <= 0) return;
  const [px, py] = toScreen(cam, place.label.x, place.label.y);
  const drift = duration * GHOST_DRIFT;
  const cos = Math.cos(GHOST_ANGLE), sin = Math.abs(Math.sin(GHOST_ANGLE));
  const base = Math.round(W * 0.16);
  ctx.save();
  ctx.font = `900 ${base}px ${FONT}`;
  // a long name ("United States of America") shrinks so that, rotated and drifting, it stays on screen
  const room = Math.min(W * 0.92, (W - 16 - drift - base * sin) / cos);
  const size = fitFontPx(base, ctx.measureText(place.name).width, room);
  ctx.font = `900 ${size}px ${FONT}`;
  const at = ghostCentre(px, py, ctx.measureText(place.name).width, size, GHOST_ANGLE, drift, W, H);
  ctx.globalAlpha = 0.22 * alpha;
  ctx.translate(at.x + seconds * GHOST_DRIFT, at.y);
  ctx.rotate(GHOST_ANGLE);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = "#ffffff";
  ctx.fillText(place.name, 0, 0);
  ctx.restore();
}

function drawItem(ctx, item, ms, images) {
  const pop = popState(item.atMs, ms);
  if (!pop.visible) return;
  const driftY = -Math.max(0, ms - item.atMs) / 1000 * 6;
  ctx.save();
  ctx.globalAlpha = pop.alpha;
  if (pop.blur > 0.2) ctx.filter = `blur(${pop.blur.toFixed(1)}px)`;
  ctx.translate(item.x, item.y + driftY);
  ctx.scale(pop.scale, pop.scale);
  ctx.shadowColor = "rgba(0,0,0,0.45)";
  ctx.shadowBlur = 18;
  ctx.shadowOffsetY = 6;
  const img = item.image ? images.get(item.image) : null;
  if (img && img.width && img.height) { // an SVG with no intrinsic size has none: draw the badge
    const k = Math.min(item.size / img.width, (item.size * 0.62) / img.height);
    ctx.drawImage(img, (-img.width * k) / 2, (-img.height * k) / 2, img.width * k, img.height * k);
  } else {
    const lines = badgeLines(item.name);
    const widest = () => Math.max(...lines.map((line) => ctx.measureText(line).width));
    let size = Math.round(item.size * 0.24);
    ctx.font = `900 ${size}px ${FONT}`;
    while (widest() > item.size * 1.1 && size > 28) {
      size -= 4;
      ctx.font = `900 ${size}px ${FONT}`;
    }
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    if (item.emoji) {
      ctx.font = `${Math.round(item.size * 0.34)}px sans-serif`;
      ctx.fillText(item.emoji, 0, -size * 0.95);
      ctx.font = `900 ${size}px ${FONT}`;
    }
    const top = (item.emoji ? size * 0.25 : 0) - ((lines.length - 1) * size * 1.05) / 2;
    if (item.plate) { // a plate in its region's colour, white-edged: readable over sea or dark land
      const pw = widest() + size * 0.9, ph = lines.length * size * 1.05 + size * 0.5;
      ctx.beginPath();
      ctx.roundRect(-pw / 2, top - size * 0.525 - size * 0.25, pw, ph, ph / 2);
      ctx.fillStyle = item.plate;
      ctx.fill();
      ctx.shadowColor = "transparent";
      ctx.lineWidth = 4;
      ctx.strokeStyle = "#ffffff";
      ctx.stroke();
      ctx.fillStyle = "#ffffff";
    } else {
      ctx.shadowColor = "rgba(255,255,255,0.75)";
      ctx.shadowBlur = 14;
      ctx.shadowOffsetY = 0;
      ctx.fillStyle = "#111111";
    }
    lines.forEach((line, k) => ctx.fillText(line, 0, top + k * size * 1.05));
  }
  ctx.restore();
}


function caption(ctx, words, seconds) {
  const i = activeWord(words, seconds);
  if (i < 0) return;
  const w = words[i];
  ctx.save();
  ctx.font = `900 60px ${FONT}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 10;
  ctx.strokeStyle = "rgba(0,0,0,0.85)";
  ctx.lineJoin = "round";
  ctx.strokeText(w.text, W / 2, CAPTION_Y);
  ctx.fillStyle = w.hot ? "#ffe14a" : "#ffffff";
  ctx.fillText(w.text, W / 2, CAPTION_Y);
  ctx.restore();
}

function legend(ctx, text) {
  if (!text) return;
  ctx.save();
  ctx.font = `900 50px ${FONT}`;
  const width = ctx.measureText(text).width + 64;
  ctx.fillStyle = "rgba(255,255,255,0.94)";
  ctx.beginPath();
  ctx.roundRect((W - width) / 2, 150, width, 86, 24);
  ctx.fill();
  ctx.fillStyle = "#111";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, W / 2, 193);
  ctx.restore();
}

const RANK_X = 190, RANK_Y = 330, RANK_R = 125, GOLD = "#ffcc00", SHINE_MS = 600;

// A ranking's big #N disc, top left, popping in as the country fills; number one is gold and shines once.
function rankBadge(ctx, rank, ms) {
  const pop = popState(rank.atMs, ms);
  if (!pop.visible) return;
  ctx.save();
  ctx.globalAlpha = pop.alpha;
  if (pop.blur > 0.2) ctx.filter = `blur(${pop.blur.toFixed(1)}px)`;
  ctx.translate(RANK_X, RANK_Y);
  ctx.scale(pop.scale, pop.scale);
  ctx.beginPath();
  ctx.arc(0, 0, RANK_R, 0, Math.PI * 2);
  ctx.shadowColor = "rgba(0,0,0,0.45)";
  ctx.shadowBlur = 24;
  ctx.shadowOffsetY = 8;
  ctx.fillStyle = rank.gold ? GOLD : rank.colour;
  ctx.fill();
  ctx.shadowColor = "transparent";
  const t = (ms - rank.atMs - POP_MS) / SHINE_MS;
  if (rank.gold && t > 0 && t < 1) { // a white band sweeps the disc diagonally once the pop settles
    ctx.save();
    ctx.clip();
    ctx.rotate(Math.PI / 4);
    ctx.fillStyle = "rgba(255,255,255,0.6)";
    ctx.fillRect(-1.6 * RANK_R + t * 3.2 * RANK_R - 30, -2 * RANK_R, 60, 4 * RANK_R);
    ctx.restore();
  }
  ctx.lineWidth = 10;
  ctx.strokeStyle = "#ffffff";
  ctx.stroke();
  const text = `#${rank.n}`;
  ctx.font = `900 120px ${FONT}`;
  const size = fitFontPx(120, ctx.measureText(text).width, RANK_R * 1.6); // "#99" stays inside the ring
  ctx.font = `900 ${size}px ${FONT}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = rank.gold ? "#111111" : "#ffffff";
  ctx.fillText(text, 0, 6);
  ctx.restore();
}

const STAT_TOP = 150, STAT_PAD = 28, COUNT_MS = 700;

// A white card, top right of the rank badge: the stat's value in the place colour counting up, its label below.
// `at` ({x0, x1, cy, scale, maxY}) places it elsewhere: centred in x0..x1 at height cy, scaled, its bottom
// never below maxY (a versus panel).
function statCard(ctx, stat, ms, colour, at = null) {
  const pop = popState(stat.atMs, ms);
  if (!pop.visible) return;
  ctx.save();
  ctx.font = `900 104px ${FONT}`;
  const valueW = ctx.measureText(stat.value).width; // measured at its final text, so the card never jitters
  ctx.font = `700 40px ${FONT}`;
  const labelW = stat.label ? ctx.measureText(stat.label).width : 0;
  const { cx, width, valuePx, labelPx } = at ? cardLayout(valueW, labelW, at.x0, at.x1) : cardLayout(valueW, labelW);
  const height = STAT_PAD * 2 + valuePx + (stat.label ? 12 + labelPx : 0);
  const scale = pop.scale * (at ? at.scale : 1);
  ctx.globalAlpha = pop.alpha;
  if (pop.blur > 0.2) ctx.filter = `blur(${pop.blur.toFixed(1)}px)`;
  ctx.translate(cx, at ? Math.min(at.cy, at.maxY - (height * at.scale) / 2) : STAT_TOP + height / 2);
  ctx.scale(scale, scale);
  ctx.shadowColor = "rgba(0,0,0,0.45)";
  ctx.shadowBlur = 18;
  ctx.shadowOffsetY = 6;
  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.roundRect(-width / 2, -height / 2, width, height, 24);
  ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = `900 ${valuePx}px ${FONT}`;
  ctx.fillStyle = colour || "#111111";
  ctx.fillText(countText(stat.count, clamp01((ms - stat.atMs) / COUNT_MS), stat.value), 0,
               -height / 2 + STAT_PAD + valuePx / 2);
  if (stat.label) {
    ctx.font = `700 ${labelPx}px ${FONT}`;
    ctx.fillStyle = "#222222";
    ctx.fillText(stat.label, 0, height / 2 - STAT_PAD - labelPx / 2);
  }
  ctx.restore();
}

const PANEL_CARD_INSET = 60, VS_R = 95, VS_GAP = 14;

// The country's name across the top of its panel: white, dark-stroked, shrunk to the panel's width,
// never higher than `minY` (the panel under the VS disc keeps its name clear of it) and never on the
// caption band (with three panels the third name would sit on it: it moves just below).
function panelName(ctx, name, rect, minY) {
  const [rx, ry, rw, rh] = rect;
  ctx.save();
  ctx.font = `900 72px ${FONT}`;
  const size = fitFontPx(72, ctx.measureText(name).width, rw * 0.9);
  ctx.font = `900 ${size}px ${FONT}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  ctx.lineWidth = 10;
  ctx.strokeStyle = "rgba(0,0,0,0.85)";
  let y = Math.max(ry + 0.12 * rh, minY + size / 2);
  if (y + size / 2 > CAPTION_Y - CAPTION_HALF && y - size / 2 < CAPTION_Y + CAPTION_HALF) {
    y = CAPTION_Y + CAPTION_HALF + VS_GAP + size / 2;
  }
  ctx.strokeText(name, rx + rw / 2, y);
  ctx.fillStyle = "#ffffff";
  ctx.fillText(name, rx + rw / 2, y);
  ctx.restore();
}

// One versus panel, sliding in from its side (even k from the left, odd from the right): its own map,
// its country filled, its name on top and its stat card counting up near the bottom.
// `discY`: the boundary the VS disc sits on; the panels either side keep their card and name clear of it.
function drawPanel(ctx, doc, panel, k, ms, seconds, images, discY) {
  const [rx, ry, rw, rh] = panel.rect;
  const dx = slideOffset(panel.enterMs, ms, k % 2 ? 1 : -1);
  if (Math.abs(dx) >= W) return;
  const view = { cx: rx + rw / 2, cy: ry + rh / 2 };
  ctx.save();
  ctx.translate(dx, 0);
  ctx.beginPath();
  ctx.rect(rx, ry, rw, rh);
  ctx.clip();
  // the world under the panel's sharp layer: a panel whose layer failed still shows a map
  drawLayers(ctx, panel.camera, [...doc.layers, ...panel.layers], images, view);
  fillPlace(ctx, panel.camera, panel.place, fillAlpha(0, ms), seconds, view);
  const below = discY !== null && ry === discY, above = discY !== null && ry + rh === discY;
  panelName(ctx, panel.name, panel.rect, below ? discY + VS_R + VS_GAP : -Infinity);
  statCard(ctx, panel.stat, ms, panel.place.colour,
           { x0: rx + PANEL_CARD_INSET, x1: rx + rw - PANEL_CARD_INSET, cy: ry + 0.8 * rh, scale: Math.min(1, rh / 960),
             maxY: above ? discY - VS_R - VS_GAP : ry + rh });
  ctx.restore();
}

// The VS disc, slammed onto the first boundary between panels.
function vsDisc(ctx, vs, y, ms) {
  const pop = popState(vs.atMs, ms);
  if (!pop.visible) return;
  ctx.save();
  ctx.globalAlpha = pop.alpha;
  if (pop.blur > 0.2) ctx.filter = `blur(${pop.blur.toFixed(1)}px)`;
  ctx.translate(W / 2, y);
  ctx.scale(pop.scale, pop.scale);
  ctx.beginPath();
  ctx.arc(0, 0, VS_R, 0, Math.PI * 2);
  ctx.shadowColor = "rgba(0,0,0,0.5)";
  ctx.shadowBlur = 24;
  ctx.fillStyle = "#111111";
  ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.lineWidth = 8;
  ctx.strokeStyle = "#ffffff";
  ctx.stroke();
  ctx.font = `900 80px ${FONT}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = "#ffffff";
  ctx.fillText("VS", 0, 4);
  ctx.restore();
}

function drawVersus(ctx, doc, images, seconds) {
  const ms = seconds * 1000;
  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, W, H);
  const discY = doc.vs && doc.panels.length > 1 ? doc.panels[1].rect[1] : null;
  doc.panels.forEach((panel, k) => drawPanel(ctx, doc, panel, k, ms, seconds, images, discY));
  ctx.fillStyle = "#ffffff";
  for (const panel of doc.panels.slice(1)) ctx.fillRect(0, panel.rect[1] - 3, W, 6);
  if (discY !== null) vsDisc(ctx, doc.vs, discY, ms);
  caption(ctx, doc.words, seconds);
}

export function drawFrame(ctx, doc, images, seconds) {
  if (doc.panels && doc.panels.length) return drawVersus(ctx, doc, images, seconds);
  const ms = seconds * 1000;
  const cam = cameraAt(doc.camera, ms);
  drawLayers(ctx, cam, doc.layers, images);
  const fadeOut = 1 - Math.min(1, ms / Math.max(1, doc.camera.flyMs * 0.8));
  // each at the share it was drawn at (a regions segment's faint country), all fading together
  for (const place of doc.previous || []) fillPlace(ctx, cam, place, fadeOut * (place.alpha ?? 1), seconds);
  // a regions segment's country is a faint backdrop under its states
  const faint = doc.countryFaint ? 0.25 : 1;
  for (const place of doc.places) fillPlace(ctx, cam, place, faint * fillAlpha(place.fillMs, ms), seconds);
  const regions = doc.regions || [];
  for (const region of regions) fillPlace(ctx, cam, region, fillAlpha(region.fillMs, ms), seconds);
  if (doc.places.length) borders(ctx, cam, doc.borders, fillAlpha(doc.places[0].fillMs, ms));
  // the ghost name is a country's; a regions segment leaves it out, so it never runs through the state tags
  if (doc.places.length === 1 && !doc.countryFaint) ghostName(ctx, cam, doc.places[0], fillAlpha(doc.places[0].fillMs, ms), seconds, doc.duration || 0);
  for (const region of regions) regionTag(ctx, cam, region, fillAlpha(region.fillMs, ms));
  for (const item of doc.items) drawItem(ctx, item, ms, images);
  legend(ctx, doc.legend);
  // the card sits right of the badge (cardLayout); the badge is drawn last, on top of any overhang
  if (doc.stat) statCard(ctx, doc.stat, ms, doc.places[0] && doc.places[0].colour);
  if (doc.rank) rankBadge(ctx, doc.rank, ms);
  caption(ctx, doc.words, seconds);
}
