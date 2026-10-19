// reelmachine/recipes/techreel/runtime/hero.mjs
// The recurring hero object. buildHero makes it once; updateHero sets what happens inside it at t (the liquid
// swirl, the fans, the blink, pulse, shake, rings, "z z z", the screen text, the glass box, the split dots).
// Where it sits and how big it is (the state moves) is main.mjs's, on the outer node.
import { blinkAt, clamp01, seedOf, splitPoints, HERO_MOVE_MS } from "./pure.mjs";
import { el } from "./components.mjs";

/** Natural size of each hero (px), what the stage stack lays out. */
export const HERO_SIZE = {
  orb: { w: 360, h: 360 }, laptop: { w: 760, h: 470 }, monitor: { w: 620, h: 540 },
  chip: { w: 700, h: 400 }, phone: { w: 330, h: 640 }, robot: { w: 440, h: 460 },
  svg: { w: 420, h: 420 }, image: { w: 420, h: 420 }, // the caller's art, centred in a 420 px box
};
// where text shows on the screen-like heroes, in the hero's own px
const SCREEN = {
  laptop: { x: 114, y: 34, w: 532, h: 336 }, monitor: { x: 36, y: 36, w: 548, h: 344 },
  phone: { x: 26, y: 26, w: 278, h: 588 },
};
// the heroes that can split into dots (the others treat `split` as `center`, main.mjs); the dots spread over
// SPLIT_W times the hero's width and its height
export const SPLITS = new Set(["orb", "robot"]);
const SPLIT_W = 2, SPLIT_MAX = 6;
const SHAKE_MS = 400, SHAKE_PX = 10, SHAKE_HZ = 12, PULSE_HZ = 1.2, RING_PERIOD_MS = 1600;
const FAN_RPS = 0.8, LOCK_DROP_PX = 380, GLASS_PAD = 36;
const INK = "#efeae4"; // the line art's off-white
const BOARD = "#050809"; // the line art's fill: near-black, so the objects read solid over the glows

const NS = "http://www.w3.org/2000/svg";

// the orb's reflected world is drawn ORB_PAD px past the orb on every side, so the liquid and the lens warps
// always have world to pull in from; at the rim the lens reads LENS_PX further out, and at mid-radius it turns
// the reflection up to SWIRL_PX round the centre
const ORB = 360, ORB_PAD = 200, LENS_PX = 110, SWIRL_PX = 60;
// the liquid's contrast curve: darks a little deeper, lights brighter, so the reflections read as polished
const CHROME_CURVE = "0 0.08 0.2 0.38 0.6 0.8 0.93 1";

