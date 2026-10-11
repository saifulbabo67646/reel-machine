"""explainer3d plan 3: components the agent writes as code (opt-in)."""

from __future__ import annotations

import dataclasses
import shutil

import pytest

from reelmachine.config import get_settings
from reelmachine.core.errors import InvalidInput
from reelmachine.recipes.explainer3d import components
from reelmachine.recipes.explainer3d.models import Explainer3DInputs
from reelmachine.recipes.explainer3d.validate import resolve_script

node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

GAUGE = """
export function create({ THREE, params, rng }) {
  const group = new THREE.Group();
  const dial = new THREE.Mesh(new THREE.CircleGeometry(params.radius, 48), new THREE.MeshStandardMaterial({ color: params.colour }));
  const needle = new THREE.Mesh(new THREE.BoxGeometry(params.radius * 0.9, 0.01, 0.01), new THREE.MeshStandardMaterial({ color: 0xff0000 }));
  needle.geometry.translate(params.radius * 0.45, 0, 0.01);
  group.add(dial, needle);
  const wobble = rng() * 0.1;
  return {
    object: group,
    update(t) { needle.rotation.z = Math.PI - Math.min(t / 3, 1) * Math.PI + Math.sin(t * 9) * wobble; },
  };
}
"""


def _script(**over):
    base = {
        "look": "cinematic", "set": {"preset": "studio"},
        "components": {"gauge": {"code": GAUGE}},
        "cast": {"gauge": {"asset": {"component": "gauge", "size": [0.4, 0.4, 0.05], "params": {"radius": 0.2, "colour": "#ffffff"}},
                           "at": "stage.center"}},
        "shots": [{"narration": "Pressure rises.", "camera": {"move": "orbit", "target": "gauge"},
                   "effects": [{"component": "gauge", "at": [0.2, 0.9], "params": {"radius": 0.1, "colour": "#00ff00"}}]}],
    }
    base.update(over)
    return base


def test_lint_names_the_forbidden_call_and_its_line():
    assert components.lint(GAUGE) == []
    bad = "export function create({ THREE }) {\n  const t = Date.now();\n  fetch('https://x');\n  return {};\n}\n"
    found = components.lint(bad)
    assert ("Date", 2) in found and ("fetch", 3) in found
    for snippet in ["import x from 'y'", "require('fs')", "new Function('x')", "eval('1')", "Math.random()",
                    "setTimeout(f, 1)", "globalThis.x", "process.exit()", "a.constructor", "performance.now()"]:
        assert components.lint(f"export function create() {{ {snippet}; }}"), snippet
    assert components.lint("function make() {}")  # must export create


@node
def test_a_clean_component_passes_the_dry_run():
    result = components.dry_run(GAUGE, {"radius": 0.2, "colour": "#ffffff"}, seed=7, seconds=3.0)
    assert result.ok, result.error


@node
def test_the_dry_run_reports_errors_and_frames_that_depend_on_order():
    thrown = components.dry_run("export function create() { return { object: null, update(t) { if (t > 1) throw new Error('boom at ' + t); } }; }",
                                {}, seed=1, seconds=2.0)
    assert not thrown.ok and "boom" in thrown.error
    stateful = """export function create({ THREE }) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1));
  let calls = 0;
  return { object: m, update(t) { calls += 1; m.position.x = calls; } };
}"""
    drift = components.dry_run(stateful, {}, seed=1, seconds=2.0)
    assert not drift.ok and "same frame" in drift.error
    looping = components.dry_run("export function create() { while (true) {} }", {}, seed=1, seconds=1.0)
    assert not looping.ok and "too long" in looping.error


def test_components_are_off_unless_the_deployment_allows_them(monkeypatch):
    inputs = Explainer3DInputs.model_validate(_script())
    off = dataclasses.replace(get_settings(), explainer3d_custom_code=False)
    with pytest.raises(InvalidInput) as info:
        components.check(inputs, off)
    assert info.value.details["path"] == "components" and "REEL_EXPLAINER3D_CUSTOM_CODE" in info.value.hint


@node
def test_checked_components_name_the_component_that_failed():
    on = dataclasses.replace(get_settings(), explainer3d_custom_code=True)
    components.check(Explainer3DInputs.model_validate(_script()), on)
    broken = _script(components={"gauge": {"code": GAUGE.replace("Math.PI", "Math.PI * Date.now()")}})
    with pytest.raises(InvalidInput) as info:
        components.check(Explainer3DInputs.model_validate(broken), on)
    assert info.value.details["path"] == "components.gauge.code"


def test_component_references_are_checked():
    resolve_script(Explainer3DInputs.model_validate(_script()))
    with pytest.raises(InvalidInput) as info:
        resolve_script(Explainer3DInputs.model_validate(_script(shots=[{"narration": "x", "camera": {"move": "orbit"},
                                                                          "effects": [{"component": "laser"}]}])))
    assert info.value.details["path"] == "shots[0].effects[0].component"


@node
def test_a_string_built_escape_does_not_reach_the_host():
    # the lint cannot see through string concatenation; the isolated context must hold anyway
    sneaky = """export function create() {
  const F = (() => 0)["con" + "struc" + "tor"];
  const g = F("return this")();
  if (g.process || g.require) throw new Error("escaped to the host");
  return {};
}"""
    result = components.dry_run(sneaky, {}, seed=1, seconds=1.0)
    assert result.ok, result.error
