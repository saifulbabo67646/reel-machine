// The cinematic overlay: a title card, punch captions (two or three words at a time, each
// popping in as it is spoken), an end card, the impact flash and a progress bar.
import { clamp01, easeOutBack } from "./math.mjs";

// words -> chunks of at most three, broken after punctuation or when a chunk gets long
export function chunkWords(words, hot = []) {
  const chunks = [];
  let cur = [];
  words.forEach((w, i) => {
    cur.push({ ...w, hot: !!hot[i], index: i });
    const text = cur.map((c) => c.text).join(" ");
    if (cur.length >= 3 || /[.,!?;:…]$/.test(w.text) || text.length > 16) {
      chunks.push(cur);
      cur = [];
    }
  });
  if (cur.length) chunks.push(cur);
  return chunks.map((c, k) => ({
    words: c,
    start: c[0].start - 0.05,
    end: k + 1 < chunks.length ? chunks[k + 1][0].start - 0.05 : c[c.length - 1].end + 0.6,
  }));
}

// "*monster fish*" in a title is drawn in the accent colour
export function titleHtml(title) {
  return String(title)
    .split("*")
    .map((part, i) => {
      const safe = part.replace(/[&<>]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[ch]);
      return i % 2 ? `<b>${safe}</b>` : safe;
    })
    .join("");
}

export class CinematicOverlay {
  constructor(root, shot) {
    this.shot = shot;
    const L = shot.look;
    root.classList.add("cine");
    this.title = root.querySelector("#title");
    this.label = root.querySelector("#label");
    this.captions = root.querySelector("#captions");
    const make = (id) => {
      const el = document.createElement("div");
      el.id = id;
      root.appendChild(el);
      return el;
    };
    this.card = make("card");
    this.flash = make("flash");
    this.bar = make("bar");
    this.bar.style.background = L.progress || "#ffe14a";
    if (shot.captions.title) {
      this.title.innerHTML = titleHtml(shot.captions.title);
      this.title.style.display = "block";
    }
    if (shot.captions.label) {
      this.label.textContent = shot.captions.label;
      this.label.style.display = "block";
    }
    if (shot.captions.card) this.card.textContent = shot.captions.card;
    this.chunks = chunkWords(shot.captions.words, shot.captions.hot);
    this.current = -1;
    this.spans = [];
  }

  update(t, flash) {
    const shot = this.shot;
    const ci = this.chunks.findIndex((c) => t >= c.start && t < c.end);
    if (ci !== this.current) {
      this.captions.innerHTML = "";
      this.spans = (ci >= 0 ? this.chunks[ci].words : []).map((w) => {
        const s = document.createElement("span");
        s.textContent = w.text;
        if (w.hot) s.className = "hot";
        this.captions.appendChild(s);
        return s;
      });
      this.current = ci;
    }
    if (ci >= 0) {
      this.chunks[ci].words.forEach((w, k) => {
        const age = t - w.start, s = this.spans[k];
        if (age < 0) {
          s.style.opacity = "0";
          s.style.transform = "scale(0.4)";
          return;
        }
        const sc = 0.4 + 0.6 * easeOutBack(clamp01(age / 0.16));
        s.style.opacity = "1";
        s.style.transform = `scale(${sc.toFixed(4)}) rotate(${((k % 2 ? 1 : -1) * 2 * (1 - clamp01(age / 0.2))).toFixed(3)}deg)`;
      });
    }
    // the title card pops in on the first shot only
    const first = shot.reel.startMs === 0;
    const tk = first ? 0.6 + 0.4 * easeOutBack(clamp01(t / 0.35)) : 1;
    this.title.style.transform = `translateX(-50%) scale(${tk.toFixed(4)})`;
    if (shot.captions.card) {
      const e = t - 0.15;
      this.card.style.opacity = e > 0 ? "1" : "0";
      const k = e > 0 ? 0.3 + 0.7 * easeOutBack(clamp01(e / 0.3)) + 0.03 * Math.sin(e * 6) : 0.3;
      this.card.style.transform = `scale(${k.toFixed(4)}) rotate(${(-3 + 3 * clamp01(e / 0.3)).toFixed(3)}deg)`;
    }
    this.flash.style.opacity = String(clamp01(flash));
    const total = shot.reel.totalMs / 1000;
    this.bar.style.width = `${(clamp01((shot.reel.startMs / 1000 + t) / total) * 100).toFixed(3)}%`;
  }
}
