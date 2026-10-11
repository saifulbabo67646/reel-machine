"""explainer3d plan 3: objects the agent builds from primitive parts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reelmachine.recipes.explainer3d.assets import compound_dims, resolve_cast
from reelmachine.recipes.explainer3d.compile import shot_document
from reelmachine.recipes.explainer3d.models import CompoundSpec, Explainer3DInputs
from reelmachine.recipes.explainer3d.state import build_ctx, initial_world, plan_shots
from reelmachine.recipes.explainer3d.timing import shot_windows
from reelmachine.recipes.explainer3d.validate import resolve_script

ROD = {"parts": [
    {"shape": "cylinder", "size": [0.02, 1.6, 0.02], "at": [0, 0.8, 0], "material": "wood"},
    {"shape": "torus", "size": [0.1, 0.02, 0.1], "at": [0.05, 0.3, 0], "rotate": [0, 90, 0], "material": "metal"},
    {"shape": "sphere", "size": [0.04, 0.04, 0.04], "at": [0, 1.62, 0], "colour": "#ff3b30", "glow": 1.5},
]}


def _inputs(cast, **over):
    return Explainer3DInputs.model_validate({
        "cast": cast, "shots": [{"narration": "x", "camera": {"move": "orbit", "target": "stage.center"},
                                 "actions": [{"move": "slam", "actor": next(iter(cast))}]}], **over})


def test_a_compound_parses_and_measures_its_bounds():
    spec = CompoundSpec.model_validate(ROD)
    w, h, d = compound_dims(spec)
    assert h == pytest.approx(1.64, abs=1e-3)  # rod from 0 to 1.6, ball tip to 1.64
    assert w == pytest.approx(0.12, abs=1e-3)  # the reel sticks out to the side: -0.02 to 0.10


def test_compounds_are_cast_members_and_containers_get_a_fill():
    bucket = {"parts": [{"shape": "cylinder", "size": [0.3, 0.35, 0.3], "at": [0, 0.175, 0], "material": "wood"}],
              "container": True}
    inputs = _inputs({"rod": {"asset": ROD}, "bucket": {"asset": bucket, "at": "stage.center"}})
    resolve_script(inputs)
    models = resolve_cast(inputs, cache_root=None)
    assert models["rod"].kind == "compound" and models["rod"].dims[1] == pytest.approx(1.64, abs=1e-3)
    assert initial_world(inputs, models)["bucket"]["fill"] is not None
    assert build_ctx(inputs, models)["dims"]["rod"][1] == pytest.approx(1.64, abs=1e-3)
    shots = resolve_script(inputs)
    windows = shot_windows([1000])
    plan = plan_shots(inputs, shots, windows, models)[0]
    doc = shot_document(inputs, plan, windows[0], [], build_ctx(inputs, models), render_ms=1000, models=models)
    assert doc["actors"]["rod"]["compound"]["parts"][2]["glow"] == 1.5


@pytest.mark.parametrize("bad", [
    {"parts": []},
    {"parts": [{"shape": "teapot", "size": [1, 1, 1], "at": [0, 0, 0]}]},
    {"parts": [{"shape": "box", "size": [0, 1, 1], "at": [0, 0, 0]}]},
    {"parts": [{"shape": "box", "size": [1, 1, 1], "at": [0, 0, 0]}] * 81},
])
def test_bad_compounds_are_refused(bad):
    with pytest.raises(ValidationError):
        CompoundSpec.model_validate(bad)


def test_models_need_the_cinematic_look():
    from reelmachine.core.errors import InvalidInput

    inputs = Explainer3DInputs.model_validate({
        "look": "diorama-day", "cast": {"bowl": {"asset": "polyhaven:wooden_bowl_01", "at": "stage.center"}},
        "shots": [{"narration": "x", "camera": {"move": "orbit", "target": "bowl"}}]})
    with pytest.raises(InvalidInput) as info:
        resolve_script(inputs)
    assert info.value.details["path"] == "cast.bowl.asset"
