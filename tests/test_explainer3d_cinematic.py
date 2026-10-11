"""explainer3d plan 2: cinematic reels — fast tests (no Node, no network)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reelmachine.core.timeline import WordSpan
from reelmachine.recipes.explainer3d.markup import display_words, parse_markup, spoken_text
from reelmachine.recipes.explainer3d.models import Explainer3DInputs, ModelSpec
from reelmachine.recipes.explainer3d.timing import fit_windows, shot_windows
from reelmachine.recipes.explainer3d.validate import resolve_script


def _script(**over):
    base = {
        "title": "Bait",
        "cast": {"bowl": {"asset": {"shape": "bowl", "material": "stone"}, "at": "stage.center"}},
        "shots": [{"narration": "A bowl.", "camera": {"move": "orbit", "target": "bowl"}}],
    }
    base.update(over)
    return base


# ---------------------------------------------------------------- narration markup
def test_markup_splits_shown_and_spoken_and_hot_words():
    tokens = parse_markup("This [*$1*|one dollar] bait pulls *monster fish* out. [WATCH 👀|watch]")
    assert spoken_text(tokens) == "This one dollar bait pulls monster fish out. watch"
    assert [(t.shown, t.hot) for t in tokens] == [
        ("This", False), ("$1", True), ("bait", False), ("pulls", False), ("monster", True),
        ("fish", True), ("out.", False), ("WATCH 👀", False),
    ]


def test_plain_narration_is_unchanged_by_markup():
    tokens = parse_markup("First, a potato goes in.")
    assert spoken_text(tokens) == "First, a potato goes in." and not any(t.hot for t in tokens)


def test_display_words_take_the_spoken_words_timings():
    tokens = parse_markup("This [$1|one dollar] bait")
    spans = [WordSpan(text="This", start_ms=0, end_ms=100), WordSpan(text="one", start_ms=100, end_ms=200),
             WordSpan(text="dollar", start_ms=200, end_ms=400), WordSpan(text="bait", start_ms=400, end_ms=600)]
    words = display_words(tokens, spans, total_ms=600)
    assert [(w.text, w.start_ms, w.end_ms) for w in words] == [("This", 0, 100), ("$1", 100, 400), ("bait", 400, 600)]


def test_display_words_spread_evenly_when_the_voice_split_words_differently():
    tokens = parse_markup("one two three")
    words = display_words(tokens, [WordSpan(text="onetwothree", start_ms=0, end_ms=900)], total_ms=900)
    assert [(w.start_ms, w.end_ms) for w in words] == [(0, 300), (300, 600), (600, 900)]


# ---------------------------------------------------------------- schema
def test_beat_shots_need_a_duration():
    beat = {"duration": 2.5, "camera": {"move": "orbit", "target": "bowl"}}
    inputs = Explainer3DInputs.model_validate(_script(shots=[beat]))
    assert inputs.shots[0].narration == "" and inputs.shots[0].duration == 2.5
    with pytest.raises(ValidationError):
        Explainer3DInputs.model_validate(_script(shots=[{"narration": "", "camera": {"move": "orbit"}}]))


def test_length_music_auto_models_and_cards_parse():
    inputs = Explainer3DInputs.model_validate(_script(
        length=55, music="auto",
        cast={"fish": {"asset": {"model": "khronos:BarramundiFish", "size": 1.1}},
              "bowl": {"asset": "polyhaven:wooden_bowl_01", "at": "stage.center"}},
        shots=[{"narration": "Go.", "card": "SAVE THIS 📌", "set": {"environment": "underwater"},
                "camera": {"move": "orbit", "target": "bowl"}}],
    ))
    assert inputs.length == 55 and inputs.music == "auto"
    assert isinstance(inputs.cast["bowl"].asset, ModelSpec) and inputs.cast["bowl"].asset.model == "polyhaven:wooden_bowl_01"
    assert inputs.shots[0].set.environment == "underwater" and inputs.shots[0].card == "SAVE THIS 📌"


@pytest.mark.parametrize("bad", [
    {"length": 10},
    {"length": 400},
    {"cast": {"x": {"asset": {"model": "sketchfab:thing"}}}},
    {"cast": {"x": {"asset": {"shape": "ball", "count": 40}}}},
])
def test_schema_refuses(bad):
    with pytest.raises(ValidationError):
        Explainer3DInputs.model_validate(_script(**bad))


def test_music_auto_is_not_a_path():
    resolve_script(Explainer3DInputs.model_validate(_script(music="auto")))


# ---------------------------------------------------------------- timing
def test_beat_shot_windows_take_their_duration_and_padding_follows_the_look():
    windows = shot_windows([1000, 0], durations=[None, 2500], lead_ms=120, tail_ms=200, min_ms=900)
    assert [w.duration_ms for w in windows] == [1320, 2500]
    assert windows[1].start_ms == 1320 and windows[1].speech_ms == 0


def test_fit_stretches_tails_to_the_target_and_never_shrinks():
    windows = shot_windows([2000, 2000])  # 2600 ms each
    fitted = fit_windows(windows, 6000)
    assert sum(w.duration_ms for w in fitted) == 6000
    assert [w.start_ms for w in fitted] == [0, fitted[0].duration_ms]
    assert [w.speech_offset_ms for w in fitted] == [w.speech_offset_ms for w in windows]
    capped = fit_windows(windows, 20000)
    assert [w.duration_ms for w in capped] == [4160, 4160]  # 1.6x at most
    assert fit_windows(windows, 4000) == windows


# ---------------------------------------------------------------- cinematic moves (end states)
from reelmachine.recipes.explainer3d.camera import CAMERA_MOVES, default_start, pose  # noqa: E402
from reelmachine.recipes.explainer3d.geometry import anchors  # noqa: E402
from reelmachine.recipes.explainer3d.moves import MOVES  # noqa: E402

MARKS = {k: list(v) for k, v in anchors("stone-slab").items()}
CTX = {"anchors": MARKS, "dims": {"bowl": [0.24, 0.1, 0.24], "potato": [0.08, 0.14, 0.08], "fish": [0.14, 0.3, 1.1],
                                  "balls": [0.05, 0.05, 0.05]},
       "homes": {"bowl": MARKS["stage.center"], "potato": [0.3, 0.9, 0.1], "fish": MARKS["lake.near"],
                 "balls": MARKS["stage.front"]},
       "colours": {"bowl": "#8a5a33", "potato": "#e07b2a", "fish": "#b07a45", "balls": "#c97a34"}}


def _w():
    def actor(pos, visible=True, fill=None):
        return {"position": list(pos), "visible": visible, "rotationY": 0.0, "scale": 1.0, "fill": fill}
    return {
        "bowl": actor(MARKS["stage.center"], fill={"level": 0.2, "colour": "#d9d27a", "surface": "liquid", "glaze": None}),
        "potato": actor([0.3, 0.9, 0.1]), "fish": actor(MARKS["lake.near"], visible=False),
        "balls": actor(MARKS["stage.front"], visible=False),
    }


def _end(name, actor, **raw):
    move = MOVES[name]
    params = move.params.model_validate(raw).model_dump(by_alias=True)
    return move.end(_w(), {"actor": actor, "params": params}, CTX)


def test_slam_lands_home_and_burst_turns_the_actor_into_crumbs():
    assert _end("slam", "potato")["potato"]["position"] == [0.3, 0.9, 0.1]
    world = _end("burst", "potato", into="bowl")
    assert world["potato"]["visible"] is False
    assert world["bowl"]["fill"]["surface"] == "crumbs" and world["bowl"]["fill"]["colour"] == "#e07b2a"
    assert world["bowl"]["fill"]["level"] == pytest.approx(0.4)


def test_drizzle_glazes_and_smash_turns_everything_to_paste():
    assert _end("drizzle", None, into="bowl")["bowl"]["fill"]["glaze"] == "#f0a020"
    world = _end("smash", "potato", into="bowl", colour="#c97a34")
    assert world["potato"]["visible"] is False
    assert world["bowl"]["fill"]["surface"] == "paste" and world["bowl"]["fill"]["glaze"] is None


def test_pop_out_throw_leap_sink_school():
    world = _end("pop_out", "balls", **{"from": "bowl", "to": "stage.front"})
    assert world["balls"]["visible"] and world["balls"]["position"] == MARKS["stage.front"]
    assert world["bowl"]["fill"]["level"] == pytest.approx(0.0)
    thrown = _end("throw", "potato", to="lake.far")["potato"]
    assert thrown["visible"] is False and thrown["position"][1] < 0
    leapt = _end("leap", "fish", spot="lake.near", span=1.6)["fish"]
    assert leapt["visible"] is False and leapt["position"][0] == pytest.approx(MARKS["lake.near"][0] + 0.8)
    assert _end("sink", "potato", to="underwater.center")["potato"]["position"] == MARKS["underwater.center"]
    eaten = _end("school", "fish", around="potato", eat=True)
    assert eaten["potato"]["visible"] is False and eaten["fish"]["visible"] is True


def test_track_and_dive_cameras():
    params = CAMERA_MOVES["track"].params.model_validate({"offset": [0, 0.3, 1.0]}).model_dump(by_alias=True)
    start = default_start("track", params, [0, 1, 0])
    end = pose("track", params, start, [1.0, 0.5, 0.0], 1.0)
    assert end["position"] == pytest.approx([1.0, 0.8, 1.0]) and end["target"] == pytest.approx([1.0, 0.5, 0.0])
    params = CAMERA_MOVES["dive"].params.model_validate({}).model_dump(by_alias=True)
    start = default_start("dive", params, MARKS["lake.far"])
    assert 0 < pose("dive", params, start, MARKS["lake.far"], 1.0)["position"][1] < 0.1  # skims the surface
    wide = CAMERA_MOVES["orbit"].params.model_validate({"fov": 60}).model_dump(by_alias=True)
    assert default_start("orbit", wide, [0, 1, 0])["fov"] == 60


# ---------------------------------------------------------------- retention lint
from reelmachine.recipes.explainer3d.lint import retention_warnings  # noqa: E402

FISH_CAST = {
    "bowl": {"asset": {"shape": "bowl", "material": "wood"}, "at": "stage.center"},
    "fish": {"asset": {"shape": "ball", "height": 0.3}},
}


def _lint(shots, length=None):
    inputs = Explainer3DInputs.model_validate(_script(cast=FISH_CAST, shots=shots, length=length))
    shots = resolve_script(inputs)
    windows = shot_windows([2400] * len(shots), durations=[s.duration_ms for s in shots])
    return retention_warnings(inputs, shots, windows)


def test_lint_wants_a_hook_beats_and_a_call_to_action():
    slow = [{"narration": "Welcome to my channel about fishing and other things.", "camera": {"move": "orbit", "target": "bowl"}}] * 4
    warnings = " ".join(_lint(slow))
    assert "hook" in warnings and "beat" in warnings and "call to action" in warnings
    good = [{"narration": "Watch this.", "actions": [{"move": "leap", "actor": "fish", "at": [0.0, 0.6]}]},
            {"narration": "Save this for later.", "actions": [{"move": "slam", "actor": "fish"}]}]
    assert _lint(good) == []


def test_lint_flags_a_length_far_from_the_target():
    shots = [{"narration": "Watch this.", "actions": [{"move": "leap", "actor": "fish", "at": [0.0, 0.6]}]},
             {"narration": "Save this.", "actions": [{"move": "slam", "actor": "fish"}]}]
    assert any("length" in w for w in _lint(shots, length=60))


def test_describe_carries_a_guide_and_probe_lints(tmp_path):
    import dataclasses

    from reelmachine.config import get_settings
    from reelmachine.engine import Engine

    engine = Engine(dataclasses.replace(get_settings(), workdir=tmp_path / "w", tts="fake"),
                    provider_overrides={"renderer": "explainer3d-fake"})
    try:
        guide = engine.describe("explainer3d")["guide"]
        assert "leap" in guide["moves"] and "dive" in guide["cameras"] and "lake" in guide["environments"]
        assert guide["moves"]["leap"]["params"]["slowmo"]["default"] == 0.6
        assert "[shown|spoken]" in guide["narration"] and guide["length"]
        report = engine.probe("explainer3d", _script(length=30), caller="local")
    finally:
        engine.close()
    assert any(w.startswith("hook") for w in report.warnings)
    assert report.details["length"]["targetMs"] == 30000


import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

RUNTIME = Path(__file__).resolve().parents[1] / "reelmachine/recipes/explainer3d/runtime"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_runtime_end_states_match_python_for_cinematic_moves():
    cases, expected = [], []
    for name, actor, raw in [
        ("leap", "fish", {"spot": "lake.near", "direction": "left"}), ("slam", "potato", {"on": "bowl"}),
        ("burst", "potato", {"into": "bowl"}), ("drizzle", None, {"into": "bowl"}),
        ("smash", "potato", {"into": "bowl", "colour": "#aa5500"}), ("pop_out", "balls", {"from": "bowl"}),
        ("throw", "potato", {"to": "lake.far"}), ("splash", None, {"spot": "lake.left"}),
        ("sink", "potato", {"to": "underwater.floor"}), ("school", "fish", {"around": "potato", "eat": True}),
    ]:
        move = MOVES[name]
        action = {"move": name, "actor": actor, "params": move.params.model_validate(raw).model_dump(by_alias=True),
                  "t0": 0.0, "t1": 2.0, "seed": 7, "index": 0}
        cases.append({"kind": "action", "world": _w(), "action": action, "ctx": CTX, "p": 1.0})
        expected.append(move.end(_w(), action, CTX))
    for move, raw in [("track", {"offset": [0.2, 0.3, 1.0]}), ("dive", {"depth": 0.7})]:
        params = CAMERA_MOVES[move].params.model_validate(raw).model_dump(by_alias=True)
        start = default_start(move, params, MARKS["lake.far"])
        for p in (0.0, 0.4, 1.0):
            cases.append({"kind": "camera", "move": move, "params": params, "start": start, "target": MARKS["lake.far"], "p": p})
            expected.append(pose(move, params, start, MARKS["lake.far"], p))
    proc = subprocess.run(["node", str(RUNTIME / "tools/evaluate.mjs")], input=json.dumps({"cases": cases}),
                          capture_output=True, text=True, check=True)
    for (case, got), want in zip(zip(cases, json.loads(proc.stdout)["results"]), expected):
        assert json.dumps(got, sort_keys=True) == json.dumps(want, sort_keys=True) or _close(got, want), case.get("action", case)


def _close(a, b):
    if isinstance(a, dict):
        return set(a) == set(b) and all(_close(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) < 1e-6
    return a == b


def test_hot_markup_survives_trailing_punctuation():
    tokens = parse_markup("One boiled *sweet potato*. *Smash* it, *now*!")
    assert [(t.shown, t.hot) for t in tokens] == [
        ("One", False), ("boiled", False), ("sweet", True), ("potato.", True), ("Smash", True), ("it,", False), ("now!", True)]


def test_camera_aim_shifts_the_point_a_camera_looks_at():
    from reelmachine.recipes.explainer3d.state import plan_shots

    inputs = Explainer3DInputs.model_validate(_script(shots=[
        {"narration": "x", "camera": {"move": "orbit", "target": "bowl", "aim": [0, 0.8, 0]}}]))
    shots = resolve_script(inputs)
    plan = plan_shots(inputs, shots, shot_windows([1000]))[0]
    plain = plan_shots(Explainer3DInputs.model_validate(_script()), resolve_script(Explainer3DInputs.model_validate(_script())), shot_windows([1000]))[0]
    assert plan.camera["target"][1] == pytest.approx(plain.camera["target"][1] + 0.8)
