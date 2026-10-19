// reelmachine/recipes/techreel/runtime/cards.mjs
// The catalogue's card visuals, same contract as components.mjs: build(v, doc) makes the DOM once (every part
// laid out at its final size, so main.mjs measures the whole card), update(node, v, t, doc) sets it for time t
// (ms) only. `error` is an overlay (compose.py classes it); it lives here because it is drawn like a card.
import { clamp01, backOut, counterValue, numberParts, staggerP, winner, seedOf, COUNTER_MS, ENTER_MS } from "./pure.mjs";
import { el, slam } from "./components.mjs";

const lerp = (a, b, p) => a + (b - a) * p;
const easeOut = (p) => 1 - Math.pow(1 - p, 3);
const easeInOut = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
const ROW_GAP_MS = 180, RULE_MS = 250;
const MSG_GAP_MS = 450, TYPING_MS = 300, POP_MS = 220;
const PRESS_MS = 260, CURSOR_MS = 400, CLICK_FRAC = 0.7;
const SHAKE_MS = 300, SHAKE_PX = 14;
const TICKET_GAP_MS = 260, TICKET_OVERLAP = 0.4, TICKET_TILT = 3;
const ITEM_GAP_MS = 200, TICK_AFTER_MS = 250;
const VS_CARD_GAP_MS = 140, VS_GLOW_AT_MS = 650, VS_GLOW_MS = 300;
const BAR_MS = 600, BAR_GAP_MS = 120;

const CHECK_SVG = `<svg viewBox="0 0 24 24"><path d="M5 12.5 L10 17.5 L19 7" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const CROSS_SVG = `<svg viewBox="0 0 24 24"><path d="M6.5 6.5 L17.5 17.5 M17.5 6.5 L6.5 17.5" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round"/></svg>`;
const CURSOR_SVG = `<svg viewBox="0 0 24 24" width="54" height="54"><path d="M4 2.5 L4 19.5 L8.6 15.4 L11.6 22 L14.6 20.6 L11.7 14.2 L18 14 Z" fill="#fff" stroke="#0b0b0c" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
const SHIELD_SVG = `<svg viewBox="0 0 24 24"><path d="M12 2.5 L20 5.6 V11.4 C20 16.3 16.6 20 12 21.5 C7.4 20 4 16.3 4 11.4 V5.6 Z" fill="currentColor" opacity="0.22"/><path d="M12 2.5 L20 5.6 V11.4 C20 16.3 16.6 20 12 21.5 C7.4 20 4 16.3 4 11.4 V5.6 Z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M8.6 12 L11 14.4 L15.6 9.6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const WARN_SVG = `<svg viewBox="0 0 48 44"><path d="M24 3 L45.5 40.5 H2.5 Z" fill="#f5b400" stroke="#c98a00" stroke-width="2.5" stroke-linejoin="round"/><rect x="22" y="15" width="4.2" height="14" rx="2.1" fill="#1b1b1d"/><circle cx="24.1" cy="34" r="2.5" fill="#1b1b1d"/></svg>`;

const svg = (parent, className, markup) => {
  const node = el("span", className, parent);
  node.innerHTML = markup;
  return node;
};

/** A part popping up into place: fades in, rises `rise` px and springs from `from`× to its size. */
function pop(node, p, rise, from) {
  node.style.opacity = clamp01(p * 2).toFixed(3);
  node.style.transform = `translateY(${(rise * (1 - easeOut(p))).toFixed(1)}px) scale(${lerp(from, 1, backOut(p)).toFixed(4)})`;
}

/** Shrink `node`'s font until it is no wider than `room` (one line). */
function fitWidth(node, room, minPx = 40) {
  let px = parseFloat(getComputedStyle(node).fontSize);
  while (node.scrollWidth > room && px > minPx) {
    px -= 4;
    node.style.fontSize = `${px}px`;
  }
}

// ------------------------------------------------------------ equation

