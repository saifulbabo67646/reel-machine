// reelmachine/recipes/techreel/runtime/hero.mjs
// The recurring hero object. buildHero makes it once; updateHero sets what happens inside it at t (the liquid
// swirl, pulse, shake, rings, "z z z", the screen text). Where it sits and how big it is (the state moves) is
// main.mjs's, on the outer node.
import { clamp01, seedOf, HERO_MOVE_MS } from "./pure.mjs";
import { el } from "./components.mjs";

/** Natural size of each hero (px), what the stage stack lays out. */
export const HERO_SIZE = { orb: { w: 360, h: 360 }, laptop: { w: 760, h: 470 }, monitor: { w: 620, h: 540 } };
// where text shows on the screen-like heroes, in the hero's own px
const SCREEN = { laptop: { x: 114, y: 34, w: 532, h: 336 }, monitor: { x: 36, y: 36, w: 548, h: 344 } };
const SHAKE_MS = 400, SHAKE_PX = 10, SHAKE_HZ = 12, PULSE_HZ = 1.2, RING_PERIOD_MS = 1600;
const INK = "#efeae4"; // the line art's off-white

const NS = "http://www.w3.org/2000/svg";

function orbSvg(seed) {
  // chrome is a reflected world: a white sky over a dark horizon that curves round the sphere, a light floor
  // under it lit in the accent. Turbulence displaces that world into liquid swirls; on top, sphere shading:
  // the fresnel falloff, a thin bright rim, the accent bounce light and a white specular.
  return `
<svg xmlns="${NS}" width="360" height="360" viewBox="0 0 360 360">
  <defs>
    <linearGradient id="hero-sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff"/><stop offset="0.55" stop-color="#e4e9eb"/><stop offset="1" stop-color="#9aa4a9"/>
    </linearGradient>
    <linearGradient id="hero-floor" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#f2f5f6"/><stop offset="0.45" stop-color="#8f999e"/><stop offset="1" stop-color="#3a4448"/>
    </linearGradient>
    <radialGradient id="hero-shade" cx="50%" cy="45%" r="52%">
      <stop offset="0.62" stop-color="#000" stop-opacity="0"/>
      <stop offset="0.9" stop-color="#000" stop-opacity="0.32"/>
      <stop offset="0.97" stop-color="#000" stop-opacity="0.5"/>
      <stop offset="1" stop-color="#fff" stop-opacity="0.55"/>
    </radialGradient>
    <radialGradient id="hero-rim" cx="50%" cy="22%" r="82%">
      <stop offset="0.6" stop-color="var(--accent)" stop-opacity="0"/>
      <stop offset="0.76" stop-color="var(--accent)" stop-opacity="0.42"/>
      <stop offset="0.82" stop-color="var(--accent)" stop-opacity="0.7"/>
    </radialGradient>
    <radialGradient id="hero-spec" cx="50%" cy="50%" r="50%">
      <stop offset="0" stop-color="#fff" stop-opacity="1"/>
      <stop offset="0.35" stop-color="#fff" stop-opacity="0.75"/>
      <stop offset="1" stop-color="#fff" stop-opacity="0"/>
    </radialGradient>
    <filter id="hero-liquid" x="-50%" y="-50%" width="200%" height="200%" color-interpolation-filters="sRGB">
      <feTurbulence id="hero-turb" type="fractalNoise" baseFrequency="0.011" numOctaves="2" seed="${seed}" result="noise"/>
      <feOffset id="hero-drift" in="noise" dx="0" dy="0" result="moved"/>
      <feDisplacementMap in="SourceGraphic" in2="moved" scale="95" xChannelSelector="R" yChannelSelector="G"/>
    </filter>
    <clipPath id="hero-clip"><circle cx="180" cy="180" r="179"/></clipPath>
  </defs>
  <g clip-path="url(#hero-clip)">
    <rect width="360" height="360" fill="#b9c1c5"/>
    <g filter="url(#hero-liquid)">
      <g id="hero-flow">
        <rect x="-120" y="-120" width="600" height="340" fill="url(#hero-sky)"/>
        <ellipse cx="180" cy="198" rx="300" ry="36" fill="#262e32"/>
        <ellipse cx="180" cy="300" rx="300" ry="96" fill="url(#hero-floor)"/>
        <ellipse cx="180" cy="232" rx="300" ry="16" fill="#5b666b"/>
        <ellipse cx="96" cy="120" rx="64" ry="12" fill="#6f7a7f" transform="rotate(-18 96 120)"/>
        <ellipse cx="262" cy="96" rx="58" ry="10" fill="#7d878c" transform="rotate(14 262 96)"/>
        <ellipse cx="180" cy="420" rx="260" ry="80" fill="var(--accent)" opacity="0.55"/>
      </g>
    </g>
    <circle cx="180" cy="180" r="180" fill="url(#hero-shade)"/>
    <circle cx="180" cy="180" r="180" fill="url(#hero-rim)"/>
    <ellipse cx="122" cy="92" rx="52" ry="30" fill="url(#hero-spec)" transform="rotate(-32 122 92)"/>
    <ellipse cx="262" cy="270" rx="14" ry="8" fill="url(#hero-spec)" opacity="0.7" transform="rotate(-40 262 270)"/>
  </g>
  <circle cx="180" cy="180" r="179" fill="none" stroke="rgba(255,255,255,0.45)" stroke-width="1.5"/>
</svg>`;
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

export function buildHero(kind, doc) {
  const size = HERO_SIZE[kind] || HERO_SIZE.orb;
  const node = el("div", `hero ${kind}`);
  node.style.width = `${size.w}px`;
  node.style.height = `${size.h}px`;
  const inner = el("div", "hero-inner", node);
  inner.style.position = "relative";
  inner.style.width = "100%";
  inner.style.height = "100%";
  const ui = { node, inner, kind, size, rings: [], zs: [], text: null };
  // the rings sit behind the object
  // round the orb; round the whole object for the screens (their screens are opaque, so a ring only shows outside)
  const ringD = kind === "orb" ? size.w : Math.max(size.w, size.h) * 0.8;
  for (let i = 0; i < 3; i++) {
    const ring = el("div", "ring", inner);
    Object.assign(ring.style, { width: `${ringD}px`, height: `${ringD}px`, left: `${(size.w - ringD) / 2}px`, top: `${(size.h - ringD) / 2}px` });
    ui.rings.push(ring);
  }
  if (kind === "orb") {
    el("div", "orbglow", inner);
    const art = el("div", null, inner);
    art.style.position = "relative";
    art.innerHTML = orbSvg(seedOf(JSON.stringify(doc.look || {})) % 1000);
    ui.turb = art.querySelector("#hero-turb");
    ui.flow = art.querySelector("#hero-flow");
    ui.drift = art.querySelector("#hero-drift");
    ui.label = el("div", "orblabel", inner);
  } else {
    const box = SCREEN[kind];
    const glow = el("div", "screenglow", inner);
    Object.assign(glow.style, { left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px` });
    const art = el("div", null, inner);
    art.style.position = "relative";
    art.innerHTML = kind === "laptop" ? laptopSvg() : monitorSvg();
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
  if (ui.kind === "orb") return;
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

/** `h` is heroAt(states, t); main.mjs places, scales and fades the node itself. */
export function updateHero(node, h, t, doc) {
  const ui = node._hero;
  if (!ui || !h) return;
  // pulse and shake move the inner box; the outer one is main's
  const pulse = amount(h, "pulse");
  const scale = 1 + 0.04 * pulse * Math.sin((2 * Math.PI * PULSE_HZ * t) / 1000);
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
  if (ui.kind === "orb") ui.label.style.transform = `translate(-50%, -50%) scale(${(0.8 + 0.2 * screen).toFixed(3)})`;
  if (ui.glow) ui.glow.style.opacity = screen.toFixed(3);

  if (ui.turb) {
    // the liquid swirl: the noise field drifts and breathes, and the reflected world sways under it — all
    // smooth functions of t, so any frame can be drawn on its own
    const f = 0.0105 + 0.0015 * Math.sin((2 * Math.PI * t) / 5200);
    const g = 0.0110 + 0.0012 * Math.cos((2 * Math.PI * t) / 6900);
    ui.turb.setAttribute("baseFrequency", `${f.toFixed(5)} ${g.toFixed(5)}`);
    ui.drift.setAttribute("dx", (70 * Math.sin((2 * Math.PI * t) / 7000)).toFixed(2));
    ui.drift.setAttribute("dy", (50 * Math.cos((2 * Math.PI * t) / 9000)).toFixed(2));
    const tilt = 7 * Math.sin((2 * Math.PI * t) / 6100);
    const bob = 10 * Math.sin((2 * Math.PI * t) / 4300);
    ui.flow.setAttribute("transform", `rotate(${tilt.toFixed(2)} 180 180) translate(0 ${bob.toFixed(2)})`);
  }
}
