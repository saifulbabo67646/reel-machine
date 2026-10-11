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


from reelmachine.recipes.explainer3d.geometry import anchors, dims, fill_y  # noqa: E402
from reelmachine.recipes.explainer3d.models import ShapeSpec  # noqa: E402
from reelmachine.recipes.explainer3d.timing import (  # noqa: E402
    LEAD_MS, MIN_SHOT_MS, TAIL_MS, auto_layout, estimate_speech_ms, shot_windows,
)


def test_dims_follow_kind_ratio_unless_width_given():
    assert dims(ShapeSpec(shape="bowl", height=0.2)) == (0.44, 0.2, 0.44)
    assert dims(ShapeSpec(shape="bowl", height=0.2, width=0.3)) == (0.3, 0.2, 0.3)


def test_anchors_sit_on_the_stage_top():
    a = anchors("stone-slab")
    assert a["stage.center"] == (0.0, 0.9, 0.0)
    assert a["sky"][1] > a["stage.center"][1]
    assert anchors("none")["stage.center"][1] == 0.0


def test_fill_rises_inside_the_rim():
    assert fill_y(1.0, 0.0) == pytest.approx(0.15)
    assert fill_y(1.0, 1.0) < 1.0


def test_shot_windows_pad_and_floor():
    windows = shot_windows([2000, 100])
    assert windows[0].duration_ms == LEAD_MS + 2000 + TAIL_MS
    assert windows[1].start_ms == windows[0].end_ms
    assert windows[1].duration_ms == MIN_SHOT_MS


def test_auto_layout_is_sequential_and_inside():
    spans = auto_layout(3)
    assert spans[0][0] == pytest.approx(0.1) and spans[-1][1] == pytest.approx(0.95)
    assert all(a[1] == pytest.approx(b[0]) for a, b in zip(spans, spans[1:]))


def test_speech_estimates():
    assert estimate_speech_ms("one two three four five six", "en") > 1500
    assert estimate_speech_ms("駅[えき]はどこですか", "ja") == int(len("駅はどこですか") / 7.0 * 1000)
