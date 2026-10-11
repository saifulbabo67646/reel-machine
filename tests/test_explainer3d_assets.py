"""explainer3d plan 2: model catalogue, fetching and measuring — offline (mock transport)."""

from __future__ import annotations

import json
import struct

import httpx
import pytest

from reelmachine.core.errors import InvalidInput
from reelmachine.recipes.explainer3d import assets
from reelmachine.recipes.explainer3d.models import ModelSpec


def _gltf(min_, max_, node=None):
    nodes = [{"mesh": 0, **(node or {})}]
    return {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nodes,
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [{"type": "VEC3", "componentType": 5126, "count": 8, "min": min_, "max": max_}],
        "buffers": [{"uri": "m.bin", "byteLength": 0}],
    }


def test_bounds_apply_node_transforms():
    doc = _gltf([-1, 0, -1], [1, 2, 1], {"scale": [0.5, 0.5, 0.5], "translation": [0, 1, 0]})
    lo, hi = assets.gltf_bounds(doc)
    assert lo == pytest.approx([-0.5, 1.0, -0.5]) and hi == pytest.approx([0.5, 2.0, 0.5])


def test_bounds_read_a_glb_json_chunk(tmp_path):
    body = json.dumps(_gltf([0, 0, -0.3], [0.1, 0.3, 0.3])).encode()
    body += b" " * (-len(body) % 4)
    glb = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(body)) + struct.pack("<II", len(body), 0x4E4F534A) + body
    path = tmp_path / "fish.glb"
    path.write_bytes(glb)
    lo, hi = assets.gltf_bounds(assets.read_gltf_json(path))
    assert hi[2] - lo[2] == pytest.approx(0.6)


def test_model_dims_scale_to_height_or_longest_side():
    lo, hi = [-0.1, 0, -0.1], [0.1, 0.1, 0.1]  # 0.2 x 0.1 x 0.2
    assert assets.model_dims(ModelSpec(model="polyhaven:x", height=0.05), lo, hi) == pytest.approx((0.1, 0.05, 0.1))
    assert assets.model_dims(ModelSpec(model="polyhaven:x", size=0.4), lo, hi) == pytest.approx((0.4, 0.2, 0.4))
    assert assets.model_dims(ModelSpec(model="polyhaven:x"), lo, hi) == pytest.approx((0.3, 0.15, 0.3))


def _polyhaven(calls):
    gltf = json.dumps(_gltf([-0.1, 0, -0.1], [0.1, 0.08, 0.1])).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers.get("user-agent", "").startswith("reel-machine")
        if request.url.path == "/files/wooden_bowl_01":
            return httpx.Response(200, json={"gltf": {"1k": {"gltf": {
                "url": "https://dl.test/bowl/wooden_bowl_01_1k.gltf", "size": len(gltf),
                "include": {"wooden_bowl_01.bin": {"url": "https://dl.test/bowl/b.bin", "size": 3},
                            "textures/diff.jpg": {"url": "https://dl.test/bowl/d.jpg", "size": 3}}}}}})
        if request.url.path == "/files/nope":
            return httpx.Response(404, json={"error": "not found"})
        if request.url.path == "/assets":
            return httpx.Response(200, json={
                "wooden_bowl_01": {"name": "Wooden Bowl 01", "tags": ["bowl", "kitchen"], "categories": ["props"]},
                "rock_moss_set_01": {"name": "Rock Moss Set", "tags": ["rock", "moss"], "categories": ["nature"]}})
        if request.url.path.endswith(".gltf"):
            return httpx.Response(200, content=gltf)
        return httpx.Response(200, content=b"abc")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_downloads_once_and_measures(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(assets, "http_client", lambda: _polyhaven(calls))
    first = assets.fetch_model("polyhaven:wooden_bowl_01", tmp_path)
    assert (first.root / first.main).is_file() and (first.root / "textures/diff.jpg").is_file()
    assert first.bounds[1][1] == pytest.approx(0.08) and first.licence.name == "CC0-1.0"
    n = len(calls)
    again = assets.fetch_model("polyhaven:wooden_bowl_01", tmp_path)
    assert len(calls) == n and again.main == first.main  # cached: no network


def test_an_unknown_model_is_refused_with_the_field(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "http_client", lambda: _polyhaven([]))
    with pytest.raises(InvalidInput) as info:
        assets.fetch_model("polyhaven:nope", tmp_path, path="cast.x.asset.model")
    assert info.value.details["path"] == "cast.x.asset.model"


def test_find_models_searches_names_and_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "http_client", lambda: _polyhaven([]))
    hits = assets.find_models("moss rock", tmp_path)
    assert hits[0]["source"] == "polyhaven:rock_moss_set_01" and hits[0]["licence"] == "CC0-1.0"
    fish = assets.find_models("fish", tmp_path)
    assert fish[0]["source"] == "khronos:BarramundiFish"


