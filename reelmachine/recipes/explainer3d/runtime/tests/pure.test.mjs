import test from "node:test";
import assert from "node:assert/strict";
import { easeOutBack, mixHex, mulberry32, spring } from "../math.mjs";
import { applyAction, stateAt } from "../poses.mjs";
import { cameraPose, ease } from "../camera.mjs";
import { grains, steam, stream } from "../particles.mjs";
import { captionState } from "../captions.mjs";

const ctx = { anchors: { "stage.center": [0, 0.9, 0] }, dims: { bowl: [0.44, 0.2, 0.44], b: [0.1, 0.1, 0.1] }, homes: { b: [0.3, 0.9, 0] }, colours: { b: "#d9b44a" } };
const world = () => ({
  bowl: { position: [0, 0.9, 0], visible: true, rotationY: 0, scale: 1, fill: { level: 0, colour: "#d9d27a", surface: "liquid" } },
  b: { position: [0.3, 0.9, 0], visible: false, rotationY: 0, scale: 1, fill: null },
});

test("springs and eases hit their ends exactly", () => {
  assert.equal(spring(0), 0);
  assert.equal(spring(1), 1);
  assert.ok(Math.max(...Array.from({ length: 101 }, (_, i) => easeOutBack(i / 100))) <= 1.05);
  assert.equal(ease(1, true, true), 1);
});

test("the prng is seeded", () => {
  const a = mulberry32(42), b = mulberry32(42);
  assert.deepEqual([a(), a(), a()], [b(), b(), b()]);
});

test("mixHex rounds like Python", () => assert.equal(mixHex("#000000", "#ffffff", 0.5), "#808080"));

test("moves are deterministic and start where the world is", () => {
  const action = { move: "fly_in", actor: "b", params: { from: "sky", to: null }, t0: 0, t1: 1, seed: 1 };
  assert.deepEqual(applyAction(world(), action, ctx, 0.37), applyAction(world(), action, ctx, 0.37));
  const start = applyAction(world(), action, ctx, 0).world.b.position;
  assert.deepEqual(start, [0.3, 3.9, 0]);
});

test("stateAt applies earlier actions to their end", () => {
  const shot = { world: world(), ctx, actions: [
    { move: "fly_in", actor: "b", params: { from: "sky", to: null }, t0: 0, t1: 1, seed: 1 },
    { move: "drop_into", actor: "b", params: { into: "bowl", fill_add: 0.15, colour: null }, t0: 1, t1: 2, seed: 2 },
  ] };
  const done = stateAt(shot, 5).world;
  assert.equal(done.b.visible, false);
  assert.ok(Math.abs(done.bowl.fill.level - 0.15) < 1e-9);
  assert.equal(stateAt(shot, 0.5).world.bowl.fill.level, 0);
});

test("particles are pure in t", () => {
  assert.deepEqual(stream(3, [0, 1, 0], [0, 0, 0], 0.4), stream(3, [0, 1, 0], [0, 0, 0], 0.4));
  assert.deepEqual(grains(3, [0, 1, 0], 0.5, 0.4), grains(3, [0, 1, 0], 0.5, 0.4));
  assert.equal(steam(3, [0, 1, 0], 0.4, 1).length, 40);
});

test("orbit keeps its radius", () => {
  const start = { position: [0, 1, 2], target: [0, 0, 0], fov: 40 };
  const end = cameraPose("orbit", { to: 90 }, start, [0, 0, 0], 1);
  assert.ok(Math.abs(Math.hypot(end.position[0], end.position[2]) - 2) < 1e-9);
});

test("captions follow the words", () => {
  const words = [{ start: 0.25, end: 0.5 }, { start: 0.6, end: 1.0 }];
  assert.equal(captionState(words, 0.1).active, -1);
  assert.equal(captionState(words, 0.7).active, 1);
  assert.equal(captionState(words, 2.0).speaking, false);
});

test("a pour tips the vessel toward the container and streams from above it", () => {
  const w = world();
  w.bottle = { position: [-0.45, 0.9, 0], visible: true, rotationY: 0, scale: 1, fill: null };
  const c = { ...ctx, dims: { ...ctx.dims, bottle: [0.09, 0.28, 0.09] } };
  const out = applyAction(w, { move: "pour", actor: "bottle", params: { into: "bowl", fill_add: 0.2, colour: "#f2efe6" }, t0: 0, t1: 2, seed: 3 }, c, 0.5);
  const tilt = out.fx.find((e) => e.type === "tilt");
  const stream = out.fx.find((e) => e.type === "stream");
  assert.ok(tilt.angle < 0, "a vessel left of the bowl must tip clockwise (neck toward +x)");
  assert.ok(Math.abs(stream.from[0] - 0) < 0.22, `stream starts over the bowl, not at x=${stream.from[0]}`);
  assert.ok(stream.from[1] > 0.9 + 0.2, "and above its rim");
});
