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


import math  # noqa: E402

from reelmachine.recipes.explainer3d.camera import CAMERA_MOVES, default_start, ease, pose  # noqa: E402
from reelmachine.recipes.explainer3d.moves import MOVES  # noqa: E402
from reelmachine.recipes.explainer3d.world import mix_hex  # noqa: E402

CTX = {
    "anchors": {"stage.center": [0.0, 0.9, 0.0], "sky": [0.0, 3.9, 0.0]},
    "dims": {"bowl": [0.44, 0.2, 0.44], "potato": [0.12, 0.1, 0.12]},
    "homes": {"bowl": [0.0, 0.9, 0.0], "potato": [0.3, 0.9, 0.0]},
    "colours": {"bowl": "#8f877c", "potato": "#d9b44a"},
}


def _world():
    return {
        "bowl": {"position": [0.0, 0.9, 0.0], "visible": True, "rotationY": 0.0, "scale": 1.0,
                 "fill": {"level": 0.0, "colour": "#d9d27a", "surface": "liquid"}},
        "potato": {"position": [0.3, 0.9, 0.0], "visible": False, "rotationY": 0.0, "scale": 1.0, "fill": None},
    }


def test_mix_hex_endpoints():
    assert mix_hex("#000000", "#FFFFFF", 0.0) == "#000000"
    assert mix_hex("#000000", "#ffffff", 1.0) == "#ffffff"
    assert mix_hex("#000000", "#ffffff", 0.5) == "#808080"


def test_fly_in_lands_home_and_is_visible():
    move = MOVES["fly_in"]
    params = move.params.model_validate({}).model_dump(by_alias=True)
    world = move.end(_world(), {"actor": "potato", "params": params}, CTX)
    assert world["potato"]["position"] == [0.3, 0.9, 0.0]
    assert world["potato"]["visible"] is True


def test_drop_into_hides_actor_and_fills_container():
    move = MOVES["drop_into"]
    params = move.params.model_validate({"into": "bowl"}).model_dump(by_alias=True)
    world = move.end(_world(), {"actor": "potato", "params": params}, CTX)
    assert world["potato"]["visible"] is False and world["potato"]["scale"] == 0.0
    assert world["bowl"]["fill"]["level"] == pytest.approx(0.15)
    assert world["bowl"]["fill"]["colour"] == "#d9b44a"  # an empty bowl takes the colour


def test_fill_clamps_at_full():
    move = MOVES["pour"]
    params = move.params.model_validate({"into": "bowl", "fill_add": 0.8}).model_dump(by_alias=True)
    world = _world()
    for _ in range(3):
        world = move.end(world, {"actor": "potato", "params": params}, CTX)
    assert world["bowl"]["fill"]["level"] == 1.0


def test_every_move_has_an_example_that_validates():
    for move in MOVES.values():
        move.params.model_validate(move.example.get("params", {}))
    for move in CAMERA_MOVES.values():
        move.params.model_validate(move.example)


def test_orbit_pose_keeps_radius_and_reaches_angle():
    params = CAMERA_MOVES["orbit"].params.model_validate({"from": 0, "to": 90}).model_dump(by_alias=True)
    target = [0.0, 1.0, 0.0]
    start = default_start("orbit", params, target)
    end = pose("orbit", params, start, target, 1.0)
    assert end["position"][0] == pytest.approx(2.2) and end["position"][2] == pytest.approx(0.0, abs=1e-9)


def test_push_in_and_ease():
    params = CAMERA_MOVES["push_in"].params.model_validate({"factor": 0.5}).model_dump(by_alias=True)
    target = [0.0, 1.0, 0.0]
    start = default_start("push_in", params, target)
    end = pose("push_in", params, start, target, 1.0)
    assert math.dist(end["position"], target) == pytest.approx(math.dist(start["position"], target) * 0.5)
    assert ease(0.0, True, True) == 0.0 and ease(1.0, True, False) == pytest.approx(1.0)
    assert ease(0.5, False, False) == 0.5


