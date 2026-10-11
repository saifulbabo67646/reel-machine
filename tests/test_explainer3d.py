"""explainer3d: fast tests (no Node, no ffmpeg)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reelmachine.core.errors import ErrorCode, RendererUnavailable, ShotRenderFailed
from reelmachine.recipes.explainer3d.models import Explainer3DInputs


def _script(**over):
    base = {
        "title": "Bait",
        "cast": {"bowl": {"asset": {"shape": "bowl", "material": "stone"}, "at": "stage.center"}},
        "shots": [{"narration": "A bowl.", "camera": {"move": "orbit", "target": "bowl"}}],
    }
    base.update(over)
    return base


def test_new_error_codes_are_additive():
    assert RendererUnavailable("x").to_job_error().code == ErrorCode.RENDERER_UNAVAILABLE
    assert ShotRenderFailed("x").to_job_error().code == ErrorCode.SHOT_RENDER_FAILED


def test_inputs_parse_and_ids_fill_in():
    inputs = Explainer3DInputs.model_validate(_script())
    shots = inputs.shot_list()
    assert shots[0].id == "shot-1"
    assert inputs.cast["bowl"].asset.shape == "bowl"
    assert inputs.shots[0].camera.params() == {}


def test_camera_continue_alias_and_extra_params():
    inputs = Explainer3DInputs.model_validate(
        _script(shots=[{"narration": "x", "camera": {"move": "orbit", "continue": True, "to": 90}}])
    )
    cam = inputs.shots[0].camera
    assert cam.continue_ is True
    assert cam.params() == {"to": 90}


@pytest.mark.parametrize(
    "bad",
    [
        {"cast": {"Bowl": {"asset": {"shape": "bowl"}}}},
        {"shots": []},
        {"shots": [{"narration": ""}]},
        {"shots": [{"narration": "x", "actions": [{"move": "appear", "actor": "bowl", "at": [0.5, 0.5]}]}]},
        {"cast": {"bowl": {"asset": {"shape": "bowl", "colour": "red"}}}},
    ],
)
def test_inputs_reject_bad_shapes(bad):
    with pytest.raises(ValidationError):
        Explainer3DInputs.model_validate(_script(**bad))
