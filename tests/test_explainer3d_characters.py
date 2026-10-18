"""explainer3d plan 3: characters — catalogue, fetching, cast, moves (offline)."""

from __future__ import annotations

import json
import math
import zipfile

import pytest

from reelmachine.core.errors import InvalidInput
from reelmachine.recipes.explainer3d import characters
from reelmachine.recipes.explainer3d.models import CharacterSpec, Explainer3DInputs
from reelmachine.recipes.explainer3d.moves import MOVES
from reelmachine.recipes.explainer3d.state import build_ctx, initial_world
from reelmachine.recipes.explainer3d.validate import resolve_script


def _gltf(height=1.8):
    return {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "Body"}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [{"type": "VEC3", "componentType": 5126, "count": 8, "min": [-0.3, 0, -0.15], "max": [0.3, height, 0.15]}],
            "images": [{"uri": "T_Eye_Normal_png.png"}], "buffers": [{"uri": "Body.bin", "byteLength": 0}]}


def _packs(tmp_path):
    base, anim = tmp_path / "ubc.zip", tmp_path / "ual.zip"
    folder = "Universal Base Characters[Standard]/Base Characters/Godot - UE/"
    with zipfile.ZipFile(base, "w") as z:
        for who in ("Male", "Female"):
            z.writestr(folder + f"Superhero_{who}_FullBody.gltf", json.dumps(_gltf()))
            z.writestr(folder + f"Superhero_{who}_FullBody.bin", b"")
        z.writestr(folder + "T_Eye_Normal.png", b"png")
        hair = "Universal Base Characters[Standard]/Hairstyles/Rigged to Head Bone/glTF (Godot -Unreal)/"
        for name in ("Hair_Long", "Hair_SimpleParted", "Hair_Beard"):
            z.writestr(hair + f"{name}.gltf", json.dumps({"asset": {"version": "2.0"}, "images": [{"uri": "T_Hair_1_BaseColor_png.png"}]}))
            z.writestr(hair + f"{name}.bin", b"")
        z.writestr("Universal Base Characters[Standard]/Hairstyles/Textures/T_Hair_1_BaseColor.png", b"png")
        z.writestr("Universal Base Characters[Standard]/License.txt", "CC0 1.0 Universal")
    with zipfile.ZipFile(anim, "w") as z:
        z.writestr("Universal Animation Library[Standard]/Unreal-Godot/UAL1_Standard.glb", b"glTF")
    return base, anim


def test_importing_the_packs_extracts_only_what_renders_and_fixes_texture_names(tmp_path):
    base, anim = _packs(tmp_path)
    root = characters.import_packs(base, anim, tmp_path / "cache")
    assert (root / "male" / "Superhero_Male_FullBody.gltf").is_file() and (root / "animations.glb").is_file()
    doc = json.loads((root / "male" / "Superhero_Male_FullBody.gltf").read_text())
    assert doc["images"][0]["uri"] == "T_Eye_Normal.png"  # the pack names a file it does not ship
    assert characters.installed(tmp_path / "cache")
    assert (root / "hair" / "Hair_Long.gltf").is_file() and (root / "hair" / "T_Hair_1_BaseColor.png").is_file()
    assert json.loads((root / "hair" / "Hair_Long.gltf").read_text())["images"][0]["uri"] == "T_Hair_1_BaseColor.png"
    assert "CC0" in (root / "licences" / "base-License.txt").read_text()  # the pack's own words, kept


def test_the_catalogue_and_clip_names():
    assert {"quaternius:male", "quaternius:female", "mannequin"} <= set(characters.CHARACTERS)
    assert characters.CLIPS["walk"] == "Walk_Loop" and characters.CLIPS["sit"] == "Sitting_Idle_Loop"
    assert "pick_up" in characters.CLIPS and "talk" in characters.CLIPS


