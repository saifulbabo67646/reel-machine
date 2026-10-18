// reelmachine/recipes/mapreel/runtime/main.mjs
// Boots a map segment: load its images, then draw whatever time HyperFrames seeks to.
// HyperFrames waits on window.__hf.buildReady (set inline in index.html) before capturing.
const done = () => window.__mapResolve && window.__mapResolve();

function load(url) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve([url, img]);
    img.onerror = () => resolve([url, null]); // reported below for layers; item images become text badges
    img.src = url;
  });
}

try {
  const doc = JSON.parse(document.getElementById("segment-data").textContent);
  const { drawFrame } = await import("./draw.mjs");
  const layerUrls = [...doc.layers, ...(doc.panels || []).flatMap((p) => p.layers)].map((l) => l.url);
  const itemUrls = doc.items.filter((i) => i.image).map((i) => i.image);
  const loaded = await Promise.all([...layerUrls, ...itemUrls].map(load));
  const images = new Map(loaded.filter(([, img]) => img));
  const missing = layerUrls.filter((url) => !images.has(url));
  if (missing.length) {
    for (const url of missing) console.error(`mapreel: layer ${url} failed to load`);
    window.__mapError = missing.map((url) => `mapreel: layer ${url} failed to load`).join("; ");
  }
  const canvas = document.getElementById("map");
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  const renderAt = (t) => drawFrame(ctx, doc, images, t);
  window.addEventListener("hf-seek", (event) => renderAt(event.detail.time));
  window.__mapRenderAt = renderAt;
  renderAt(window.__hfThreeTime || 0);
} catch (error) {
  window.__mapError = String((error && error.stack) || error);
  console.error("mapreel: the segment could not be built", error);
} finally {
  // HyperFrames polls window.__hfStartupError before every capture and fails the render with its text,
  // so a map that could not be drawn fails the job instead of shipping a blank segment
  if (window.__mapError) window.__hfStartupError = window.__mapError;
  done();
}