function orbSvg(seed, lens) {
  // liquid chrome is a mirror of a world: a dark sky (the reel's night) over a bright, blurred horizon and a
  // pale floor. Turbulence (drifting with t) warps that world into flowing liquid, a finer noise thresholded
  // hard gives the crisp white glints, and a sphere lens squeezes both towards the rim so they wrap round the
  // ball. On top: the fresnel edge, a soft accent rim light, an inner caustic and the hard white specular.
  const P = ORB_PAD, W = ORB + 2 * P;
  const band = (y, h, fill, o = 1) => `<rect x="${-P}" y="${y}" width="${W}" height="${h}" fill="${fill}" opacity="${o}"/>`;
  return `
<svg xmlns="${NS}" width="${ORB}" height="${ORB}" viewBox="0 0 ${ORB} ${ORB}">
  <defs>
    <linearGradient id="hero-sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#34444a"/><stop offset="0.3" stop-color="#56676b"/>
      <stop offset="0.46" stop-color="#8e9b9d"/><stop offset="0.52" stop-color="#d4dbdc"/>
      <stop offset="0.6" stop-color="#7f8d90"/><stop offset="0.8" stop-color="#a2adaf"/>
      <stop offset="1" stop-color="#8d999b"/>
    </linearGradient>
    <radialGradient id="hero-body" cx="34%" cy="68%" r="58%">
      <stop offset="0" stop-color="#d3dadb" stop-opacity="0.78"/>
      <stop offset="0.4" stop-color="#c3cbcc" stop-opacity="0.5"/>
      <stop offset="1" stop-color="#b9c2c3" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="hero-shade" cx="50%" cy="50%" r="50%" fx="42%" fy="40%">
      <stop offset="0.6" stop-color="#0b1416" stop-opacity="0"/>
      <stop offset="0.86" stop-color="#0b1416" stop-opacity="0.3"/>
      <stop offset="0.96" stop-color="#0b1416" stop-opacity="0.72"/>
      <stop offset="1" stop-color="#0b1416" stop-opacity="0.8"/>
    </radialGradient>
    <radialGradient id="hero-rim" cx="50%" cy="28%" r="74%">
      <stop offset="0.74" stop-color="var(--accent)" stop-opacity="0"/>
      <stop offset="0.9" stop-color="var(--accent)" stop-opacity="0.22"/>
      <stop offset="0.97" stop-color="var(--accent)" stop-opacity="0.5"/>
    </radialGradient>
    <radialGradient id="hero-spec" cx="50%" cy="50%" r="50%">
      <stop offset="0" stop-color="#fff" stop-opacity="1"/>
      <stop offset="0.7" stop-color="#fff" stop-opacity="0.95"/>
      <stop offset="1" stop-color="#fff" stop-opacity="0"/>
    </radialGradient>
    <filter id="hero-soft" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="8"/></filter>
    <filter id="hero-caustic" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3"/></filter>
    <filter id="hero-liquid" filterUnits="userSpaceOnUse" x="${-P}" y="${-P}" width="${W}" height="${W}" color-interpolation-filters="sRGB">
      <feTurbulence id="hero-turb" type="fractalNoise" baseFrequency="0.007 0.010" numOctaves="2" seed="${seed}" result="noise"/>
      <feOffset id="hero-drift" in="noise" dx="0" dy="0" result="moved"/>
      <feDisplacementMap in="SourceGraphic" in2="moved" scale="150" xChannelSelector="R" yChannelSelector="G" result="liquid"/>
      <feComponentTransfer in="liquid" result="crisp">
        <feFuncR type="table" tableValues="${CHROME_CURVE}"/>
        <feFuncG type="table" tableValues="${CHROME_CURVE}"/>
        <feFuncB type="table" tableValues="${CHROME_CURVE}"/>
      </feComponentTransfer>
      <feTurbulence id="hero-glint" type="fractalNoise" baseFrequency="0.006 0.022" numOctaves="2" seed="${seed + 1}" result="fine"/>
      <feDisplacementMap in="fine" in2="moved" scale="90" xChannelSelector="R" yChannelSelector="G" result="fineflow"/>
      <feColorMatrix in="fineflow" type="matrix" values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  1 0 0 0 0" result="fineA"/>
      <feComponentTransfer in="fineA" result="glints"><feFuncA type="linear" slope="12" intercept="-6.4"/></feComponentTransfer>
      <feMerge result="world"><feMergeNode in="crisp"/><feMergeNode in="glints"/></feMerge>
      ${lens ? `<feImage href="${lens}" x="0" y="0" width="${ORB}" height="${ORB}" preserveAspectRatio="none" result="lens"/>
      <feDisplacementMap in="world" in2="lens" scale="${2 * (LENS_PX + SWIRL_PX)}" xChannelSelector="R" yChannelSelector="G"/>` : ""}
      <feGaussianBlur stdDeviation="0.9"/>
    </filter>
    <clipPath id="hero-clip"><circle cx="180" cy="180" r="179.5"/></clipPath>
  </defs>
  <g clip-path="url(#hero-clip)">
    <rect width="${ORB}" height="${ORB}" fill="#b4bec0"/>
    <g filter="url(#hero-liquid)">
      <g transform="rotate(-24 180 180)"><g id="hero-flow">
        <rect x="${-P}" y="${-P}" width="${W}" height="${W}" fill="url(#hero-sky)"/>
        <g filter="url(#hero-soft)">
          ${band(70, 26, "#1f2c31", 0.9)}
          ${band(120, 18, "#c3cdce", 0.7)}
          ${band(176, 20, "#fff")}
          ${band(232, 14, "#6f7e82", 0.8)}
          ${band(296, 22, "#d0d8d9", 0.7)}
          ${band(340, 16, "#fff", 0.9)}
          <ellipse cx="180" cy="${ORB + 40}" rx="240" ry="50" fill="var(--accent)" opacity="0.1"/>
        </g>
      </g></g>
    </g>
    <circle cx="180" cy="180" r="180" fill="url(#hero-body)"/>
    <circle cx="180" cy="180" r="180" fill="url(#hero-shade)"/>
    <circle cx="180" cy="180" r="180" fill="url(#hero-rim)"/>
    <g id="hero-caustic" filter="url(#hero-caustic)" opacity="0.3">
      <path d="M150 300 C 214 300, 270 262, 276 214 C 280 180, 262 160, 240 158"
            stroke="color-mix(in srgb, var(--accent) 45%, #fff)" stroke-width="6" fill="none" stroke-linecap="round"/>
    </g>
    <ellipse cx="112" cy="84" rx="30" ry="14" fill="url(#hero-spec)" transform="rotate(-38 112 84)"/>
    <ellipse cx="84" cy="128" rx="8" ry="5" fill="#fff" opacity="0.85" transform="rotate(-55 84 128)"/>
  </g>
  <circle cx="180" cy="180" r="179" fill="none" stroke="rgba(255,255,255,0.45)" stroke-width="1.5"/>
</svg>`;
}

