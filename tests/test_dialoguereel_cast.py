import pytest

from reelmachine.core.errors import InvalidInput
from reelmachine.recipes.dialoguereel.cast import CAST_ID, cast_id, caller_root, load, resolve, save
from reelmachine.recipes.dialoguereel.models import CastSpec

SPEC = CastSpec.model_validate({"characters": [
    {"id": "kid", "name": "Cupcake", "side": "left", "source": {"kind": "builtin", "name": "cupcake"}},
    {"id": "mom", "name": "Mug", "side": "right", "source": {"kind": "builtin", "name": "mug"}},
]})


def with_voice(spec, voice):
    data = spec.model_dump()
    data["characters"][0]["voice"] = voice
    return CastSpec.model_validate(data)


def with_colour(spec, colour):
    data = spec.model_dump()
    data["characters"][0]["source"]["colors"] = {"primary": colour}
    return CastSpec.model_validate(data)


def with_svg(spec, index, svg):
    data = spec.model_dump()
    data["characters"][index]["source"] = {"kind": "svg", "svg": svg}
    return CastSpec.model_validate(data)


def test_the_id_ignores_voices_but_not_looks():
    a = cast_id(SPEC); b = cast_id(with_voice(SPEC, "elevenlabs:x")); c = cast_id(with_colour(SPEC, "#000000"))
    assert a == b != c and CAST_ID.match(a)


def test_save_then_load_round_trips_with_voices(tmp_path):
    ident = save(tmp_path, with_voice(SPEC, "elevenlabs:x"))
    assert load(tmp_path, ident).characters[0].voice == "elevenlabs:x"
    assert (tmp_path / "casts" / f"{ident}.json").is_file()


@pytest.mark.parametrize("ident", ["cast_000000000000", "../casts/x", "cast_ZZZ", ""])
def test_unknown_or_pathlike_ids_are_refused(tmp_path, ident):
    with pytest.raises(InvalidInput) as err:
        load(tmp_path, ident)
    assert "make_cast" in err.value.hint and err.value.details["path"] == "cast"


def test_resolve_builds_both_characters():
    cast = resolve(SPEC)
    assert cast.id == cast_id(SPEC) and [c.id for c in cast.characters] == ["kid", "mom"]
    assert all(c.svg.startswith("<svg") for c in cast.characters)


def test_resolve_names_the_character_whose_svg_fails():
    with pytest.raises(InvalidInput) as err:
        resolve(with_svg(SPEC, 1, "<svg/>"))
    assert err.value.details["path"] == "cast.characters[1].source.svg"


def test_resolve_names_an_unknown_builtin():
    data = SPEC.model_dump()
    data["characters"][0]["source"]["name"] = "nope"
    with pytest.raises(InvalidInput) as err:
        resolve(CastSpec.model_validate(data))
    assert err.value.details["path"] == "cast.characters[0].source.name"


def test_local_store_exposes_the_caller_dir(tmp_path):
    from reelmachine.jobs.local import LocalJobStore
    from reelmachine.jobs.memory import InMemoryJobStore
    assert LocalJobStore(tmp_path).caller_dir("alice") == tmp_path / "alice"
    assert InMemoryJobStore(tmp_path).caller_dir("alice") == tmp_path / "alice"


def test_caller_root_follows_the_job_workdir(tmp_path):
    from types import SimpleNamespace
    ctx = SimpleNamespace(job=SimpleNamespace(id="j1"),
                          config=SimpleNamespace(workdir=tmp_path / "alice" / "j1", cache_dir=tmp_path / "c"))
    assert caller_root(ctx) == tmp_path / "alice"
    ctx.config.workdir = tmp_path / "other"
    assert caller_root(ctx) == tmp_path / "c"


