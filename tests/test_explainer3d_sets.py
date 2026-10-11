"""explainer3d plan 3: sets are data — presets, overrides, marks, packs (offline)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reelmachine.core.errors import InvalidInput
from reelmachine.recipes.explainer3d import assets
from reelmachine.recipes.explainer3d.models import Explainer3DInputs
from reelmachine.recipes.explainer3d.sets import PRESETS, resolve_set
from reelmachine.recipes.explainer3d.state import build_ctx
from reelmachine.recipes.explainer3d.validate import resolve_script


def _script(**over):
    base = {
        "look": "cinematic",
        "set": {"preset": "kitchen"},
        "cast": {"bowl": {"asset": {"shape": "bowl", "material": "wood"}, "at": "stage.center"}},
        "shots": [{"narration": "A bowl.", "camera": {"move": "orbit", "target": "bowl"}}],
    }
    base.update(over)
    return base


def test_presets_ship_and_resolve():
    assert {"lake", "underwater", "kitchen", "living-room", "workshop", "studio", "cafe", "meadow-day"} <= set(PRESETS)
    kitchen = resolve_set({"preset": "kitchen"})
    assert kitchen.name == "kitchen" and kitchen.sky.startswith("polyhaven:") and kitchen.stage == "counter"
    assert resolve_set({"preset": "underwater"}).mode == "underwater"


def test_a_preset_can_be_extended_and_the_old_environment_name_still_works():
    base = resolve_set({"preset": "kitchen"})
    more = resolve_set({"preset": "kitchen", "props": [{"model": "polyhaven:jug_01", "at": [0.4, 0.9, -0.2]}],
                        "marks": {"sink": [1.0, 0.9, -0.5]}, "stage": "table"})
    assert len(more.props) == len(base.props) + 1 and more.marks["sink"] == (1.0, 0.9, -0.5) and more.stage == "table"
    legacy = resolve_set({"environment": "lake", "stage": "stone-slab"})
    assert legacy.name == "lake" and legacy.water is not None


def test_a_set_can_be_built_from_nothing():
    custom = resolve_set({"sky": "polyhaven:comfy_cafe", "floor": {"texture": "polyhaven:laminate_floor"},
                          "walls": {"colour": "#e8dccb", "sides": ["back"]}, "stage": "table"})
    assert custom.name == "custom" and custom.walls.sides == ["back"] and custom.mode == "normal"


def test_unknown_presets_and_bad_sources_are_refused():
    with pytest.raises(InvalidInput) as info:
        resolve_script(Explainer3DInputs.model_validate(_script(set={"preset": "moonbase"})))
    assert info.value.details["path"] == "set.preset"
    with pytest.raises(ValidationError):
        Explainer3DInputs.model_validate(_script(set={"sky": "hdrihaven:x"}))


def test_shot_sets_inherit_the_reel_set_unless_they_name_a_preset():
    inputs = Explainer3DInputs.model_validate(_script(shots=[
        {"narration": "a", "camera": {"move": "orbit", "target": "bowl"}},
        {"narration": "b", "set": {"fog": {"colour": "#ffffff", "density": 0.05}}, "camera": {"move": "orbit", "target": "bowl"}},
        {"narration": "c", "set": {"environment": "underwater"}, "camera": {"move": "orbit", "target": "underwater.center"}},
    ]))
    shots = resolve_script(inputs)
    sets = {s.id: inputs_sets for s, inputs_sets in zip(shots, [s.set_key for s in shots])}
    assert len(set(sets.values())) == 3
    assert shots[1].environment == "kitchen" and shots[2].environment == "underwater"


def test_marks_are_anchors():
    inputs = Explainer3DInputs.model_validate(_script(
        set={"preset": "kitchen", "marks": {"sink": [1.0, 0.9, -0.5]}},
        cast={"bowl": {"asset": {"shape": "bowl"}, "at": "sink"}},
        shots=[{"narration": "x", "camera": {"move": "orbit", "target": "sink"}}]))
    resolve_script(inputs)
    assert build_ctx(inputs)["anchors"]["sink"] == [1.0, 0.9, -0.5]
    bad = Explainer3DInputs.model_validate(_script(shots=[{"narration": "x", "camera": {"move": "orbit", "target": "stove"}}]))
    with pytest.raises(InvalidInput):
        resolve_script(bad)


def test_counter_and_table_stages_have_their_own_height():
    inputs = Explainer3DInputs.model_validate(_script(set={"preset": "living-room"}))
    assert build_ctx(inputs)["anchors"]["stage.center"][1] == pytest.approx(0.75)  # a table


def test_set_packs_fetch_sky_textures_and_props(tmp_path, monkeypatch):
    def touch(p):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return p

    def fake_model(source, root, *, path="", res="1k"):
        folder = touch(tmp_path / source.replace(":", "_") / "m.gltf").parent
        return assets.ModelFile(source=source, root=folder, main="m.gltf", bounds=([-0.1, 0, -0.1], [0.1, 0.2, 0.1]),
                                licence=assets.POLYHAVEN_LICENCE, provenance=assets.Provenance(provider="polyhaven", source=source),
                                files=("m.gltf",))
    monkeypatch.setattr(assets, "fetch_model", fake_model)
    monkeypatch.setattr(assets, "fetch_hdri", lambda ident, root, res="4k": touch(tmp_path / f"{ident}.hdr"))
    monkeypatch.setattr(assets, "fetch_texture", lambda ident, root, res="2k": {
        n: touch(tmp_path / ident / f"{n}.jpg") for n in ("diff", "nor", "rough")})
    monkeypatch.setattr(assets, "fetch_url", lambda url, dest: touch(dest))
    kitchen = resolve_set({"preset": "kitchen"})
    pack = assets.set_pack(kitchen, tmp_path)
    assert "sky.hdr" in pack.files and any(k.startswith("tex/") for k in pack.files)
    assert len(pack.dressing) == len(kitchen.props)
    assert assets.set_pack(resolve_set({"preset": "meadow-day"}), tmp_path) is None  # a classic set needs nothing


def test_find_assets_covers_skies_and_textures(tmp_path, monkeypatch):
    import httpx

    def handler(request):
        kind = request.url.params.get("t")
        rows = {"hdris": {"comfy_cafe": {"name": "Comfy Cafe", "tags": ["cafe", "indoor"]}},
                "textures": {"laminate_floor": {"name": "Laminate Floor", "tags": ["wood", "floor"]}},
                "models": {}}[kind]
        return httpx.Response(200, json=rows)
    monkeypatch.setattr(assets, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    assert assets.find_assets("cafe", tmp_path, kind="sky")[0]["source"] == "polyhaven:comfy_cafe"
    assert assets.find_assets("wood floor", tmp_path, kind="texture")[0]["source"] == "polyhaven:laminate_floor"

    import dataclasses

    from reelmachine.config import get_settings
    from reelmachine.engine import Engine
    from reelmachine.mcp.tenants import TenantRegistry
    from reelmachine.mcp.tools import McpTools

    engine = Engine(dataclasses.replace(get_settings(), workdir=tmp_path / "w"))
    try:
        result = McpTools(engine=engine, tenants=TenantRegistry()).find_assets(None, "cafe", kind="sky")
    finally:
        engine.close()
    assert result["assets"][0]["source"] == "polyhaven:comfy_cafe" and "sky" in result["hint"]