from reelmachine.core.errors import InvalidInput  # noqa: E402
from reelmachine.recipes.explainer3d.validate import resolve_script  # noqa: E402


def _resolve(**over):
    return resolve_script(Explainer3DInputs.model_validate(_script(**over)))


def _path(**over) -> str:
    with pytest.raises(InvalidInput) as info:
        _resolve(**over)
    return info.value.details["path"]


def test_resolve_fills_windows_and_aliases():
    shots = _resolve(
        cast={
            "bowl": {"asset": {"shape": "bowl"}, "at": "stage.center"},
            "potato": {"asset": {"shape": "ball", "height": 0.1}},
        },
        shots=[{
            "narration": "Drop it.",
            "actions": [
                {"move": "fly_in", "actor": "potato", "from": "left"},
                {"move": "drop_into", "actor": "potato", "into": "bowl"},
            ],
        }],
    )
    a0, a1 = shots[0].actions
    assert a0.params["from"] == "left" and a0.at == pytest.approx((0.1, 0.525))
    assert a1.at[0] == pytest.approx(a0.at[1])
    assert shots[0].camera.move == "static" and shots[0].camera.continues is False


def test_auto_actions():
    shots = _resolve(shots=[{"narration": "x", "actions": "auto",
                             "auto": [{"move": "spin_showcase", "actor": "bowl"}]}])
    assert shots[0].actions[0].at == pytest.approx((0.1, 0.95))


@pytest.mark.parametrize(
    "over, path",
    [
        ({"cast": {"bowl": {"asset": "pack:stone-bowl"}}}, "cast.bowl.asset"),
        ({"cast": {"bowl": {"asset": {"shape": "bowl"}, "at": "moon"}}}, "cast.bowl.at"),
        ({"look": "neon"}, "look"),
        ({"shots": [{"narration": "x", "actions": [{"move": "teleport", "actor": "bowl"}]}]},
         "shots[0].actions[0].move"),
        ({"shots": [{"narration": "x", "actions": [{"move": "appear", "actor": "ghost"}]}]},
         "shots[0].actions[0].actor"),
        ({"shots": [{"narration": "x", "actions": [{"move": "appear"}]}]}, "shots[0].actions[0].actor"),
        ({"shots": [{"narration": "x", "actions": [{"move": "sprinkle", "actor": "bowl", "into": "bowl"}]}]},
         "shots[0].actions[0].actor"),
        ({"shots": [{"narration": "x", "actions": [{"move": "spin_showcase", "actor": "bowl", "turns": 9}]}]},
         "shots[0].actions[0].turns"),
        ({"cast": {"bowl": {"asset": {"shape": "bowl"}}, "ball": {"asset": {"shape": "ball"}}},
          "shots": [{"narration": "x", "actions": [{"move": "drop_into", "actor": "bowl", "into": "ball"}]}]},
         "shots[0].actions[0].into"),
        ({"shots": [{"narration": "x", "actions": [
            {"move": "spin_showcase", "actor": "bowl", "at": [0.0, 0.6]},
            {"move": "exit", "actor": "bowl", "at": [0.5, 1.0]}]}]},
         "shots[0].actions[1].at"),
        ({"shots": [{"narration": "x"}]}, "shots[0]"),
        ({"shots": [{"narration": "x", "camera": {"move": "orbit", "continue": True}}]},
         "shots[0].camera.continue"),
        ({"shots": [{"narration": "x", "camera": {"move": "orbit", "target": "nowhere"}}]},
         "shots[0].camera.target"),
        ({"shots": [{"narration": "x", "camera": {"move": "zoom"}}]}, "shots[0].camera.move"),
        ({"shots": [{"narration": "x", "actions": "auto",
                     "auto": [{"move": "appear", "actor": "bowl", "at": [0, 1]}]}]},
         "shots[0].auto[0].at"),
    ],
)
def test_refusals_name_the_field(over, path):
    assert _path(**over) == path
