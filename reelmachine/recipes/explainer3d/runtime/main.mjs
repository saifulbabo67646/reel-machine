// The shot: pick the renderer its look asks for, build it, then draw whatever time
// HyperFrames seeks to. Building may load models and sky images; HyperFrames waits on
// window.__hf.buildReady (set inline in index.html) before it captures a frame.
const shot = JSON.parse(document.getElementById("shot-data").textContent);
const done = () => window.__e3dResolve && window.__e3dResolve();
try {
  const renderAt = shot.look.renderer === "cinematic"
    ? await (await import("./cinematic.mjs")).startCinematic(shot)
    : (await import("./classic.mjs")).startClassic(shot);
  window.addEventListener("hf-seek", (event) => renderAt(event.detail.time));
  window.__e3dRenderAt = renderAt;
  renderAt(window.__hfThreeTime || 0);
} catch (error) {
  window.__e3dError = String((error && error.stack) || error);
  console.error("explainer3d: the shot could not be built", error);
} finally {
  done();
}
