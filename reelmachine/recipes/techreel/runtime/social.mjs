// reelmachine/recipes/techreel/runtime/social.mjs
// The analogy and call-to-action visuals, same contract as components.mjs: build(v, doc) makes the DOM once at
// its final size (main.mjs measures it), update(node, v, t, doc) sets it for time t (ms) only. `flash` is an
// overlay (compose.py classes it): main.mjs lays it full-bleed over the stage, under the captions.
import { clamp01, backOut, typed, gridOf, litSet, emptySpot, nodeLayout, nodeLinks, seedOf, ENTER_MS, TYPE_CPS } from "./pure.mjs";
import { el, accentWords, wordSpans, slam } from "./components.mjs";

const lerp = (a, b, p) => a + (b - a) * p;
const easeOut = (p) => 1 - Math.pow(1 - p, 3);
const easeInOut = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
const SVG_NS = "http://www.w3.org/2000/svg";

const PEOPLE_W = 888, PEOPLE_CELL = 34, PERSON_TALL = 1.55; // a row is taller than a cell is wide: people stand
const FADE_ALL_MS = 600, PERSON_FADE_MS = 140, LIT_AFTER_MS = 150, LIT_SPAN_MS = 600, LIT_GAP_MS = 90, HOP_MS = 320;
const BADGE_AFTER_MS = 250, HOP = 0.34; // a lit person hops a third of its cell
const NODE_GAP_MS = 180, LINK_AFTER_MS = 150, LINK_MS = 300, FLOW_PULSE_MS = 1200;
const ECG_W = 880, ECG_H = 240, ECG_DRAW_MS = 900, ECG_SWEEP_MS = 1200, ECG_TRAIL = 140;
const FLASH_IN_MS = 150, FLASH_OUT_MS = 150;
const SEND_AFTER_MS = 200, PRESS_MS = 260, REPLY_AFTER_MS = 350;
export const TYPE_DELAY_MS = 300; // stages.py COMMENT_TYPE_DELAY_MS: "Add a comment…" shows this long, then the tick and the typing
export const FLIP_MS = 900; // stages.py FOLLOW_FLIP_MS: the ding plays on this flip
const BURST_MS = 520;

