// reelmachine/recipes/techreel/runtime/components.mjs
// One builder per stage visual: build(v, doc) makes its DOM once, update(node, v, t, doc) sets it for time t
// (ms). `fit` (optional) sizes it once the fonts are in, before main.mjs measures it; `ownEnter` means the
// component animates its own entrance, so main.mjs only applies the generic exit.
import { clamp01, backOut, counterValue, typed, terminalStarts, accentUnits, accentPieces, isUnspaced, ENTER_MS } from "./pure.mjs";

const lerp = (a, b, p) => a + (b - a) * p;
const easeOut = (p) => 1 - Math.pow(1 - p, 3);
const easeInOut = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
const MAX_W = 952;
const CURSOR_MS = 350, PRESS_MS = 260, CHIP_STAGGER_MS = 120, WORD_STAGGER_MS = 60;
const WOBBLE_MS = 700, ROW_STAGGER_MS = 220;
export const STAMP_FROM = 1.8; // a stamp slams down from this scale (main.mjs may start it lower to stay in the stage)
export const STAMP_TILT = 9; // the most a stamp's slam turns it past its own rotation (stamp.update: -9° → 4°)

export function el(tag, className, parent, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  if (parent) parent.appendChild(node);
  return node;
}

/** `*accent*` text as words (the heading and the caption come pre-split from compose.py). */
export const accentWords = accentUnits;

/** A word's text into `span`: a partly accented word gets its accented runs as inner accent spans. */
export function fillWord(span, w) {
  if (!w.accentSpans || !w.accentSpans.length) {
    span.textContent = w.t;
    return span;
  }
  for (const piece of accentPieces(w)) {
    if (piece.a) el("span", "accent", span, piece.t);
    else span.appendChild(document.createTextNode(piece.t));
  }
  return span;
}

/** Word spans of class `cls` inside `parent`, a space between them; none in an unspaced script
    (`doc.language`), whose units are characters. */
export function wordSpans(parent, words, doc, cls = "word") {
  const gap = isUnspaced(doc?.language) ? "" : " ";
  return words.map((w, i) => {
    const span = fillWord(el("span", w.a ? `${cls} accent` : cls, parent), w);
    if (gap && i < words.length - 1) parent.appendChild(document.createTextNode(gap));
    return span;
  });
}

/** A word slamming down: big, blurred and clear → its size, from `start` over ENTER_MS. */
export function slam(span, t, start) {
  const p = clamp01((t - start) / ENTER_MS);
  const s = p <= 0 ? 1.6 : lerp(1.6, 1, backOut(p));
  span.style.opacity = clamp01(p * 2.5).toFixed(3);
  span.style.transform = `translateY(${(-18 * (1 - easeOut(p))).toFixed(1)}px) scale(${s.toFixed(4)})`;
  span.style.filter = p < 1 ? `blur(${(10 * (1 - easeOut(p))).toFixed(2)}px)` : "none";
}

/** Shrink `node`'s font until it is no wider than `room` (one line) — for big numbers and single words. */
function fitWidth(node, room, minPx = 40) {
  let px = parseFloat(getComputedStyle(node).fontSize);
  while (node.scrollWidth > room && px > minPx) {
    px -= 4;
    node.style.fontSize = `${px}px`;
  }
}

// ------------------------------------------------------------ title

const title = {
  ownEnter: true,
  build(v, doc) {
    const node = el("div", `title ${v.size || "l"}`);
    node._words = wordSpans(node, accentWords(v.text), doc);
    return node;
  },
  fit(node) {
    // the longest word must fit on a line; lines wrap between words
    let px = parseFloat(getComputedStyle(node).fontSize);
    const widest = () => Math.max(...node._words.map((w) => w.getBoundingClientRect().width));
    while (widest() > MAX_W && px > 40) {
      px -= 4;
      node.style.fontSize = `${px}px`;
    }
  },
  update(node, v, t) {
    node._words.forEach((w, i) => slam(w, t, v.enterMs + i * WORD_STAGGER_MS));
  },
};

