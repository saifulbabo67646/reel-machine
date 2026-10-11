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


from reelmachine.recipes.explainer3d.state import build_ctx, initial_world, plan_shots  # noqa: E402


def _plans(over, speech=(2000, 2000, 2000)):
    inputs = Explainer3DInputs.model_validate(_script(**over))
    shots = resolve_script(inputs)
    return plan_shots(inputs, shots, shot_windows(list(speech)[: len(shots)]))


CAST2 = {
    "bowl": {"asset": {"shape": "bowl", "height": 0.2}, "at": "stage.center"},
    "potato": {"asset": {"shape": "ball", "height": 0.1, "colour": "#d9b44a"}},
}


def test_initial_world_hides_unplaced_actors_and_gives_containers_a_fill():
    inputs = Explainer3DInputs.model_validate(_script(cast=CAST2))
    world = initial_world(inputs)
    assert world["bowl"]["visible"] and world["bowl"]["fill"]["level"] == 0.0
    assert not world["potato"]["visible"] and world["potato"]["fill"] is None
    assert build_ctx(inputs)["colours"]["potato"] == "#d9b44a"


def test_state_carries_to_the_next_shot():
    plans = _plans({
        "cast": CAST2,
        "shots": [
            {"narration": "in", "actions": [{"move": "fly_in", "actor": "potato"},
                                            {"move": "drop_into", "actor": "potato", "into": "bowl"}]},
            {"narration": "mash", "actions": [{"move": "mash", "actor": "bowl"}]},
        ],
    })
    assert plans[0].world["bowl"]["fill"]["level"] == 0.0
    assert plans[1].world["bowl"]["fill"]["level"] == pytest.approx(0.15)
    assert plans[1].world["potato"]["visible"] is False


def test_actions_become_local_seconds_with_seeds_and_cues():
    plans = _plans({"cast": CAST2, "shots": [{"narration": "x", "actions": [
        {"move": "fly_in", "actor": "potato", "at": [0.0, 0.5]}]}]}, speech=(2000,))
    action = plans[0].actions[0]
    duration_s = (250 + 2000 + 350) / 1000
    assert action["t0"] == 0.0 and action["t1"] == pytest.approx(duration_s * 0.5)
    assert isinstance(action["seed"], int)
    assert plans[0].cues == [{"at_ms": 0, "sfx": "whoosh"}]


def test_camera_chain_continues_and_eases_only_at_the_ends():
    plans = _plans({"shots": [
        {"narration": "a", "camera": {"move": "orbit", "target": "bowl", "from": 0, "to": 30}},
        {"narration": "b", "camera": {"move": "orbit", "target": "bowl", "to": 60, "continue": True}},
        {"narration": "c", "camera": {"move": "push_in", "target": "bowl", "continue": True}},
        {"narration": "d", "camera": {"move": "orbit", "target": "bowl"}},
    ]}, speech=(2000, 2000, 2000, 2000))
    assert plans[1].camera["start"] == plans[0].camera["end"]
    assert plans[2].camera["start"] == plans[1].camera["end"]
    assert [p.join for p in plans] == ["none", "cut", "cut", "xfade"]
    assert [(p.camera["easeIn"], p.camera["easeOut"]) for p in plans] == [
        (True, False), (False, False), (False, True), (True, True)]


def test_shot_without_camera_holds_the_previous_pose():
    plans = _plans({"shots": [
        {"narration": "a", "camera": {"move": "orbit", "target": "bowl"}},
        {"narration": "b", "actions": [{"move": "spin_showcase", "actor": "bowl"}]},
    ]}, speech=(2000, 2000))
    assert plans[1].camera["move"] == "static"
    assert plans[1].camera["start"] == plans[0].camera["end"] and plans[1].join == "cut"


import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

RUNTIME = Path(__file__).resolve().parents[1] / "reelmachine/recipes/explainer3d/runtime"
node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _evaluate(cases):
    proc = subprocess.run(["node", str(RUNTIME / "tools/evaluate.mjs")], input=json.dumps({"cases": cases}),
                          capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)["results"]


