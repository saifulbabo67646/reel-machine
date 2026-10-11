// Dry-run an agent-written component in an isolated context.
// stdin: {threeCore, threeModule, code, params, seed, times}. stdout: {ok, error?}.
// three.js and the component are loaded *inside* a fresh vm context, so no object from this
// process reaches the component; every call into it has a time limit. The frames at `times`
// are computed twice — forwards from one instance, backwards from another — and must match:
// a frame may depend on t alone.
import vm from "node:vm";
import { readFileSync } from "node:fs";

const input = JSON.parse(readFileSync(0, "utf8"));
const out = (o) => process.stdout.write(JSON.stringify(o));
const LIMIT_MS = 4000;

const RUNNER = `
import * as THREE from "three";
import { create } from "component";

function rng(seed) {
  let a = seed >>> 0;
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
function round(x) { return Math.round(x * 1e5) / 1e5; }
function stateOf(object) {
  const rows = [];
  if (!object || !object.traverse) return rows;
  object.updateMatrixWorld(true);
  object.traverse((o) => {
    const row = [o.visible, ...o.matrixWorld.elements.map(round)];
    const m = o.material;
    if (m && m.color) row.push(m.color.getHexString(), round(m.opacity ?? 1));
    const p = o.geometry && o.geometry.attributes && o.geometry.attributes.position;
    if (p) { let s = 0; for (let i = 0; i < p.array.length; i += 7) s += p.array[i]; row.push(p.count, round(s)); }
    rows.push(row);
  });
  return rows;
}
function make(json) {
  const { params, seed, size } = JSON.parse(json);
  const made = create({ THREE, params, rng: rng(seed), size }) || {};
  return made;
}
let a = null, b = null;
globalThis.__make = (json) => { a = make(json); b = make(json); return "ok"; };
globalThis.__frame = (which, t) => {
  const inst = which === "a" ? a : b;
  if (inst.update) inst.update(t, { t, p: 0 });
  return JSON.stringify(stateOf(inst.object));
};
`;

try {
  const context = vm.createContext({});
  const mods = {
    "./three.core.js": new vm.SourceTextModule(input.threeCore, { context, identifier: "three.core.js" }),
    three: new vm.SourceTextModule(input.threeModule, { context, identifier: "three.module.js" }),
    component: new vm.SourceTextModule(input.code, { context, identifier: "component" }),
  };
  const runner = new vm.SourceTextModule(RUNNER, { context, identifier: "runner" });
  await runner.link((specifier) => {
    if (!(specifier in mods)) throw new Error(`components may not import (${specifier})`);
    return mods[specifier];
  });
  await runner.evaluate({ timeout: LIMIT_MS });
  const call = (src) => vm.runInContext(src, context, { timeout: LIMIT_MS });
  call(`__make(${JSON.stringify(JSON.stringify({ params: input.params, seed: input.seed, size: input.size }))})`);
  const forward = input.times.map((t) => call(`__frame("a", ${t})`));
  const backward = [...input.times].reverse().map((t) => call(`__frame("b", ${t})`)).reverse();
  const bad = input.times.filter((t, i) => forward[i] !== backward[i]);
  if (bad.length) {
    out({ ok: false, error: `not the same frame when drawn in a different order at t = ${bad.join(", ")} s — `
      + "a frame must depend on t alone (no counters, no state carried between updates)" });
  } else out({ ok: true });
} catch (error) {
  const message = String((error && error.message) || error);
  out({ ok: false, error: /timed out/i.test(message) ? `took too long (over ${LIMIT_MS / 1000} s) — a loop that never ends?` : message });
}