// ------------------------------------------------------------ counter

const counter = {
  build(v) {
    const node = el("div", "counter");
    node._num = el("div", "num", node, counterValue(v, Infinity));
    if (v.label) el("div", "label", node, v.label);
    return node;
  },
  fit(node, v) {
    node._num.textContent = counterValue(v, Infinity);
    fitWidth(node._num, MAX_W);
    // held at its final width, so the number does not shake the layout as it rolls
    node._num.style.minWidth = `${Math.ceil(node._num.scrollWidth)}px`;
  },
  update(node, v, t) {
    node._num.textContent = counterValue(v, t);
  },
};

// ------------------------------------------------------------ stamp (overlay)

const stamp = {
  ownEnter: true,
  build(v) {
    const node = el("div", `stamp ${v.tone || "red"}`, null, v.text);
    return node;
  },
  /** A stamp wider than the stage (measured as main.mjs measures it: tilted by its rotation plus the slam's
      overshoot) shrinks its font until it fits. */
  fit(node, v) {
    const a = ((Math.abs(v.rotate ?? -8) + STAMP_TILT) * Math.PI) / 180;
    const tilted = () => node.offsetWidth * Math.cos(a) + node.offsetHeight * Math.sin(a);
    let px = parseFloat(getComputedStyle(node).fontSize);
    while (tilted() > MAX_W && px > 32) {
      px -= 4;
      node.style.fontSize = `${px}px`;
    }
  },
  /** Slams from STAMP_FROM× (or `node._from`, set by main.mjs) with the rotation overshooting, then settles.
      Main positions and rotates the wrapper. */
  update(node, v, t) {
    const p = clamp01((t - v.enterMs) / ENTER_MS);
    const settle = clamp01((t - v.enterMs - ENTER_MS) / 220);
    // down fast to a touch under its size, then back up to it
    const s = p < 1 ? lerp(node._from ?? STAMP_FROM, 0.94, p * p) : lerp(0.94, 1, easeOut(settle));
    const r = p < 1 ? lerp(-9, 4, p) : lerp(4, 0, easeOut(settle));
    node.style.opacity = clamp01(p * 3).toFixed(3);
    node.style.transform = `rotate(${r.toFixed(2)}deg) scale(${s.toFixed(4)})`;
  },
};

// ------------------------------------------------------------ emoji (overlay)

const emoji = {
  ownEnter: true,
  build(v) {
    return el("div", "emoji", null, v.emoji);
  },
  /** Pops (back-out) and wobbles two cycles, fading the wobble out. */
  update(node, v, t) {
    const p = clamp01((t - v.enterMs) / ENTER_MS);
    const q = clamp01((t - v.enterMs) / WOBBLE_MS);
    const wobble = 14 * Math.sin(2 * Math.PI * 2 * q) * (1 - q);
    node.style.opacity = clamp01(p * 3).toFixed(3);
    node.style.transform = `rotate(${wobble.toFixed(2)}deg) scale(${Math.max(0, backOut(p)).toFixed(4)})`;
  },
};

// ------------------------------------------------------------ chips

const chips = {
  ownEnter: true,
  build(v) {
    const node = el("div", `chips ${v.style || "outline"}`);
    node._chips = (v.items || []).map((text) => el("div", "chip", node, text));
    return node;
  },
  update(node, v, t) {
    node._chips.forEach((c, i) => {
      const p = clamp01((t - v.enterMs - i * CHIP_STAGGER_MS) / ENTER_MS);
      c.style.opacity = clamp01(p * 2).toFixed(3);
      c.style.transform = `translateY(${(30 * (1 - easeOut(p))).toFixed(1)}px) scale(${lerp(0.6, 1, backOut(p)).toFixed(4)})`;
    });
  },
};

// ------------------------------------------------------------ window