/** The sphere lens as a displacement map (a data URL, drawn once): red/green say how far right/down each pixel
    of the orb reads its reflection from. Outwards with the square of the distance from the centre, so the world
    bunches up towards the rim as on a ball; and round the centre, most at mid-radius, so the reflections curl.
    Plain arithmetic per pixel, so every render draws the same map; "" (no lens, a flat mirror) without a canvas. */
function lensMap() {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = ORB;
  const ctx = canvas.getContext("2d");
  if (!ctx) return "";
  const img = ctx.createImageData(ORB, ORB);
  const R = ORB / 2, reach = 2 * (LENS_PX + SWIRL_PX);
  for (let y = 0; y < ORB; y++) {
    for (let x = 0; x < ORB; x++) {
      const nx = (x + 0.5 - R) / R, ny = (y + 0.5 - R) / R;
      const r = Math.min(1, Math.hypot(nx, ny));
      const out = LENS_PX * r * r, curl = SWIRL_PX * Math.sin(Math.PI * r);
      const u = r > 0 ? nx / Math.hypot(nx, ny) : 0, v = r > 0 ? ny / Math.hypot(nx, ny) : 0;
      const i = 4 * (y * ORB + x);
      img.data[i] = Math.round(255 * (0.5 + (out * u - curl * v) / reach));
      img.data[i + 1] = Math.round(255 * (0.5 + (out * v + curl * u) / reach));
      img.data[i + 2] = 128;
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return canvas.toDataURL("image/png");
}

const laptopSvg = () => `
<svg xmlns="${NS}" width="760" height="470" viewBox="0 0 760 470" fill="none" stroke="${INK}" stroke-width="4" stroke-linejoin="round">
  <rect x="92" y="12" width="576" height="380" rx="22" fill="#050809"/>
  <rect x="114" y="34" width="532" height="336" rx="6" stroke-width="2" stroke="rgba(239,234,228,0.18)" fill="rgba(0,0,0,0.25)"/>
  <path d="M30 404 H730 L700 446 H60 Z" fill="#050809"/>
  <path d="M320 404 V414 H440 V404" stroke-width="3"/>
</svg>`;

const monitorSvg = () => `
<svg xmlns="${NS}" width="620" height="540" viewBox="0 0 620 540" fill="none" stroke="${INK}" stroke-width="4" stroke-linejoin="round">
  <rect x="12" y="12" width="596" height="392" rx="20" fill="#050809"/>
  <rect x="36" y="36" width="548" height="344" rx="6" stroke-width="2" stroke="rgba(239,234,228,0.18)" fill="rgba(0,0,0,0.25)"/>
  <path d="M270 404 L252 492 H368 L350 404"/>
  <rect x="190" y="492" width="240" height="22" rx="11"/>
</svg>`;

// a fan's seven blades, swept, round (0, 0); the group is turned from t
const blades = (r) => Array.from({ length: 7 }, (_, i) =>
  `<path transform="rotate(${(i * 360) / 7})" d="M0 -${r * 0.24} C ${r * 0.34} -${r * 0.42} ${r * 0.5} -${r * 0.7} ${r * 0.2} -${r * 0.93} C ${r * 0.06} -${r * 0.7} -${r * 0.06} -${r * 0.44} 0 -${r * 0.24} Z" fill="rgba(239,234,228,0.10)" stroke-width="3"/>`).join("");

const chipSvg = () => `
<svg xmlns="${NS}" width="700" height="400" viewBox="0 0 700 400" fill="none" stroke="${INK}" stroke-width="4" stroke-linejoin="round" stroke-linecap="round">
  <path d="M14 26 H44 V350 H14 Z M14 70 H6 M14 300 H6" fill="${BOARD}"/>
  <rect x="44" y="34" width="640" height="300" rx="20" fill="${BOARD}"/>
  <rect x="66" y="56" width="596" height="232" rx="30" stroke-width="3" fill="rgba(239,234,228,0.03)"/>
  ${[210, 518].map((cx, k) => `
  <circle cx="${cx}" cy="172" r="104" stroke-width="3"/>
  <circle cx="${cx}" cy="172" r="96" stroke-width="1.5" stroke="rgba(239,234,228,0.25)"/>
  <g transform="translate(${cx} 172)"><g class="fan" data-k="${k}">${blades(92)}</g></g>
  <circle cx="${cx}" cy="172" r="24" fill="var(--accent)" stroke="none" style="filter: drop-shadow(0 0 14px var(--accent))"/>
  <circle cx="${cx}" cy="172" r="24" fill="none"/>`).join("")}
  <g stroke-width="2.5">${[0, 1, 2, 3, 4].map((i) => `<rect x="${86 + i * 44}" y="300" width="32" height="22" rx="3"/>`).join("")}</g>
  <rect x="470" y="298" width="180" height="26" rx="13" fill="var(--accent)" stroke="none"/>
  <text x="560" y="318" text-anchor="middle" fill="var(--accentInk)" stroke="none" font-family="var(--mono)" font-size="19" font-weight="800" letter-spacing="5">VRAM</text>
  <path d="M190 334 V372 H560 V334" fill="${BOARD}"/>
  <path d="M${Array.from({ length: 16 }, (_, i) => `${214 + i * 21} 344 V364`).join(" M")}" stroke="var(--accent)" stroke-width="5" opacity="0.8"/>
</svg>`;

const phoneSvg = () => `
<svg xmlns="${NS}" width="330" height="640" viewBox="0 0 330 640" fill="none" stroke="${INK}" stroke-width="4" stroke-linejoin="round" stroke-linecap="round">
  <rect x="8" y="8" width="314" height="624" rx="54" fill="${BOARD}"/>
  <rect x="26" y="26" width="278" height="588" rx="38" stroke-width="2" stroke="rgba(239,234,228,0.18)" fill="rgba(0,0,0,0.25)"/>
  <rect x="122" y="44" width="86" height="26" rx="13" fill="${INK}" stroke="none" opacity="0.9"/>
  <path d="M2 150 V200 M2 220 V270 M328 180 V260" stroke-width="5"/>
  <path d="M125 596 H205" stroke-width="5" opacity="0.6"/>
</svg>`;

// eyes are ellipses so a blink only squeezes their height
const ROBOT_EYES = [[172, 226], [268, 226]], EYE_R = 24;
const robotSvg = () => `
<svg xmlns="${NS}" width="440" height="460" viewBox="0 0 440 460" fill="none" stroke="${INK}" stroke-width="4" stroke-linejoin="round" stroke-linecap="round">
  <path d="M220 96 V48"/>
  <circle cx="220" cy="34" r="16" fill="var(--accent)" stroke="none" style="filter: drop-shadow(0 0 16px var(--accent))"/>
  <rect x="24" y="196" width="30" height="100" rx="12" fill="${BOARD}"/>
  <rect x="386" y="196" width="30" height="100" rx="12" fill="${BOARD}"/>
  <rect x="54" y="96" width="332" height="300" rx="76" fill="${BOARD}"/>
  <rect x="92" y="148" width="256" height="190" rx="56" stroke-width="2" stroke="rgba(239,234,228,0.22)" fill="rgba(239,234,228,0.03)"/>
  ${ROBOT_EYES.map(([cx, cy]) => `<ellipse class="eye" cx="${cx}" cy="${cy}" rx="${EYE_R}" ry="${EYE_R}" fill="var(--accent)" stroke="none" style="filter: drop-shadow(0 0 12px var(--accent))"/>`).join("")}
  <path d="M182 286 Q220 314 258 286" stroke="var(--accent)" stroke-width="6"/>
  <circle cx="132" cy="296" r="12" fill="var(--accent)" stroke="none" opacity="0.18"/>
  <circle cx="308" cy="296" r="12" fill="var(--accent)" stroke="none" opacity="0.18"/>
  <path d="M176 396 V420 H264 V396" fill="${BOARD}"/>
</svg>`;

const ART = { laptop: laptopSvg, monitor: monitorSvg, phone: phoneSvg, chip: chipSvg, robot: robotSvg };

// a padlock, its shackle up; it drops onto the glass box
const padlockSvg = () => `
<svg xmlns="${NS}" width="150" height="180" viewBox="0 0 150 180" fill="none" stroke="${INK}" stroke-width="6" stroke-linejoin="round" stroke-linecap="round">
  <path d="M38 80 V54 a37 37 0 0 1 74 0 V80" stroke-width="12"/>
  <rect x="14" y="78" width="122" height="94" rx="20" fill="var(--accent)" stroke="none" style="filter: drop-shadow(0 0 22px var(--accent))"/>
  <circle cx="75" cy="116" r="12" fill="var(--accentInk)" stroke="none"/>
  <path d="M75 122 V146" stroke="var(--accentInk)" stroke-width="10"/>
</svg>`;

/** A drop landing with two small bounces, 0..1. */
function dropOut(p) {
  const n = 7.5625, d = 2.75;
  if (p < 1 / d) return n * p * p;
  if (p < 2 / d) return n * (p -= 1.5 / d) * p + 0.75;
  if (p < 2.5 / d) return n * (p -= 2.25 / d) * p + 0.9375;
  return n * (p -= 2.625 / d) * p + 0.984375;
}

export function buildHero(kind, doc) {
  const size = HERO_SIZE[kind] || HERO_SIZE.orb;
  const node = el("div", `hero ${kind}`);
  node.style.width = `${size.w}px`;
  node.style.height = `${size.h}px`;
  const inner = el("div", "hero-inner", node);
  inner.style.position = "relative";
  inner.style.width = "100%";
  inner.style.height = "100%";
  const ui = { node, inner, kind, size, rings: [], zs: [], text: null, fans: [], eyes: [], dots: [] };
  const seed = seedOf(JSON.stringify(doc.look || {}));
  // the rings sit behind the object
  // round the orb; round the whole object for the boxy ones (they are opaque, so a ring only shows outside)
  const ringD = SCREEN[kind] || kind === "chip" ? Math.max(size.w, size.h) * 0.8 : size.w;
  for (let i = 0; i < 3; i++) {
    const ring = el("div", "ring", inner);
    Object.assign(ring.style, { width: `${ringD}px`, height: `${ringD}px`, left: `${(size.w - ringD) / 2}px`, top: `${(size.h - ringD) / 2}px` });
    ui.rings.push(ring);
  }
  if (kind === "orb") {
    el("div", "orbglow", inner);
    const art = el("div", null, inner);
    art.style.position = "relative";
    art.innerHTML = orbSvg(seed % 1000, lensMap());
    ui.turb = art.querySelector("#hero-turb");
    ui.flow = art.querySelector("#hero-flow");
    ui.drift = art.querySelector("#hero-drift");
    ui.caustic = art.querySelector("#hero-caustic");
    ui.label = el("div", "orblabel", inner);
  } else if (kind === "chip" || kind === "robot") {
    const art = el("div", null, inner);
    art.style.position = "relative";
    art.innerHTML = ART[kind]();
    ui.fans = [...art.querySelectorAll(".fan")];
    ui.eyes = [...art.querySelectorAll(".eye")];
    ui.blinkSeed = seed;
    ui.label = el("div", "orblabel", inner);
  } else if (!SCREEN[kind]) {
    // an agent's drawing (scoped by compose.py, ids prefixed "c_hero.") or the caller's image, contained
    const art = el("div", "heroart", inner);
    if (kind === "svg") {
      art.innerHTML = doc.hero?.svg || "";
      const drawn = art.querySelector("svg");
      if (drawn) {
        drawn.setAttribute("width", String(size.w));
        drawn.setAttribute("height", String(size.h));
      }
    } else if (doc.hero?.file) {
      const img = el("img", null, art);
      img.alt = "";
      img.src = doc.hero.file;
      node._ready = (img.decode ? img.decode() : Promise.resolve()).catch(() => null);
    }
    ui.label = el("div", "orblabel", inner);
  } else {
    const box = SCREEN[kind];
    const glow = el("div", "screenglow", inner);
    Object.assign(glow.style, { left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px` });
    const art = el("div", null, inner);
    art.style.position = "relative";
    art.innerHTML = ART[kind]();
    ui.glow = glow;
    ui.label = el("div", "screen", inner);
    Object.assign(ui.label.style, { left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px` });
  }
  // "z z z" rises from the top right
  for (let i = 0; i < 3; i++) {
    const z = el("div", "zzz", inner, "z");
    z.style.left = `${size.w * 0.8 + i * 44}px`;
    z.style.top = `${-20 - i * 56}px`;
    z.style.fontSize = `${56 + i * 20}px`;
    ui.zs.push(z);
  }
  // locked: a frosted glass box over the object, a padlock dropping onto it
  ui.glass = el("div", "glass", inner);
  Object.assign(ui.glass.style, { left: `${-GLASS_PAD}px`, top: `${-GLASS_PAD}px`,
    width: `${size.w + 2 * GLASS_PAD}px`, height: `${size.h + 2 * GLASS_PAD}px` });
  ui.lock = el("div", "padlock", inner);
  ui.lock.innerHTML = padlockSvg();
  Object.assign(ui.lock.style, { left: `${size.w / 2 - 75}px`, top: `${size.h / 2 - 90}px` });
  // split: accent dots, outside the inner box so they do not shrink with the object
  if (SPLITS.has(kind)) {
    for (let i = 0; i < SPLIT_MAX; i++) {
      const dot = el("div", "splitdot", node);
      const label = el("div", "splitlabel", dot);
      ui.dots.push({ dot, label });
    }
  }
  node._hero = ui;
  return node;
}

/** How much of a state's effect shows: in over the move as it starts, out over the move as the next one starts. */
function amount(h, name) {
  const p = clamp01(h.p);
  return (h.state === name ? p : 0) + (h.from === name ? 1 - p : 0);
}

function setScreenText(ui, text) {
  if (ui.text === text) return;
  ui.text = text;
  ui.label.textContent = text;
  if (!SCREEN[ui.kind]) return;
  // as big as fits in the screen's width (measured only when the text changes)
  const box = SCREEN[ui.kind];
  let px = 170;
  ui.label.style.fontSize = `${px}px`;
  ui.label.style.width = "auto";
  while (ui.label.scrollWidth > box.w * 0.84 && px > 40) {
    px -= 6;
    ui.label.style.fontSize = `${px}px`;
  }
  ui.label.style.width = `${box.w}px`;
}

/** The split dots: `split` is the split state in play (the current one or the one being left), or null. */
function updateSplit(ui, h, split) {
  const a = amount(h, "split");
  // out over the move as the split starts, back in over the move as it ends
  const p = h.state === "split" ? clamp01(h.p) : h.from === "split" ? 1 - clamp01(h.p) : 0;
  const count = split ? Math.min(split.count ?? 3, ui.dots.length) : 0;
  const pts = splitPoints(count, ui.size.w * SPLIT_W, ui.size.h, p);
  ui.dots.forEach(({ dot, label }, i) => {
    if (i >= count || a <= 0.001) {
      dot.style.display = "none";
      return;
    }
    const text = split.labels?.[i] ?? "";
    if (label.textContent !== text) label.textContent = text;
    label.style.display = text ? "block" : "none";
    dot.style.display = "block";
    dot.style.left = `${ui.size.w / 2}px`;
    dot.style.top = `${ui.size.h / 2}px`;
    dot.style.opacity = clamp01(p * 3).toFixed(3);
    dot.style.transform = `translate(-50%, -50%) translate(${pts[i].x.toFixed(1)}px, ${pts[i].y.toFixed(1)}px) scale(${(0.4 + 0.6 * clamp01(p * 1.5)).toFixed(3)})`;
  });
  return a;
}

/** `h` is heroAt(states, t); main.mjs places, scales and fades the node itself. `split` is the split state in
    play (main.mjs finds it), for its count and labels. */
export function updateHero(node, h, t, doc, split = null) {
  const ui = node._hero;
  if (!ui || !h) return;
  // the object shrinks to nothing as it splits into dots
  const gone = ui.dots.length ? updateSplit(ui, h, split) : 0;
  // pulse and shake move the inner box; the outer one is main's
  const pulse = amount(h, "pulse");
  const scale = (1 - gone) * (1 + 0.04 * pulse * Math.sin((2 * Math.PI * PULSE_HZ * t) / 1000));
  // the shake runs inside the move's first SHAKE_MS, so the move's progress gives its time
  const dt = clamp01(h.p) * HERO_MOVE_MS;
  const shaking = h.state === "shake" && dt < SHAKE_MS;
  const dx = shaking ? SHAKE_PX * Math.sin((2 * Math.PI * SHAKE_HZ * dt) / 1000) * (1 - dt / SHAKE_MS) : 0;
  ui.inner.style.transform = `translateX(${dx.toFixed(2)}px) scale(${scale.toFixed(4)})`;

  const rings = amount(h, "rings");
  ui.rings.forEach((ring, k) => {
    const q = (((t / RING_PERIOD_MS) + k / 3) % 1 + 1) % 1;
    ring.style.opacity = (rings * 0.8 * (1 - q) * clamp01(q * 6)).toFixed(3);
    ring.style.transform = `scale(${(1.04 + 0.5 * q).toFixed(4)})`;
  });

  const sleep = amount(h, "sleep");
  ui.zs.forEach((z, k) => {
    const q = (((t / 2400) + k / 3) % 1 + 1) % 1;
    z.style.opacity = (sleep * Math.sin(Math.PI * q)).toFixed(3);
    z.style.transform = `translate(${(18 * Math.sin(2 * Math.PI * q)).toFixed(1)}px, ${(-60 * q).toFixed(1)}px)`;
  });

  const screen = amount(h, "screen");
  const text = (h.state === "screen" ? h.text : null) ?? (h.from === "screen" ? ui.text : null) ?? doc.hero?.text ?? "";
  if (screen > 0) setScreenText(ui, text);
  ui.label.style.opacity = screen.toFixed(3);
  if (!SCREEN[ui.kind]) ui.label.style.transform = `translate(-50%, -50%) scale(${(0.8 + 0.2 * screen).toFixed(3)})`;
  if (ui.glow) ui.glow.style.opacity = screen.toFixed(3);

  // locked: the glass frosts in over the move; the padlock drops from above and lands with a bounce
  const locked = amount(h, "locked");
  ui.glass.style.opacity = locked.toFixed(3);
  ui.glass.style.display = locked > 0.001 ? "block" : "none";
  const drop = h.state === "locked" ? dropOut(clamp01(h.p)) : 1;
  ui.lock.style.display = locked > 0.001 ? "block" : "none";
  ui.lock.style.opacity = (h.state === "locked" ? clamp01(h.p * 4) : locked).toFixed(3);
  ui.lock.style.transform = `translateY(${(-LOCK_DROP_PX * (1 - drop)).toFixed(1)}px)`;

  ui.fans.forEach((fan, k) => {
    const deg = ((t / 1000) * FAN_RPS * 360 + k * 23) % 360;
    fan.setAttribute("transform", `rotate(${deg.toFixed(2)})`);
  });
  if (ui.eyes.length) {
    const open = blinkAt(t, ui.blinkSeed);
    for (const eye of ui.eyes) eye.setAttribute("ry", Math.max(2, EYE_R * open).toFixed(2));
  }

  if (ui.turb) {
    // the liquid flow: the noise field drifts and breathes, the reflected world sways and turns under it, the
    // caustic circles — all smooth functions of t, so any frame can be drawn on its own
    const f = 0.0090 + 0.0014 * Math.sin((2 * Math.PI * t) / 5200);
    const g = 0.0160 + 0.0020 * Math.cos((2 * Math.PI * t) / 6900);
    ui.turb.setAttribute("baseFrequency", `${f.toFixed(5)} ${g.toFixed(5)}`);
    ui.drift.setAttribute("dx", (90 * Math.sin((2 * Math.PI * t) / 7000)).toFixed(2));
    ui.drift.setAttribute("dy", (60 * Math.cos((2 * Math.PI * t) / 9000)).toFixed(2));
    const tilt = 9 * Math.sin((2 * Math.PI * t) / 6100);
    const bob = 14 * Math.sin((2 * Math.PI * t) / 4300);
    ui.flow.setAttribute("transform", `rotate(${tilt.toFixed(2)} 180 180) translate(0 ${bob.toFixed(2)})`);
    ui.caustic.setAttribute("transform", `rotate(${(14 * Math.sin((2 * Math.PI * t) / 5600)).toFixed(2)} 220 214)`);
  }
}