const SEND_SVG = `<svg viewBox="0 0 24 24"><path d="M12 19 V5.5 M6 11 L12 5 L18 11" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

function svgEl(tag, attrs, parent) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, value] of Object.entries(attrs || {})) node.setAttribute(k, String(value));
  if (parent) parent.appendChild(node);
  return node;
}

/** A part popping up into place: fades in, rises `rise` px and springs from `from`× to its size. */
function pop(node, p, rise, from) {
  node.style.opacity = clamp01(p * 2).toFixed(3);
  node.style.transform = `translateY(${(rise * (1 - easeOut(p))).toFixed(1)}px) scale(${Math.max(0, lerp(from, 1, backOut(p))).toFixed(4)})`;
}

/** A press: 1 → 1 - depth → 1 over PRESS_MS from `at`. */
const pressScale = (t, at, depth) => {
  const q = (t - at) / PRESS_MS;
  return 1 - depth * (q >= 0 && q < 1 ? Math.sin(Math.PI * q) : 0);
};

// ------------------------------------------------------------ people

/** The label's first number as it counts up: `{before, n, after}`, or null when it has none. */
function countParts(label) {
  const m = /^(\D*?)(\d+)(.*)$/su.exec(String(label || ""));
  return m ? { before: m[1], n: Number(m[2]), after: m[3] } : null;
}

const people = {
  build(v) {
    const node = el("div", "people");
    const total = v.total || 1;
    const g = gridOf(total, PEOPLE_W, PEOPLE_CELL);
    const rowH = g.size * PERSON_TALL;
    const art = svgEl("svg", { width: g.cols * g.size, height: g.rows * rowH, class: "pgrid" }, node);
    const lit = litSet(total, v.lit || 0, seedOf(String(v.id)));
    // a person in a 12 × 26 box: a small head over a slim, tall body with round shoulders
    const k = (g.size * 0.6) / 12;
    node._people = Array.from({ length: total }, (_, i) => {
      const x = (i % g.cols) * g.size + (g.size - 12 * k) / 2, y = Math.floor(i / g.cols) * rowH + (rowH - 26 * k) / 2;
      const gEl = svgEl("g", { class: lit.has(i) ? "pp lit" : "pp" }, art);
      const inner = svgEl("g", { transform: `scale(${k.toFixed(4)})` }, gEl);
      svgEl("circle", { cx: 6, cy: 3.6, r: 3.4 }, inner);
      svgEl("path", { d: "M1.2 26 V12.6 A4.8 4.8 0 0 1 6 7.8 A4.8 4.8 0 0 1 10.8 12.6 V26 Z" }, inner);
      return { g: gEl, x, y, lit: lit.has(i), i };
    });
    node._litOrder = node._people.filter((p) => p.lit);
    node._grid = { ...g, rowH, lit };
    node._count = countParts(v.label);
    // the number counts the people coming in, or the lit ones when it is their number
    node._countLit = node._count && node._count.n === (v.lit || 0) && v.lit !== total;
    node._label = v.label ? el("div", "plabel", node, v.label) : null;
    if (v.badge) {
      node._badge = el("div", "pbadge", node, v.badge);
    }
    return node;
  },
  fit(node, v) {
    if (node._label) node._label.style.minWidth = `${Math.ceil(node._label.scrollWidth)}px`;
    if (!node._badge) return;
    // over the dim people: the free spot nearest the middle, else (everyone lit round it) the grid's middle
    const g = node._grid;
    const w = Math.min(g.cols, Math.ceil(node._badge.offsetWidth / g.size) + 1);
    const h = Math.min(g.rows, Math.ceil(node._badge.offsetHeight / g.rowH));
    const spot = emptySpot(g.cols, g.rows, v.total, g.lit, w, h) || emptySpot(g.cols, g.rows, v.total, g.lit, Math.max(1, w - 1), 1);
    const cx = spot ? (spot.col + w / 2) * g.size : (g.cols * g.size) / 2;
    const cy = spot ? (spot.row + h / 2) * g.rowH : (g.rows * g.rowH) / 2;
    node._badge.style.left = `${(cx - node._badge.offsetWidth / 2).toFixed(1)}px`;
    node._badge.style.top = `${(cy - node._badge.offsetHeight / 2).toFixed(1)}px`;
  },
  update(node, v, t) {
    const total = node._people.length;
    const lits = node._litOrder;
    const litAt = v.enterMs + FADE_ALL_MS + LIT_AFTER_MS;
    const gap = lits.length ? Math.min(LIT_GAP_MS, LIT_SPAN_MS / lits.length) : 0;
    const badgeAt = litAt + Math.max(0, lits.length - 1) * gap + HOP_MS + BADGE_AFTER_MS;
    const badgeP = node._badge ? clamp01((t - badgeAt) / ENTER_MS) : 0;
    let shown = 0;
    // they come in reading order over FADE_ALL_MS, each fading in over PERSON_FADE_MS
    for (const p of node._people) {
      const start = v.enterMs + (total > 1 ? (p.i / (total - 1)) * (FADE_ALL_MS - PERSON_FADE_MS) : 0);
      const f = clamp01((t - start) / PERSON_FADE_MS);
      if (t >= start) shown++;
      let hop = 0, on = false;
      if (p.lit) {
        const at = litAt + lits.indexOf(p) * gap;
        const q = clamp01((t - at) / HOP_MS);
        on = t >= at;
        hop = q > 0 && q < 1 ? Math.sin(Math.PI * q) * HOP * node._grid.size : 0;
      }
      // the meeting badge sends the rest further into the dark
      const dim = p.lit && on ? 1 : lerp(0.28, 0.16, badgeP);
      p.g.classList.toggle("on", on);
      p.g.setAttribute("opacity", (f * dim).toFixed(3));
      p.g.setAttribute("transform", `translate(${p.x.toFixed(1)} ${(p.y - hop).toFixed(1)})`);
    }
    if (node._label && node._count) {
      const c = node._count;
      const frac = node._countLit
        ? (t < litAt ? 0 : lits.filter((p, k) => t >= litAt + k * gap).length / Math.max(1, lits.length))
        : shown / total;
      node._label.textContent = `${c.before}${Math.round(c.n * frac)}${c.after}`;
    }
    if (node._badge) {
      const bob = badgeP >= 1 ? 4 * Math.sin((2 * Math.PI * (t - badgeAt)) / 1600) : 0;
      node._badge.style.opacity = clamp01(badgeP * 2).toFixed(3);
      node._badge.style.transform = `translateY(${(bob + 16 * (1 - easeOut(badgeP))).toFixed(1)}px) scale(${Math.max(0, lerp(0.6, 1, backOut(badgeP))).toFixed(4)})`;
    }
  },
};

// ------------------------------------------------------------ nodes

const nodes = {
  build(v) {
    const items = v.items || [];
    const n = items.length;
    const layout = v.layout || "row";
    const line = layout === "row" || layout === "flow" || n < 3;
    const W = line ? 888 : 760;
    const spacing = W / Math.max(1, n);
    // in a row, about as much line as disc between two nodes, so the links (a flow's arrows) read
    const D = line ? Math.min(124, spacing * 0.52) : 132;
    // the centres sit in an area inset by a node's radius, so no circle crosses the box's edge
    const areaH = line ? D + 24 : 600;
    const labelRoom = 86;
    const node = el("div", "nodes");
    node.style.width = `${W}px`;
    node.style.height = `${areaH + labelRoom}px`;
    const art = svgEl("svg", { width: W, height: areaH + labelRoom, class: "nlinks" }, node);
    const pts = nodeLayout(n, layout).map((p) => ({ x: D / 2 + p.x * (W - D), y: D / 2 + p.y * (areaH - D) }));
    node._nodes = items.map((it, i) => {
      const box = el("div", "nnode", node);
      box.style.left = `${(pts[i].x - D / 2).toFixed(1)}px`;
      box.style.top = `${(pts[i].y - D / 2).toFixed(1)}px`;
      box.style.width = `${D.toFixed(1)}px`;
      const disc = el("div", "ndisc", box);
      disc.style.width = disc.style.height = `${D.toFixed(1)}px`;
      if (it.icon) {
        const icon = el("div", "nicon", disc, it.icon);
        icon.style.fontSize = `${Math.round(D * 0.42)}px`;
      } else el("div", "ncore", disc);
      const label = el("div", "nlabel", box, it.label);
      label.style.maxWidth = `${Math.min(240, Math.max(D + 20, spacing - 12)).toFixed(0)}px`;
      return box;
    });
    const R = D / 2 + 10;
    node._links = nodeLinks(n, layout).map(([a, b]) => {
      const [p, q] = [pts[a], pts[b]];
      const d = Math.hypot(q.x - p.x, q.y - p.y) || 1;
      const ux = (q.x - p.x) / d, uy = (q.y - p.y) / d;
      const tail = layout === "flow" ? 10 : 0; // room for the arrowhead before the next node
      const x1 = p.x + ux * R, y1 = p.y + uy * R, x2 = q.x - ux * (R + tail), y2 = q.y - uy * (R + tail);
      const len = Math.max(1, Math.hypot(x2 - x1, y2 - y1));
      const path = svgEl("line", { x1, y1, x2, y2, class: "nline", "stroke-dasharray": len, "stroke-dashoffset": len }, art);
      const link = { a, b, path, len, x1, y1, ux, uy };
      if (layout === "flow") {
        link.arrow = svgEl("path", { d: "M0 0 L-22 -13 L-22 13 Z", class: "narrow" }, art);
        link.angle = (Math.atan2(uy, ux) * 180) / Math.PI;
        link.dot = svgEl("circle", { r: 7, class: "ndot" }, art);
      }
      return link;
    });
    return node;
  },
  update(node, v, t) {
    const startOf = (i) => v.enterMs + i * NODE_GAP_MS;
    node._nodes.forEach((box, i) => {
      const p = clamp01((t - startOf(i)) / ENTER_MS);
      box.style.visibility = p > 0 ? "visible" : "hidden";
      box.style.opacity = clamp01(p * 2).toFixed(3);
      box.firstChild.style.transform = `scale(${Math.max(0, lerp(0.4, 1, backOut(p))).toFixed(4)})`;
    });
    for (const l of node._links) {
      // a line draws once both its ends are there
      const at = Math.max(startOf(l.a), startOf(l.b)) + LINK_AFTER_MS;
      const p = easeInOut(clamp01((t - at) / LINK_MS));
      l.path.setAttribute("stroke-dashoffset", (l.len * (1 - p)).toFixed(2));
      l.path.style.visibility = p > 0 ? "visible" : "hidden";
      if (!l.arrow) continue;
      const tipX = l.x1 + l.ux * (l.len * p + 10), tipY = l.y1 + l.uy * (l.len * p + 10);
      l.arrow.setAttribute("transform", `translate(${tipX.toFixed(1)} ${tipY.toFixed(1)}) rotate(${l.angle.toFixed(2)})`);
      l.arrow.style.visibility = p > 0 ? "visible" : "hidden";
      // once drawn, a pulse runs down the pipe again and again
      const run = t - at - LINK_MS;
      const f = run >= 0 ? (run % FLOW_PULSE_MS) / FLOW_PULSE_MS : -1;
      l.dot.style.visibility = f >= 0 ? "visible" : "hidden";
      if (f >= 0) {
        l.dot.setAttribute("cx", (l.x1 + l.ux * l.len * f).toFixed(1));
        l.dot.setAttribute("cy", (l.y1 + l.uy * l.len * f).toFixed(1));
        l.dot.setAttribute("opacity", Math.sin(Math.PI * f).toFixed(3));
      }
    }
  },
};

// ------------------------------------------------------------ heartbeat

// one beat as (dx, dy): dx a fraction of the beat's width round its middle, dy of the line's height (up is -)
const BEAT = [[-0.5, 0], [-0.24, 0], [-0.19, -0.07], [-0.14, 0], [-0.06, 0], [-0.03, 0.1], [0.02, -0.62],
  [0.07, 0.24], [0.11, 0], [0.2, 0], [0.27, -0.14], [0.35, 0], [0.5, 0]];

function ecgPoints() {
  const beats = 3, bw = ECG_W / beats, base = ECG_H * 0.62;
  const pts = [];
  for (let k = 0; k < beats; k++) {
    for (const [dx, dy] of BEAT) {
      const x = (k + 0.5) * bw + dx * bw, y = base + dy * ECG_H;
      const last = pts[pts.length - 1];
      if (!last || Math.abs(last[0] - x) > 1e-6 || Math.abs(last[1] - y) > 1e-6) pts.push([x, y]);
    }
  }
  return pts;
}

/** The point `d` px along the polyline (`cum` its running lengths). */
function along(pts, cum, d) {
  let i = 1;
  while (i < pts.length - 1 && cum[i] < d) i++;
  const seg = cum[i] - cum[i - 1] || 1;
  const p = clamp01((d - cum[i - 1]) / seg);
  return [lerp(pts[i - 1][0], pts[i][0], p), lerp(pts[i - 1][1], pts[i][1], p)];
}

const heartbeat = {
  build(v) {
    const node = el("div", "heartbeat");
    const art = svgEl("svg", { width: ECG_W, height: ECG_H, class: "ecg" }, node);
    // the monitor's paper: a faint grid
    for (let x = 40; x < ECG_W; x += 40) svgEl("line", { x1: x, y1: 0, x2: x, y2: ECG_H, class: "egrid" }, art);
    for (let y = 40; y < ECG_H; y += 40) svgEl("line", { x1: 0, y1: y, x2: ECG_W, y2: y, class: "egrid" }, art);
    const pts = ecgPoints();
    const cum = [0];
    for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    const len = cum[cum.length - 1];
    const d = pts.map((p) => p.map((n) => n.toFixed(1)).join(",")).join(" ");
    node._line = svgEl("polyline", { points: d, class: "eline", "stroke-dasharray": len, "stroke-dashoffset": len }, art);
    node._trail = svgEl("polyline", { points: d, class: "etrail", "stroke-dasharray": `${ECG_TRAIL} ${len + ECG_TRAIL}` }, art);
    node._dot = svgEl("circle", { r: 9, class: "edot" }, art);
    Object.assign(node, { _pts: pts, _cum: cum, _len: len });
    if (v.label) {
      const label = el("div", "elabel", node);
      node._pulse = el("i", null, label);
      label.appendChild(document.createTextNode(v.label));
    }
    return node;
  },
  update(node, v, t) {
    const p = clamp01((t - v.enterMs) / ECG_DRAW_MS);
    const len = node._len;
    node._line.setAttribute("stroke-dashoffset", (len * (1 - p)).toFixed(2));
    // the pen while drawing, then a bright blip sweeping the trace every ECG_SWEEP_MS
    const sweep = t - v.enterMs - ECG_DRAW_MS;
    const at = sweep < 0 ? p * len : ((sweep % ECG_SWEEP_MS) / ECG_SWEEP_MS) * len;
    const [x, y] = along(node._pts, node._cum, at);
    node._dot.setAttribute("cx", x.toFixed(1));
    node._dot.setAttribute("cy", y.toFixed(1));
    node._dot.style.visibility = t >= v.enterMs ? "visible" : "hidden";
    node._trail.setAttribute("stroke-dashoffset", (ECG_TRAIL - at).toFixed(2));
    node._trail.style.visibility = sweep >= 0 ? "visible" : "hidden";
    if (node._pulse) {
      // the label's dot beats as the blip crosses each spike
      const beat = sweep >= 0 ? Math.max(0, Math.sin(Math.PI * 3 * ((sweep % ECG_SWEEP_MS) / ECG_SWEEP_MS))) : 0;
      node._pulse.style.transform = `scale(${(0.8 + 0.5 * Math.pow(beat, 6)).toFixed(3)})`;
    }
  },
};

// ------------------------------------------------------------ flash (overlay)

const flash = {
  ownEnter: true,
  build(v) {
    const node = el("div", "flashv");
    const text = el("div", "ftext", node);
    node._words = wordSpans(text, accentWords(v.text));
    node._text = text;
    return node;
  },
  fit(node) {
    let px = parseFloat(getComputedStyle(node._text).fontSize);
    const widest = () => Math.max(...node._words.map((w) => w.getBoundingClientRect().width));
    while ((widest() > 952 || node._text.offsetHeight > 820) && px > 48) {
      px -= 6;
      node._text.style.fontSize = `${px}px`;
    }
  },
  /** Wipes in from the left over FLASH_IN_MS, holds, and fades out over the FLASH_OUT_MS before its exit. */
  update(node, v, t) {
    const p = easeOut(clamp01((t - v.enterMs) / FLASH_IN_MS));
    const out = v.exitMs == null ? 1 : clamp01((v.exitMs - t) / FLASH_OUT_MS);
    node.style.clipPath = p < 1 ? `inset(0 ${(100 * (1 - p)).toFixed(2)}% 0 0)` : "none";
    node.style.opacity = out.toFixed(3);
    node._words.forEach((w, i) => slam(w, t, v.enterMs + 60 + i * 60));
  },
};

// ------------------------------------------------------------ comment

const STARTS_WITH_EMOJI = /^\p{Extended_Pictographic}/u;

/** When the comment's parts happen: the keyword types from TYPE_DELAY_MS after the enter, the send arrow is
    pressed, the reply lands. */
function commentTimes(v) {
  const typeAt = v.enterMs + TYPE_DELAY_MS;
  const typedAt = typeAt + ([...(v.keyword || "")].length * 1000) / TYPE_CPS;
  const sendAt = typedAt + SEND_AFTER_MS;
  return { typeAt, typedAt, sendAt, replyAt: sendAt + REPLY_AFTER_MS };
}

const comment = {
  build(v) {
    const node = el("div", "comment");
    const box = el("div", "cmbox", node);
    el("div", "cavatar", box);
    const field = el("div", "cfield", box);
    // the caret sits before the placeholder while the field is empty, as a real one does
    node._typed = el("span", "ctyped", field, "");
    node._caret = el("span", "ccaret", field);
    node._place = el("span", "cplace", field, "Add a comment…");
    node._send = el("div", "csend", box);
    node._send.innerHTML = SEND_SVG;
    node._box = box;
    if (v.reply) {
      const text = STARTS_WITH_EMOJI.test(v.reply) ? v.reply : `📩 ${v.reply}`;
      node._reply = el("div", "creply", node, text);
    }
    return node;
  },
  update(node, v, t) {
    const { typeAt, typedAt, sendAt, replyAt } = commentTimes(v);
    const text = typed(v.keyword, typeAt, t);
    node._typed.textContent = text;
    node._place.style.display = text ? "none" : "inline";
    const blink = Math.floor(t / 400) % 2 === 0;
    node._caret.style.visibility = t < typedAt || blink ? "visible" : "hidden";
    // the field lights up while it is being typed into, the arrow is pressed once the word is in
    node._box.classList.toggle("active", t >= typeAt);
    node._send.classList.toggle("on", t >= typedAt);
    node._send.style.transform = `scale(${pressScale(t, sendAt, 0.2).toFixed(4)})`;
    if (node._reply) {
      const p = clamp01((t - replyAt) / ENTER_MS);
      node._reply.style.visibility = p > 0 ? "visible" : "hidden";
      pop(node._reply, p, 18, 0.6);
    }
  },
};

// ------------------------------------------------------------ follow

const follow = {
  build(v, doc) {
    const creator = doc && doc.creator;
    // no creator, nothing to follow: a block of no size (lint warns about it)
    if (!creator) return el("div", "follow none");
    const node = el("div", "card follow");
    const top = el("div", "ftop", node);
    const avatar = el("div", "favatar", top);
    if (creator.avatar) {
      const img = el("img", null, avatar);
      img.alt = "";
      img.src = creator.avatar;
      node._ready = (img.decode ? img.decode() : Promise.resolve()).catch(() => null);
    } else {
      avatar.classList.add("initial");
      avatar.textContent = [...String(creator.name || "?").trim()][0]?.toUpperCase() || "?";
    }
    const who = el("div", "fwho", top);
    el("div", "fname", who, creator.name);
    el("div", "fhandle", who, creator.handle);
    const button = el("div", "fbutton", node);
    node._burst = el("div", "fburst", button);
    node._off = el("span", "flab", button, "Follow");
    node._on = el("span", "flab", button, "Following ✓");
    node._button = button;
    return node;
  },
  update(node, v, t) {
    if (!node._button) return;
    const flip = v.enterMs + FLIP_MS;
    const on = t >= flip;
    node._button.classList.toggle("on", on);
    node._off.style.visibility = on ? "hidden" : "visible";
    node._on.style.visibility = on ? "visible" : "hidden";
    // pressed down just before the flip, back up just after it
    node._button.style.transform = `scale(${pressScale(t, flip - PRESS_MS / 2, 0.08).toFixed(4)})`;
    const b = clamp01((t - flip) / BURST_MS);
    node._burst.style.opacity = on && b < 1 ? (0.9 * (1 - b)).toFixed(3) : "0";
    node._burst.style.transform = `scale(${lerp(1, 1.3, easeOut(b)).toFixed(4)})`;
    if (on) pop(node._on, clamp01((t - flip) / ENTER_MS), 0, 0.7);
  },
};

export const SOCIAL = { people, nodes, heartbeat, flash, comment, follow };
