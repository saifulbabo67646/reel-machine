import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "../vendor/three.module.js";
import { buildShape, innerRadius, KINDS } from "../shapes.mjs";

test("every shape's bounding box is its declared size", () => {
  for (const kind of KINDS) {
    const dims = [0.4, 0.3, 0.4];
    const { group } = buildShape(THREE, { shape: kind, material: "stone", colour: null }, dims);
    const size = new THREE.Box3().setFromObject(group).getSize(new THREE.Vector3());
    assert.ok(Math.abs(size.x - 0.4) < 0.01, `${kind} width ${size.x}`);
    assert.ok(Math.abs(size.y - 0.3) < 0.01, `${kind} height ${size.y}`);
  }
});

test("containers have an inside narrower than the outside", () => {
  for (const kind of ["bowl", "cup", "pot", "jar"]) {
    const r = innerRadius(kind, 0.4, 0.3, 0.2);
    assert.ok(r > 0 && r < 0.2, `${kind} ${r}`);
  }
});