def test_the_script_stage_saves_an_inline_cast_only_once_it_resolves(tmp_path):
    from types import SimpleNamespace

    from reelmachine.core.errors import InvalidInput
    from reelmachine.recipes.dialoguereel.stages import ScriptStage
    ctx = SimpleNamespace(job=SimpleNamespace(id="j1"),
                          config=SimpleNamespace(workdir=tmp_path / "alice" / "j1", cache_dir=tmp_path / "c"))
    bad = {"characters": [
        {"id": "kid", "name": "K", "side": "left", "source": {"kind": "svg", "svg": '<svg viewBox="0 0 400 400"/>'}},
        {"id": "mom", "name": "M", "side": "right", "source": {"kind": "builtin", "name": "mug"}}]}
    with pytest.raises(InvalidInput) as err:
        ScriptStage().run(ctx, {"cast": bad, "lines": [], "mode": "cast"})
    assert err.value.stage == "script" and not (tmp_path / "alice" / "casts").exists()
    good = {"characters": [{**bad["characters"][0], "source": {"kind": "builtin", "name": "cupcake"}},
                           bad["characters"][1]]}
    out = ScriptStage().run(ctx, {"cast": good, "lines": [], "mode": "cast"})
    assert (tmp_path / "alice" / "casts" / f"{out.cast.id}.json").is_file()


# ---------------------------------------------------------------- image characters

def _with_image(source):
    data = SPEC.model_dump()
    data["characters"][1]["source"] = {"kind": "image", **source}
    return CastSpec.model_validate(data)


FAKE_IMAGE = _with_image({"provider": "fake", "description": "a round dumpling"})


def _stage_ctx(tmp_path, caller="local"):
    import dataclasses
    from types import SimpleNamespace

    from reelmachine.config import Settings
    from reelmachine.core.stage import NeverCancel
    settings = dataclasses.replace(Settings(), workdir=tmp_path / "alice" / "j1")
    return SimpleNamespace(job=SimpleNamespace(id="j1", caller=caller), workdir=tmp_path / "alice" / "j1",
                           config=settings, cancel=NeverCancel())


def _script_out(spec, mode="storyboard"):
    from reelmachine.recipes.dialoguereel.models import DialogueReelInputs, ScriptOutput
    inputs = DialogueReelInputs.model_validate({"cast": spec.model_dump(), "lines": [], "mode": mode})
    return ScriptOutput(inputs=inputs, cast=resolve(spec))


def test_resolve_image_fetches_nothing():
    from reelmachine.recipes.dialoguereel import images
    r = resolve(FAKE_IMAGE)
    char = r.characters[1]
    assert char.kind == "image" and char.poses == {} and char.parts == list(images.POSES) and char.svg == ""
    assert r.characters[0].kind == "svg"
    supplied = resolve(_with_image({"poses": {"talk": "/nowhere/t.png", "neutral": "https://x.invalid/n.png"}}))
    assert supplied.characters[1].parts == ["neutral", "talk"]


def test_cast_folder_and_poses_on_disk(tmp_path):
    from reelmachine.recipes.dialoguereel.cast import cast_folder, poses_on_disk
    r = resolve(FAKE_IMAGE)
    folder = cast_folder(tmp_path, r.id, "mom")
    assert folder == tmp_path / "casts" / r.id / "mom"
    assert poses_on_disk(tmp_path, r) == 4
    folder.mkdir(parents=True)
    (folder / "neutral.png").write_bytes(b"x")
    assert poses_on_disk(tmp_path, r) == 3
    assert poses_on_disk(tmp_path, resolve(_with_image({"poses": {"neutral": "/n.png"}}))) == 0
    assert poses_on_disk(tmp_path, resolve(SPEC)) == 0


