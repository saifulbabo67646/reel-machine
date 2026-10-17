// reelmachine/recipes/mapreel/runtime/draw.mjs
// Draws one frame of a map segment on a 2D canvas.
import {
  cameraAt, fillAlpha, popState, activeWord, fitFontPx, badgeLines, ghostCentre, ringsBox, countText, clamp01, POP_MS, cardLayout,
} from "./pure.mjs";

const W = 1080, H = 1920;
const FONT = '"Arial Black", "Helvetica Neue", Arial, sans-serif';

function toScreen(cam, x, y) {
  return [(x - cam.x) * cam.s + W / 2, (y - cam.y) * cam.s + H / 2];
}

function tracePlace(ctx, cam, rings) {
  ctx.beginPath();
  for (const ring of rings) {
    ring.forEach(([x, y], i) => {
      const [px, py] = toScreen(cam, x, y);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    ctx.closePath();
  }
}

function drawLayers(ctx, cam, layers, images) {
  ctx.fillStyle = "#0b2236";
  ctx.fillRect(0, 0, W, H);
  for (const layer of layers) {
    const img = images.get(layer.url);
    if (!img) continue;
    const [x0, y0, x1, y1] = layer.bbox;
    for (const shift of [-1, 0, 1]) { // the world repeats east and west
      if (layer.bbox[2] - layer.bbox[0] < 0.999 && shift !== 0) continue;
      const [px0, py0] = toScreen(cam, x0 + shift, y0);
      const [px1, py1] = toScreen(cam, x1 + shift, y1);
      if (px1 < 0 || px0 > W) continue;
      ctx.drawImage(img, px0, py0, px1 - px0, py1 - py0);
    }
  }
}

const MARK_BELOW = 70, MARK_R = 46; // px: a place smaller than this on both sides gets a ring

function fillPlace(ctx, cam, place, alpha, seconds = 0) {
  if (alpha <= 0) return;
  const box = ringsBox(place.rings, cam);
  if (box.w < MARK_BELOW && box.h < MARK_BELOW) markPlace(ctx, box, place.colour, alpha, seconds);
  ctx.save();
  ctx.globalAlpha = 0.8 * alpha;
  tracePlace(ctx, cam, place.rings);
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
  ctx.globalAlpha = 0.7 * alpha;
  tracePlace(ctx, cam, place.rings);
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
    ctx.shadowColor = "rgba(255,255,255,0.75)";
    ctx.shadowBlur = 14;
    ctx.shadowOffsetY = 0;
    ctx.fillStyle = "#111111";
    const top = (item.emoji ? size * 0.25 : 0) - ((lines.length - 1) * size * 1.05) / 2;
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
  ctx.strokeText(w.text, W / 2, H * 0.71);
  ctx.fillStyle = w.hot ? "#ffe14a" : "#ffffff";
  ctx.fillText(w.text, W / 2, H * 0.71);
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
function statCard(ctx, stat, ms, colour) {
  const pop = popState(stat.atMs, ms);
  if (!pop.visible) return;
  ctx.save();
  ctx.font = `900 104px ${FONT}`;
  const valueW = ctx.measureText(stat.value).width; // measured at its final text, so the card never jitters
  ctx.font = `700 40px ${FONT}`;
  const labelW = stat.label ? ctx.measureText(stat.label).width : 0;
  const { cx, width, valuePx, labelPx } = cardLayout(valueW, labelW);
  const height = STAT_PAD * 2 + valuePx + (stat.label ? 12 + labelPx : 0);
  ctx.globalAlpha = pop.alpha;
  if (pop.blur > 0.2) ctx.filter = `blur(${pop.blur.toFixed(1)}px)`;
  ctx.translate(cx, STAT_TOP + height / 2);
  ctx.scale(pop.scale, pop.scale);
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

export function drawFrame(ctx, doc, images, seconds) {
  const ms = seconds * 1000;
  const cam = cameraAt(doc.camera, ms);
  drawLayers(ctx, cam, doc.layers, images);
  const fadeOut = 1 - Math.min(1, ms / Math.max(1, doc.camera.flyMs * 0.8));
  for (const place of doc.previous || []) fillPlace(ctx, cam, place, fadeOut, seconds);
  for (const place of doc.places) fillPlace(ctx, cam, place, fillAlpha(place.fillMs, ms), seconds);
  if (doc.places.length === 1) ghostName(ctx, cam, doc.places[0], fillAlpha(doc.places[0].fillMs, ms), seconds, doc.duration || 0);
  for (const item of doc.items) drawItem(ctx, item, ms, images);
  legend(ctx, doc.legend);
  // the card sits right of the badge (cardLayout); the badge is drawn last, on top of any overhang
  if (doc.stat) statCard(ctx, doc.stat, ms, doc.places[0] && doc.places[0].colour);
  if (doc.rank) rankBadge(ctx, doc.rank, ms);
  caption(ctx, doc.words, seconds);
}