def _close(a, b, path="$"):
    if isinstance(a, dict):
        assert set(a) == set(b), path
        for k in a:
            _close(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _close(x, y, f"{path}[{i}]")
    elif isinstance(a, float) or isinstance(b, float):
        assert a == pytest.approx(b, abs=1e-6), path
    else:
        assert a == b, path


@node
def test_runtime_end_states_match_python():
    world = _world()
    world["bottle"] = {"position": [-0.4, 0.9, 0.0], "visible": True, "rotationY": 0.0, "scale": 1.0, "fill": None}
    ctx = {**CTX, "dims": {**CTX["dims"], "bottle": [0.06, 0.2, 0.06]},
           "homes": {**CTX["homes"], "bottle": [-0.4, 0.9, 0.0]}, "colours": {**CTX["colours"], "bottle": "#cfe8ef"}}
    world["bowl"]["fill"]["level"] = 0.9  # pours past full must clamp
    cases, expected = [], []
    for name, actor, raw in [
        ("appear", "potato", {}), ("scale_pop", "potato", {}), ("fly_in", "potato", {"from": "left"}),
        ("fly_in", "potato", {"to": "bowl"}), ("drop_into", "potato", {"into": "bowl", "colour": "#ff0000"}),
        ("place_on", "bottle", {"on": "bowl"}), ("spin_showcase", "bowl", {"turns": 1.5}),
        ("exit", "bowl", {"to": "sky"}), ("pour", "bottle", {"into": "bowl", "fill_add": 0.5}),
        ("sprinkle", None, {"into": "bowl"}), ("mash", "bowl", {"colour": "#aabbcc"}), ("steam", "bowl", {}),
    ]:
        move = MOVES[name]
        action = {"move": name, "actor": actor, "params": move.params.model_validate(raw).model_dump(by_alias=True),
                  "t0": 0.0, "t1": 1.0, "seed": 7, "index": 0}
        cases.append({"kind": "action", "world": world, "action": action, "ctx": ctx, "p": 1.0})
        expected.append(move.end(world, action, ctx))
    for move, raw in [("static", {}), ("orbit", {"from": 10, "to": 80}), ("push_in", {"factor": 0.4}),
                      ("dolly", {"offset": [0.5, 0.1, 0]})]:
        params = CAMERA_MOVES[move].params.model_validate(raw).model_dump(by_alias=True)
        target = [0.1, 1.0, 0.0]
        start = default_start(move, params, target)
        for p in (0.0, 0.3, 1.0):
            cases.append({"kind": "camera", "move": move, "params": params, "start": start, "target": target, "p": p})
            expected.append(pose(move, params, start, target, p))
    for got, want in zip(_evaluate(cases), expected):
        _close(got, want)


@node
def test_runtime_node_unit_tests_pass():
    proc = subprocess.run(["node", "--test", str(RUNTIME / "tests" / "*.test.mjs")], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


from reelmachine.recipes.explainer3d.compile import (  # noqa: E402
    RUNTIME_VERSION, load_look, project_key, shot_document, write_project,
)


def _doc():
    inputs = Explainer3DInputs.model_validate(_script(cast=CAST2, shots=[{"narration": "In it goes.", "label": "Step 1",
        "actions": [{"move": "fly_in", "actor": "potato"}], "camera": {"move": "orbit", "target": "bowl"}}]))
    shots = resolve_script(inputs)
    windows = shot_windows([1500])
    plan = plan_shots(inputs, shots, windows)[0]
    words = [{"text": "In", "start": 0.25, "end": 0.5}]
    return shot_document(inputs, plan, windows[0], words, build_ctx(inputs), render_ms=windows[0].duration_ms + 300)


def test_shot_document_is_complete_and_keyed():
    doc = _doc()
    assert doc["width"] == 1080 and doc["height"] == 1920 and doc["fps"] == 30
    assert doc["duration"] == pytest.approx(2.1) and doc["renderDuration"] == pytest.approx(2.4)
    assert doc["look"]["id"] == "diorama-day" and doc["captions"]["label"] == "Step 1"
    assert doc["actors"]["potato"]["dims"] == [0.1, 0.1, 0.1]
    assert project_key(doc) == project_key(_doc()) and len(RUNTIME_VERSION) == 16


def test_write_project_is_self_contained(tmp_path):
    out = write_project(_doc(), tmp_path / "shot-1")
    html = (out / "index.html").read_text()
    assert 'data-composition-id="shot"' in html and "data-no-timeline" in html
    assert 'data-duration="2.400"' in html and '"three": "./vendor/three.module.js"' in html
    assert (out / "runtime/main.mjs").is_file() and (out / "vendor/three.module.js").is_file()


def test_looks_load():
    assert load_look("studio-clean")["id"] == "studio-clean"


from reelmachine.core.recipe import ProbeContext  # noqa: E402
from reelmachine.core.stage import StaticProviders  # noqa: E402
from reelmachine.recipes.explainer3d.recipe import Explainer3DRecipe  # noqa: E402


class _Voice:
    name = "fake"


class _Renderer:
    name = "hyperframes"

    def __init__(self, problems):
        self.problems = problems

    def missing(self):
        return self.problems


def _probe(inputs, problems=()):
    ctx = ProbeContext(providers=StaticProviders({"narration": _Voice(), "renderer": _Renderer(list(problems))}))
    return Explainer3DRecipe().probe(Explainer3DInputs.model_validate(inputs), ctx)


def test_probe_estimates_length_and_cost():
    report = _probe(_script(shots=[{"narration": "one two three four five", "camera": {"move": "orbit"}}]))
    assert report.ok and report.details["estimatedMs"] >= 1500
    assert report.details["cost"]["ttsCharacters"] == len("one two three four five")


def test_probe_reports_script_errors_with_the_path():
    report = _probe(_script(look="neon"))
    check = next(c for c in report.checks if c.name == "script")
    assert not report.ok and not check.ok and "look" in check.detail
    assert report.details["error"]["path"] == "look"


def test_probe_reports_a_missing_renderer():
    report = _probe(_script(), problems=["node is not on PATH"])
    assert not report.ok and "node" in next(c for c in report.checks if c.name == "renderer").detail
