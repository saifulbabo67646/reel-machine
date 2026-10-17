// reelmachine/recipes/mapreel/runtime/main.mjs
// Boots a map segment: load its images, then draw whatever time HyperFrames seeks to.
// HyperFrames waits on window.__hf.buildReady (set inline in index.html) before capturing.
import { drawFrame } from "./draw.mjs";

const doc = JSON.parse(document.getElementById("segment-data").textContent);
const done = () => window.__mapResolve && window.__mapResolve();

function load(url) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve([url, img]);
    img.onerror = () => resolve([url, null]); // a missing image draws as nothing, not a crash
    img.src = url;
  });
}

try {
  const urls = [...doc.layers.map((l) => l.url), ...doc.items.filter((i) => i.image).map((i) => i.image)];
  const images = new Map((await Promise.all(urls.map(load))).filter(([, img]) => img));
  const canvas = document.getElementById("map");
  const ctx = canvas.getContext("2d");
  const renderAt = (t) => drawFrame(ctx, doc, images, t);
  window.addEventListener("hf-seek", (event) => renderAt(event.detail.time));
  window.__mapRenderAt = renderAt;
  renderAt(window.__hfThreeTime || 0);
} catch (error) {
  window.__mapError = String((error && error.stack) || error);
  console.error("mapreel: the segment could not be built", error);
} finally {
  done();
}