def test_cast_stage_makes_fake_poses(tmp_path):
    from pathlib import Path

    from reelmachine.recipes.dialoguereel import images
    from reelmachine.recipes.dialoguereel.cast import cast_folder
    from reelmachine.recipes.dialoguereel.stages import CastStage
    ctx = _stage_ctx(tmp_path)
    script = _script_out(FAKE_IMAGE)
    out = CastStage().run(ctx, script)
    char = out.cast.characters[1]
    assert set(char.poses) == set(images.POSES) and all(Path(p).is_file() for p in char.poses.values())
    assert Path(char.poses["talk"]).parent == cast_folder(tmp_path / "alice", out.cast.id, "mom")
    assert char.parts == list(images.POSES)
    assert out.cast.characters[0] == script.cast.characters[0]
    assert not (ctx.workdir / "artifacts").exists()   # pose artifacts are for cast mode


def test_cast_stage_reuses_poses_and_retries_only_missing(tmp_path, monkeypatch):
    from reelmachine.engine.executor import ledger_total
    from reelmachine.recipes.dialoguereel import images
    from reelmachine.recipes.dialoguereel.cast import cast_folder
    from reelmachine.recipes.dialoguereel.stages import CastStage

    class Counting(images.FakeImages):
        name = "openai"
        calls = 0

        def generate(self, prompt):
            Counting.calls += 1
            return super().generate(prompt)

        def edit(self, image, prompt):
            Counting.calls += 1
            if "smiling" in prompt and Counting.fail:
                raise images.ProviderUnavailable("openai refused the image (HTTP 400)")
            return super().edit(image, prompt)

    Counting.fail = False
    monkeypatch.setattr(images, "generator_for", lambda provider, settings: Counting())
    spec = _with_image({"provider": "openai", "description": "a round dumpling"})
    ctx = _stage_ctx(tmp_path)
    CastStage().run(ctx, _script_out(spec))
    ledger = tmp_path / "alice" / "quota" / "openai.json"
    assert Counting.calls == 4 and ledger_total(ledger) == 4
    happy = cast_folder(tmp_path / "alice", resolve(spec).id, "mom") / "happy.png"
    happy.unlink()
    out = CastStage().run(ctx, _script_out(spec))
    assert Counting.calls == 5 and ledger_total(ledger) == 5 and out.cast.characters[1].parts == list(images.POSES)
    happy.unlink()
    Counting.fail = True   # a refused call is not billed, and the pose falls back to neutral
    out = CastStage().run(ctx, _script_out(spec))
    assert Counting.calls == 6 and ledger_total(ledger) == 5
    char = out.cast.characters[1]
    assert char.parts == ["neutral", "talk", "surprised"] and "happy" not in char.poses
    assert any("happy pose failed" in w and "cast.characters[1].source" in w for w in out.warnings)


def test_a_remote_caller_cannot_read_local_poses(tmp_path):
    from reelmachine.recipes.dialoguereel.stages import CastStage
    spec = _with_image({"poses": {"neutral": str(tmp_path / "n.png")}})
    with pytest.raises(InvalidInput) as err:
        CastStage().run(_stage_ctx(tmp_path, caller="alice"), _script_out(spec))
    assert err.value.details["path"] == "cast.characters[1].source.poses.neutral" and err.value.stage == "cast"


def test_cast_mode_publishes_pose_artifacts(tmp_path):
    from reelmachine.recipes.dialoguereel.stages import CastStage
    ctx = _stage_ctx(tmp_path)
    CastStage().run(ctx, _script_out(FAKE_IMAGE, mode="cast"))
    arts = ctx.workdir / "artifacts"
    assert (arts / "cast-mom-talk.png").is_file()
    assert sorted(p.name for p in arts.iterdir()) == [f"cast-mom-{p}.png" for p in
                                                      ("happy", "neutral", "surprised", "talk")]


def test_the_recipe_runs_the_cast_stage_before_narration():
    from reelmachine.recipes.dialoguereel.recipe import DialogueReelRecipe
    stages = DialogueReelRecipe.spec.stages
    assert [s.stage.id for s in stages[:3]] == ["script", "cast", "narration"]
    assert stages[1].feeds == "script" and stages[2].feeds == "cast"