def _inputs(**over):
    base = {"look": "cinematic", "set": {"preset": "kitchen"},
            "cast": {"chef": {"asset": {"character": "quaternius:female", "height": 1.7}, "at": "left"},
                     "bowl": {"asset": {"shape": "bowl"}, "at": "stage.center"}},
            "shots": [{"narration": "Hi.", "camera": {"move": "orbit", "target": "chef"},
                       "actions": [{"move": "walk_to", "actor": "chef", "to": "front"}]}]}
    base.update(over)
    return Explainer3DInputs.model_validate(base)


def test_characters_are_cast_members_with_a_pose(tmp_path, monkeypatch):
    monkeypatch.setattr(characters, "ensure_installed", lambda cache_root: _fake_root(tmp_path))
    inputs = _inputs()
    resolve_script(inputs)
    from reelmachine.recipes.explainer3d.assets import resolve_cast

    models = resolve_cast(inputs, tmp_path)
    assert models["chef"].kind == "character" and models["chef"].dims[1] == pytest.approx(1.7)
    world = initial_world(inputs, models)
    assert world["chef"]["pose"] == "stand" and world["chef"]["holding"] is None
    assert "pose" not in world["bowl"]


def _fake_root(tmp_path):
    root = tmp_path / "chars"
    for who in ("male", "female"):
        (root / who).mkdir(parents=True, exist_ok=True)
        (root / who / f"Superhero_{who.title()}_FullBody.gltf").write_text(json.dumps(_gltf()))
    (root / "animations.glb").write_bytes(b"glTF")
    return root


def _ctx_world():
    marks = {"stage.center": [0, 0.9, 0], "front": [0, 0, 0.8], "left": [-1.6, 0, 0.8], "door": [-2.5, 0, 1.5]}
    ctx = {"anchors": marks, "dims": {"chef": [0.6, 1.7, 0.3], "cup": [0.08, 0.1, 0.08]},
           "homes": {"chef": marks["left"], "cup": [0.3, 0.9, 0]}, "colours": {"chef": "#888888", "cup": "#ffffff"}}
    world = {"chef": {"position": list(marks["left"]), "visible": True, "rotationY": 0.0, "scale": 1.0, "fill": None,
                      "pose": "stand", "holding": None},
             "cup": {"position": [0.3, 0.9, 0.0], "visible": True, "rotationY": 0.0, "scale": 1.0, "fill": None}}
    return ctx, world


def _end(name, actor, **raw):
    ctx, world = _ctx_world()
    move = MOVES[name]
    params = move.params.model_validate(raw).model_dump(by_alias=True)
    return move.end(world, {"actor": actor, "params": params}, ctx)


def test_walk_to_arrives_facing_where_it_went():
    chef = _end("walk_to", "chef", to="front")["chef"]
    assert chef["position"] == pytest.approx([0, 0, 0.8])
    assert chef["rotationY"] == pytest.approx(math.pi / 2)  # walked along +x
    short = _end("walk_to", "chef", to="cup")["chef"]  # stops in front of an actor, not inside it
    assert math.dist(short["position"], [0.3, 0, 0]) == pytest.approx(0.45, abs=1e-6)


def test_turn_sit_stand_pick_up_put_down():
    assert _end("turn_to", "chef", to="door")["chef"]["rotationY"] == pytest.approx(math.atan2(-0.9, 0.7))
    assert _end("sit", "chef")["chef"]["pose"] == "sit"
    assert _end("stand_up", "chef")["chef"]["pose"] == "stand"
    held = _end("pick_up", "chef", item="cup")
    assert held["chef"]["holding"] == "cup" and held["cup"]["visible"] is True
    ctx, world = _ctx_world()
    world["chef"]["holding"] = "cup"
    put = MOVES["put_down"]
    after = put.end(world, {"actor": "chef", "params": put.params.model_validate({"on": "stage.center"}).model_dump()}, ctx)
    assert after["chef"]["holding"] is None and after["cup"]["position"] == [0, 0.9, 0]