# ---------------------------------------------------------------- the cast and the sets
from reelmachine.core.assets import Provenance  # noqa: E402
from reelmachine.recipes.explainer3d.models import Explainer3DInputs  # noqa: E402
from reelmachine.recipes.explainer3d.state import build_ctx, initial_world  # noqa: E402


def _fake_fetch(tmp_path):
    def fetch(source, cache_root, *, path="", res="1k"):
        root = tmp_path / "models" / source.replace(":", "_")
        root.mkdir(parents=True, exist_ok=True)
        (root / "m.gltf").write_text("{}")
        return assets.ModelFile(source=source, root=root, main="m.gltf", bounds=([-0.1, 0, -0.1], [0.1, 0.1, 0.1]),
                                licence=assets.POLYHAVEN_LICENCE, provenance=Provenance(provider="polyhaven", source=source),
                                files=("m.gltf",))
    return fetch


def test_cast_models_resolve_with_measured_dims_and_licences(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "fetch_model", _fake_fetch(tmp_path))
    inputs = Explainer3DInputs.model_validate({
        "cast": {"bowl": {"asset": {"model": "polyhaven:wooden_bowl_01", "size": 0.24, "container": True}, "at": "stage.center"},
                 "ball": {"asset": {"shape": "ball", "height": 0.05}}},
        "shots": [{"narration": "x", "camera": {"move": "orbit", "target": "bowl"}}]})
    models = assets.resolve_cast(inputs, tmp_path)
    assert models["bowl"].kind == "model" and models["bowl"].dims == pytest.approx((0.24, 0.12, 0.24))
    assert models["bowl"].licence.name == "CC0-1.0" and models["ball"].kind == "shape"
    ctx = build_ctx(inputs, models)
    assert ctx["dims"]["bowl"] == pytest.approx([0.24, 0.12, 0.24])
    world = initial_world(inputs, models)
    assert world["bowl"]["fill"]["level"] == 0.0  # a container model gets a fill


def test_cinematic_sets_fetch_their_pack(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "fetch_model", _fake_fetch(tmp_path))
    monkeypatch.setattr(assets, "fetch_hdri", lambda ident, root, res="4k": _touch(tmp_path / f"{ident}.hdr"))
    monkeypatch.setattr(assets, "fetch_texture", lambda ident, root, res="2k": {
        n: _touch(tmp_path / ident / f"{n}.jpg") for n in ("diff", "nor", "rough")})
    monkeypatch.setattr(assets, "fetch_url", lambda url, dest: _touch(dest))
    packs = assets.environment_packs(["lake", "underwater", "meadow-day"], tmp_path)
    assert set(packs) == {"lake", "underwater"}
    lake = packs["lake"]
    assert lake.files["sky.hdr"].is_file() and "rock/diff.jpg" in lake.files and lake.dressing
    assert all(lic.name in {"CC0-1.0", "MIT"} for lic in lake.licences)


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_shot_projects_hard_link_cached_assets(tmp_path):
    from reelmachine.recipes.explainer3d.compile import write_project

    big = _touch(tmp_path / "cache" / "sky.hdr")
    out = write_project({"renderDuration": 1.0}, tmp_path / "shot", links={"assets/env/lake/sky.hdr": big})
    linked = out / "assets/env/lake/sky.hdr"
    assert linked.is_file() and linked.stat().st_ino == big.stat().st_ino


def test_mcp_find_models_answers_from_the_catalogue(tmp_path, monkeypatch):
    import dataclasses

    from reelmachine.config import get_settings
    from reelmachine.engine import Engine
    from reelmachine.mcp.tenants import TenantRegistry
    from reelmachine.mcp.tools import McpTools

    monkeypatch.setattr(assets, "http_client", lambda: _polyhaven([]))
    engine = Engine(dataclasses.replace(get_settings(), workdir=tmp_path / "w"))
    try:
        result = McpTools(engine=engine, tenants=TenantRegistry()).find_models(None, "rock")
    finally:
        engine.close()
    assert result["models"][0]["source"] == "polyhaven:rock_moss_set_01"
    assert "cast" in result["hint"]