const CURSOR_SVG = `<svg viewBox="0 0 24 24" width="54" height="54"><path d="M4 2.5 L4 19.5 L8.6 15.4 L11.6 22 L14.6 20.6 L11.7 14.2 L18 14 Z" fill="#fff" stroke="#0b0b0c" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
const CURSOR_HOME = [0.88, 0.78]; // where the arrow rests before its first move

const windowViz = {
  build(v) {
    const node = el("div", "card window");
    const top = el("div", "top", node);
    const dots = el("div", "bar3", top);
    for (let i = 0; i < 3; i++) el("i", "dot3", dots);
    el("div", "wtitle", top, v.title);
    node._live = v.live ? el("div", "live", top) : null;
    if (node._live) {
      el("i", null, node._live);
      node._live.appendChild(document.createTextNode("LIVE"));
    }
    if ((v.tabs || []).length) {
      const tabs = el("div", "tabs", node);
      v.tabs.forEach((name, i) => el("div", i === (v.active || 0) ? "tab on" : "tab", tabs, name));
    }
    // the body: the given chat/list rows ("> " marks the user's), else three placeholder bars
    node._rows = (v.rows || []).map((text) => {
      const mine = text.startsWith(">");
      return el("div", mine ? "wrow me" : "wrow", node, mine ? text.replace(/^>\s*/, "") : text);
    });
    if (!node._rows.length) for (const k of ["a", "b", "c"]) el("div", `sk ${k}`, node);
    node._button = v.button ? el("div", "wbutton", node, v.button) : null;
    node._cursor = el("div", "cursor", node);
    node._cursor.innerHTML = CURSOR_SVG;
    return node;
  },
  fit(node) {
    node._w = node.offsetWidth;
    node._h = node.offsetHeight;
    const b = node._button;
    node._buttonBox = b ? { x0: b.offsetLeft, y0: b.offsetTop, x1: b.offsetLeft + b.offsetWidth, y1: b.offsetTop + b.offsetHeight } : null;
  },
  update(node, v, t) {
    // the rows pop in one after another once the window has landed
    node._rows.forEach((row, i) => {
      const p = clamp01((t - v.enterMs - ENTER_MS - i * ROW_STAGGER_MS) / ENTER_MS);
      row.style.opacity = clamp01(p * 2).toFixed(3);
      row.style.transform = `translateY(${(16 * (1 - easeOut(p))).toFixed(1)}px)`;
    });
    if (node._live) {
      const pulse = 0.5 + 0.5 * Math.sin((2 * Math.PI * t) / 1100);
      node._live.style.opacity = (0.65 + 0.35 * pulse).toFixed(3);
      node._live.firstChild.style.transform = `scale(${(0.8 + 0.35 * pulse).toFixed(3)})`;
    }
    // the arrow eases from where it was to each move's target over CURSOR_MS
    let at = CURSOR_HOME, pressAt = null;
    for (const m of v.cursor || []) {
      if (t < m.atMs) break;
      const p = easeInOut(clamp01((t - m.atMs) / CURSOR_MS));
      at = [lerp(at[0], m.to[0], p), lerp(at[1], m.to[1], p)];
      const x = m.to[0] * node._w, y = m.to[1] * node._h, box = node._buttonBox;
      if (box && x >= box.x0 && x <= box.x1 && y >= box.y0 && y <= box.y1) pressAt = m.atMs + CURSOR_MS;
    }
    // the arrow's tip is its top-left (4, 2.5 of 24)
    node._cursor.style.transform = `translate(${(at[0] * node._w - 9).toFixed(1)}px, ${(at[1] * node._h - 6).toFixed(1)}px)`;
    if (node._button) {
      const q = pressAt == null ? -1 : (t - pressAt) / PRESS_MS;
      const down = q >= 0 && q < 1 ? Math.sin(Math.PI * q) : 0;
      node._button.style.transform = `scale(${(1 - 0.1 * down).toFixed(4)})`;
      node._button.classList.toggle("pressed", q >= 0.5);
    }
  },
};

// ------------------------------------------------------------ terminal

const terminal = {
  build(v) {
    const node = el("div", "card terminal");
    const top = el("div", "top", node);
    const dots = el("div", "bar3", top);
    for (let i = 0; i < 3; i++) el("i", "dot3", dots);
    el("div", "ttitle", top, "zsh");
    node._lines = (v.lines || []).map((line) => {
      if (line.cmd != null) {
        const row = el("div", "tline cmd", node);
        el("span", "ps", row, "$ ");
        const text = el("span", null, row, line.cmd);
        const caret = el("span", "caret", row);
        return { row, text, caret, cmd: line.cmd };
      }
      return { row: el("div", `tline ${line.tone || "dim"}`, node, line.out) };
    });
    return node;
  },
  update(node, v, t) {
    const starts = terminalStarts(v);
    // the caret blinks on the line being typed (or the last command, once all are typed)
    const lastCmd = node._lines.map((l, i) => (l.cmd != null && starts[i] <= t ? i : -1)).reduce((a, b) => Math.max(a, b), -1);
    node._lines.forEach((l, i) => {
      // every line keeps its space (measured full), lines yet to come are just not shown
      const shown = t >= starts[i];
      l.row.style.visibility = shown ? "visible" : "hidden";
      if (l.cmd == null) return;
      l.text.textContent = shown ? typed(l.cmd, starts[i], t) : l.cmd;
      const blink = Math.floor(t / 400) % 2 === 0;
      const typing = shown && typed(l.cmd, starts[i], t).length < [...l.cmd].length;
      l.caret.style.visibility = i === lastCmd && (typing || blink) ? "visible" : "hidden";
    });
  },
};

// ------------------------------------------------------------ repo

const BOOK_SVG = `<svg viewBox="0 0 16 16" width="38" height="38" fill="currentColor"><path d="M2 2.5A2.5 2.5 0 0 1 4.5 0h8.75a.75.75 0 0 1 .75.75v12.5a.75.75 0 0 1-.75.75h-2.5a.75.75 0 0 1 0-1.5h1.75v-2h-8a1 1 0 0 0-.714 1.7.75.75 0 1 1-1.072 1.05A2.495 2.495 0 0 1 2 11.5Zm10.5-1h-8a1 1 0 0 0-1 1v6.708A2.486 2.486 0 0 1 4.5 9h8ZM5 12.25a.25.25 0 0 1 .25-.25h3.5a.25.25 0 0 1 .25.25v3.25a.25.25 0 0 1-.4.2l-1.45-1.087a.249.249 0 0 0-.3 0L5.4 15.7a.25.25 0 0 1-.4-.2Z"/></svg>`;

const repo = {
  build(v) {
    const node = el("div", "card repo");
    const head = el("div", "rhead", node);
    const book = el("span", null, head);
    book.innerHTML = BOOK_SVG;
    book.style.color = "var(--muted)";
    const name = el("div", "rname", head);
    const [owner, repoName] = String(v.name || "").includes("/") ? v.name.split("/", 2) : ["", v.name];
    if (owner) name.appendChild(document.createTextNode(`${owner} / `));
    el("b", null, name, repoName);
    el("div", "pub", head, "Public");
    if (v.description) el("div", "rdesc", node, v.description);
    const stars = el("div", "rstars", node);
    el("span", "star", stars, "⭐");
    node._stars = el("span", "n", stars, "0");
    el("span", "u", stars, "STARS");
    const pills = el("div", "pills", node);
    if (v.license) el("div", "pill", pills, `⚖️ ${v.license}`);
    if (v.language) {
      const lang = el("div", "pill", pills);
      el("i", null, lang);
      lang.appendChild(document.createTextNode(v.language));
    }
    return node;
  },
  fit(node, v) {
    node._stars.textContent = counterValue({ from: 0, to: v.stars || 0, enterMs: 0 }, Infinity);
    node._stars.style.minWidth = `${Math.ceil(node._stars.scrollWidth)}px`;
  },
  update(node, v, t) {
    // the stars start rolling once the card has landed
    node._stars.textContent = counterValue({ from: 0, to: v.stars || 0, enterMs: v.enterMs + ENTER_MS }, t);
  },
};

export const COMPONENTS = { title, counter, stamp, emoji, chips, window: windowViz, terminal, repo };