def test_character_moves_need_a_character():
    with pytest.raises(InvalidInput) as info:
        resolve_script(_inputs(shots=[{"narration": "x", "actions": [{"move": "walk_to", "actor": "bowl", "to": "front"}]}]))
    assert info.value.details["path"] == "shots[0].actions[0].actor"
    with pytest.raises(InvalidInput) as info:
        resolve_script(_inputs(shots=[{"narration": "x", "actions": [{"move": "act", "actor": "chef", "clip": "moonwalk"}]}]))
    assert info.value.details["path"] == "shots[0].actions[0].clip"


def test_character_specs_refuse_unknown_characters():
    with pytest.raises(Exception):
        CharacterSpec.model_validate({"character": "quaternius:wizard"})


import shutil  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

RUNTIME = Path(__file__).resolve().parents[1] / "reelmachine/recipes/explainer3d/runtime"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_runtime_end_states_match_python_for_character_moves():
    ctx, world = _ctx_world()
    holding = {k: dict(v) for k, v in world.items()}
    holding["chef"]["holding"] = "cup"
    cases, expected = [], []
    for name, raw, w in [
        ("walk_to", {"to": "front"}, world), ("walk_to", {"to": "cup", "gait": "jog"}, holding),
        ("walk_to", {"to": "door", "stop": 0.5}, world), ("turn_to", {"to": "door"}, world),
        ("act", {"clip": "dance"}, world), ("talk", {}, world), ("sit", {"on": "front"}, holding),
        ("stand_up", {}, world), ("pick_up", {"item": "cup"}, world), ("put_down", {"on": "stage.center"}, holding),
    ]:
        move = MOVES[name]
        action = {"move": name, "actor": "chef", "params": move.params.model_validate(raw).model_dump(by_alias=True),
                  "t0": 0.0, "t1": 2.0, "seed": 1, "index": 0}
        cases.append({"kind": "action", "world": w, "action": action, "ctx": ctx, "p": 1.0})
        expected.append(move.end(w, action, ctx))
    proc = subprocess.run(["node", str(RUNTIME / "tools/evaluate.mjs")], input=json.dumps({"cases": cases}),
                          capture_output=True, text=True, check=True)
    for case, got, want in zip(cases, json.loads(proc.stdout)["results"], expected):
        assert _close(got, want), (case["action"]["move"], got, want)


def _close(a, b):
    if isinstance(a, dict):
        return set(a) == set(b) and all(_close(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) < 1e-6
    return a == b


def test_characters_take_an_outfit_and_hair():
    spec = CharacterSpec.model_validate({"character": "quaternius:male", "outfit": {"top": "#ff0000", "sleeves": "short"},
                                         "hair": "buzzed", "beard": True})
    assert spec.outfit.top == "#ff0000" and spec.outfit.bottom and spec.hair == "buzzed"
    assert CharacterSpec.model_validate({"character": "quaternius:female"}).hair == "long"
    assert CharacterSpec.model_validate({"character": "quaternius:male"}).hair == "parted"
    assert set(characters.BONE_REGIONS) == {"skin", "top", "sleeve", "bottom", "shoes"}
    assert characters.region_of("hand_r") == "skin" and characters.region_of("foot_l") == "shoes"
    assert characters.region_of("lowerarm_l") == "sleeve" and characters.region_of("thigh_r") == "bottom"
    assert characters.HAIR["buzzed"] == "Hair_Buzzed.gltf"


def test_the_cli_imports_character_packs(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from reelmachine.cli import app

    base, anim = _packs(tmp_path)
    target = tmp_path / "assets"
    # never the real cache: the command must install exactly where asset_cache says
    monkeypatch.setattr("reelmachine.recipes.explainer3d.stages.asset_cache", lambda settings: target)
    result = CliRunner().invoke(app, ["explainer3d", "import-characters", str(base), str(anim)])
    assert result.exit_code == 0, result.output
    assert characters.installed(target)
