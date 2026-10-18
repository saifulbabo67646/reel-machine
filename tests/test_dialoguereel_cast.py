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