const equation = {
  ownEnter: true,
  build(v) {
    const node = el("div", "equation");
    node._rows = (v.rows || []).map((text) => el("div", "erow", node, text));
    node._rule = el("div", "erule", node);
    const result = el("div", "eresult", node);
    const text = String(v.result ?? "");
    // the "=" is drawn unless the result already brings its own
    if (!/^\s*=/.test(text)) el("span", "eq", result, "= ");
    node._value = el("span", "eval", result, text);
    node._result = result;
    node._parts = numberParts(text);
    return node;
  },
  fit(node) {
    for (const row of node._rows) fitWidth(row, 880);
    fitWidth(node._result, 880, 60);
    // held at its final width, so a rolling number does not shake the layout
    node._value.style.minWidth = `${Math.ceil(node._value.scrollWidth)}px`;
  },
  update(node, v, t) {
    node._rows.forEach((row, i) => {
      const p = staggerP(i, v.enterMs, t, ROW_GAP_MS, ENTER_MS);
      row.style.opacity = clamp01(p * 2).toFixed(3);
      row.style.transform = `translateX(${(-40 * (1 - easeOut(p))).toFixed(1)}px)`;
    });
    // the rule draws once the last row is in, then the result lands
    const ruleAt = v.enterMs + node._rows.length * ROW_GAP_MS;
    node._rule.style.transform = `scaleX(${easeInOut(clamp01((t - ruleAt) / RULE_MS)).toFixed(4)})`;
    const resultAt = ruleAt + RULE_MS;
    slam(node._result, t, resultAt);
    if (node._parts) node._value.textContent = counterValue({ ...node._parts, enterMs: resultAt }, t);
  },
};

// ------------------------------------------------------------ chat

/** A person's avatar colour: one of the look's glows (bots wear the accent). */
const avatarTone = (name) => `var(--glow${1 + (seedOf(String(name)) % 3)})`;

const chat = {
  build(v) {
    const node = el("div", "card chat");
    const head = el("div", "chead", node);
    const channel = String(v.channel || "").replace(/^#\s*/, "");
    el("span", "hash", head, channel ? "#" : "");
    el("span", "cname", head, channel || "thread");
    node._msgs = (v.messages || []).map((m) => {
      const row = el("div", m.bot ? "msg bot" : "msg", node);
      const avatar = el("div", "avatar", row, m.bot ? "" : [...String(m.from || "?").trim()][0]?.toUpperCase() || "?");
      if (!m.bot) avatar.style.background = avatarTone(m.from);
      const body = el("div", "mbody", row);
      const who = el("div", "who", body);
      el("span", null, who, m.from);
      if (m.bot) el("span", "botchip", who, "BOT");
      const text = el("div", "mtext", body);
      // @mentions in the accent, as Slack draws them
      for (const part of String(m.text || "").split(/(@[\p{L}\p{N}_.-]+)/u)) {
        if (part) el("span", part.startsWith("@") ? "mention" : null, text, part);
      }
      const typing = el("div", "typing", row);
      const dots = [0, 1, 2].map(() => el("i", null, typing));
      return { row, avatar, body, typing, dots };
    });
    return node;
  },
  update(node, v, t) {
    node._msgs.forEach((m, i) => {
      // message i lands at enter + 300 + i·450, with typing dots for the 300 ms before it
      const at = v.enterMs + TYPING_MS + i * MSG_GAP_MS;
      const typing = t >= at - TYPING_MS && t < at;
      const shown = t >= at;
      m.row.style.visibility = typing || shown ? "visible" : "hidden";
      m.typing.style.display = typing ? "flex" : "none";
      m.body.style.visibility = shown ? "visible" : "hidden";
      if (typing) {
        m.avatar.style.opacity = "1";
        m.avatar.style.transform = "none";
        m.dots.forEach((d, k) => {
          const bounce = Math.max(0, Math.sin((2 * Math.PI * (t - k * 110)) / 600));
          d.style.transform = `translateY(${(-7 * bounce).toFixed(1)}px)`;
          d.style.opacity = (0.45 + 0.55 * bounce).toFixed(3);
        });
      }
      if (shown) {
        const p = clamp01((t - at) / POP_MS);
        pop(m.body, p, 14, 0.94);
        m.avatar.style.opacity = "1";
        m.avatar.style.transform = "none";
      }
    });
  },
};

// ------------------------------------------------------------ dialog

/** When the dialog's button is pressed: its cue, else 70 % through its life when a button is chosen. */
const clickAt = (v) => (v.clickAtMs != null ? v.clickAtMs
  : v.click ? v.enterMs + CLICK_FRAC * ((v.exitMs ?? v.enterMs + 2000) - v.enterMs) : null);

const dialog = {
  build(v) {
    const node = el("div", "card dialog");
    const top = el("div", "dtop", node);
    svg(top, "dicon", SHIELD_SVG);
    el("div", "dtitle", top, v.title);
    if (v.body) el("div", "dbody", node, v.body);
    const buttons = el("div", "dbuttons", node);
    const button = (cls, label) => {
      const b = el("div", `dbtn ${cls}`, buttons);
      b._mark = svg(b, "mark", cls === "approve" ? CHECK_SVG : CROSS_SVG);
      el("span", null, b, label);
      return b;
    };
    node._approve = button("approve", v.approve ?? "Approve");
    node._decline = button("decline", v.decline ?? "Decline");
    node._cursor = el("div", "cursor", node);
    node._cursor.innerHTML = CURSOR_SVG;
    return node;
  },
  fit(node) {
    node._w = node.offsetWidth;
    node._h = node.offsetHeight;
    // the arrow's tip lands a little right of and below each button's middle
    const at = (b) => [b.offsetLeft + b.offsetWidth * 0.55, b.offsetTop + b.offsetHeight * 0.6];
    // inside the card's bottom edge: a cursor hanging below it would cross into the caption band
    node._home = [node._w * 0.86, node._h - 60];
    node._targets = { approve: at(node._approve), decline: at(node._decline) };
  },
  update(node, v, t) {
    const at = clickAt(v);
    for (const b of [node._approve, node._decline]) b._mark.style.display = "none";
    if (at == null || !v.click) {
      node._cursor.style.opacity = "0";
      return;
    }
    const chosen = v.click === "decline" ? node._decline : node._approve;
    const other = chosen === node._approve ? node._decline : node._approve;
    // the arrow drifts up from the card's lower corner and reaches the button as it is pressed
    const move = easeInOut(clamp01((t - (at - CURSOR_MS)) / CURSOR_MS));
    const [tx, ty] = node._targets[v.click];
    const [hx, hy] = node._home;
    node._cursor.style.opacity = clamp01((t - (at - CURSOR_MS - 150)) / 150).toFixed(3);
    node._cursor.style.transform = `translate(${(lerp(hx, tx, move) - 9).toFixed(1)}px, ${(lerp(hy, ty, move) - 6).toFixed(1)}px)`;
    const q = (t - at) / PRESS_MS;
    const down = q >= 0 && q < 1 ? Math.sin(Math.PI * q) : 0;
    chosen.style.transform = `scale(${(1 - 0.12 * down).toFixed(4)})`;
    const done = t >= at + PRESS_MS / 2;
    chosen.classList.toggle("chosen", done);
    other.style.opacity = done ? lerp(1, 0.35, clamp01((t - at - PRESS_MS / 2) / 200)).toFixed(3) : "1";
    // the pressed button gains its ✓ or ✕, popping in
    const m = chosen._mark;
    m.style.display = done ? "inline-block" : "none";
    m.style.transform = `scale(${Math.max(0, backOut(clamp01((t - at - PRESS_MS / 2) / ENTER_MS))).toFixed(4)})`;
  },
};

// ------------------------------------------------------------ error (overlay)

const error = {
  ownEnter: true,
  build(v) {
    const node = el("div", "oserror");
    const bar = el("div", "etitlebar", node);
    el("span", "etitle", bar, v.title);
    el("span", "eclose", bar, "✕");
    const body = el("div", "ebody", node);
    svg(body, "ewarn", WARN_SVG);
    el("div", "etext", body, v.body);
    const foot = el("div", "efoot", node);
    el("div", "ebtn", foot, v.button ?? "OK");
    return node;
  },
  /** Pops in and shakes ±14 px, dying out over 300 ms. main.mjs centres the wrapper over the stage. */
  update(node, v, t) {
    const p = clamp01((t - v.enterMs) / 120);
    const q = clamp01((t - v.enterMs) / SHAKE_MS);
    const x = q < 1 ? SHAKE_PX * Math.sin(2 * Math.PI * 3 * q) * (1 - q) : 0;
    node.style.opacity = clamp01(p * 1.5).toFixed(3);
    node.style.transform = `translateX(${x.toFixed(1)}px) scale(${lerp(0.9, 1, easeOut(p)).toFixed(4)})`;
  },
};

// ------------------------------------------------------------ receipts

const receipts = {
  ownEnter: true,
  build(v) {
    const node = el("div", "receipts");
    node._tickets = (v.items || []).map((item, i) => {
      const ticket = el("div", "ticket", node);
      ticket._tilt = i % 2 === 0 ? -TICKET_TILT : TICKET_TILT;
      el("div", "ttitle", ticket, item.title);
      el("div", "tdash", ticket);
      el("div", "tsub", ticket, item.sub || " ");
      // the stub the next ticket covers: a perforation and a barcode, so the last one reads as a receipt
      el("div", "tperf", ticket);
      el("div", "tcode", ticket);
      return ticket;
    });
    return node;
  },
  fit(node) {
    // each ticket overlaps the one before by 40 % of its height; the box holds the whole fan
    const h = Math.max(...node._tickets.map((tk) => tk.offsetHeight));
    node._tickets.forEach((tk, i) => { tk.style.top = `${(i * (1 - TICKET_OVERLAP) * h).toFixed(1)}px`; });
    const pad = 28; // room for the ±3° tilt's corners
    node.style.height = `${Math.ceil(h * (1 + (1 - TICKET_OVERLAP) * (node._tickets.length - 1))) + 2 * pad}px`;
  },
  update(node, v, t) {
    node._tickets.forEach((tk, i) => {
      // each drops onto the pile from above, a touch big, and settles at its tilt
      const p = staggerP(i, v.enterMs, t, TICKET_GAP_MS, ENTER_MS);
      tk.style.visibility = p > 0 ? "visible" : "hidden";
      tk.style.opacity = clamp01(p * 2.5).toFixed(3);
      const s = lerp(1.18, 1, backOut(p));
      const r = lerp(tk._tilt * 3, tk._tilt, easeOut(p));
      tk.style.transform = `translate(-50%, ${(-70 * (1 - easeOut(p))).toFixed(1)}px) rotate(${r.toFixed(2)}deg) scale(${s.toFixed(4)})`;
    });
  },
};

// ------------------------------------------------------------ checklist

const checklist = {
  build(v) {
    const node = el("div", "card checklist");
    const n = (v.items || []).length;
    node._items = (v.items || []).map((text, i) => {
      const row = el("div", "citem", node);
      const box = el("div", "cbox", row);
      const spinning = v.state === "progress" && i === n - 1;
      const mark = spinning ? el("div", "spin", box) : svg(box, "tick", CHECK_SVG);
      el("div", "ctext", row, text);
      return { row, box, mark, spinning };
    });
    return node;
  },
  update(node, v, t) {
    node._items.forEach((it, i) => {
      const p = staggerP(i, v.enterMs, t, ITEM_GAP_MS, ENTER_MS);
      it.row.style.opacity = clamp01(p * 2).toFixed(3);
      it.row.style.transform = `translateX(${(-30 * (1 - easeOut(p))).toFixed(1)}px)`;
      if (it.spinning) {
        // the work still going: a turning arc, its angle from t alone
        it.mark.style.transform = `rotate(${((t * 0.42) % 360).toFixed(1)}deg)`;
        it.row.classList.add("going");
        return;
      }
      const q = clamp01((t - v.enterMs - i * ITEM_GAP_MS - TICK_AFTER_MS) / ENTER_MS);
      it.row.classList.toggle("done", q > 0);
      it.mark.style.transform = `scale(${Math.max(0, backOut(q)).toFixed(4)})`;
      it.box.style.transform = q > 0 && q < 1 ? `scale(${(1 + 0.18 * Math.sin(Math.PI * q)).toFixed(4)})` : "none";
    });
  },
};

// ------------------------------------------------------------ versus

const versus = {
  build(v) {
    const node = el("div", "versus");
    const side = (score, cls) => {
      const card = el("div", `card vcard ${cls}`, node);
      card._emoji = score.emoji ? el("div", "vemoji", card, score.emoji) : null;
      el("div", "vlabel", card, score.label);
      card._value = el("div", "vvalue", card, score.value);
      return card;
    };
    node._left = side(v.left || {}, "left");
    node._vs = el("div", "vs", node, "VS");
    node._right = side(v.right || {}, "right");
    node._win = winner(v.left?.value, v.right?.value) === "left" ? node._left : node._right;
    return node;
  },
  fit(node) {
    for (const card of [node._left, node._right]) fitWidth(card._value, 290, 48);
  },
  update(node, v, t) {
    // once both are in, the winner lights up with a little bump
    const g = clamp01((t - v.enterMs - VS_GLOW_AT_MS) / VS_GLOW_MS);
    [node._left, node._right].forEach((card, i) => {
      const p = staggerP(i, v.enterMs, t, VS_CARD_GAP_MS, ENTER_MS);
      const bump = card === node._win ? 0.06 * Math.sin(Math.PI * g) : 0;
      card.style.opacity = clamp01(p * 2).toFixed(3);
      card.style.transform = `translateY(${(30 * (1 - easeOut(p))).toFixed(1)}px) scale(${(lerp(0.85, 1, backOut(p)) + bump).toFixed(4)})`;
      slam(card._value, t, v.enterMs + i * VS_CARD_GAP_MS + 120);
    });
    slam(node._vs, t, v.enterMs + 2 * VS_CARD_GAP_MS);
    node._win.classList.toggle("win", g > 0);
    node._win.style.setProperty("--glow", g.toFixed(3));
  },
};

// ------------------------------------------------------------ bars

const bars = {
  build(v) {
    const node = el("div", "card bars");
    const items = v.items || [];
    const max = Math.max(0, ...items.map((it) => it.value || 0));
    node._rows = items.map((it, i) => {
      const lit = v.highlight == null || v.highlight === i;
      const row = el("div", lit ? "brow lit" : "brow", node);
      el("div", "blabel", row, it.label);
      const track = el("div", "btrack", row);
      const fill = el("div", "bfill", track);
      const value = el("div", "bvalue", row);
      const num = el("span", null, value, "");
      if (v.unit) el("span", "bunit", value, v.unit);
      const decimals = Math.min(2, (String(it.value).split(".")[1] || "").length);
      return { row, fill, num, frac: max > 0 ? (it.value || 0) / max : 0, roll: { to: it.value || 0, decimals, enterMs: 0 } };
    });
    return node;
  },
  fit(node) {
    // every value column as wide as the widest final value, so all the tracks share one length
    for (const r of node._rows) r.num.textContent = counterValue(r.roll, Infinity);
    const widest = Math.max(...node._rows.map((r) => r.num.parentNode.scrollWidth));
    for (const r of node._rows) {
      r.num.style.minWidth = `${Math.ceil(r.num.scrollWidth)}px`;
      r.num.parentNode.style.minWidth = `${Math.ceil(widest)}px`;
    }
  },
  update(node, v, t) {
    node._rows.forEach((r, i) => {
      const p = staggerP(i, v.enterMs, t, BAR_GAP_MS, BAR_MS);
      r.row.style.opacity = clamp01((t - v.enterMs - i * BAR_GAP_MS + 120) / 200).toFixed(3);
      r.fill.style.transform = `scaleX(${(r.frac * easeOut(p)).toFixed(4)})`;
      // the value rolls on the bar's own ease (counterValue eases over COUNTER_MS, so p is mapped onto it)
      r.num.textContent = counterValue(r.roll, p * COUNTER_MS);
    });
  },
};

export const CARDS = { equation, chat, dialog, error, receipts, checklist, versus, bars };
