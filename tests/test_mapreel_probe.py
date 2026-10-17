import dataclasses
import shutil
from pathlib import Path

from reelmachine.config import get_settings
from reelmachine.core.recipe import ProbeContext
from reelmachine.recipes.mapreel import geodata
from reelmachine.recipes.mapreel.models import MapReelInputs
from reelmachine.recipes.mapreel.recipe import MapReelRecipe

FIXTURE = Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson"


class _Providers(dict):
    pass


class _Renderer:
    name = "fake"

    def missing(self):
        return []


class _Voice:
    name = "fake"


def _ctx(tmp_path):
    settings = dataclasses.replace(get_settings(), workdir=tmp_path / "w", shared_cache=tmp_path / "shared")
    root = geodata.geo_cache(settings)
    root.mkdir(parents=True)
    shutil.copy2(FIXTURE, root / geodata.FILES["countries"][0])
    return ProbeContext(config=settings, providers=_Providers(renderer=_Renderer(), narration=_Voice()))


def _probe(tmp_path, segments, **extra):
    inputs = MapReelInputs.model_validate({"segments": segments, "lint": "warn", **extra})
    return MapReelRecipe().probe(inputs, _ctx(tmp_path))


def test_a_good_script_probes_ok_with_estimates(tmp_path):
    report = _probe(tmp_path, [{"kind": "country", "country": "Italy", "narration": "Italy has Gucci and Prada.",
                                "items": [{"name": "Gucci"}, {"name": "Prada"}]}])
    assert report.ok, report.checks
    seg = report.details["segments"][0]
    assert seg["codes"] == ["ITA"] and seg["estimatedMs"] >= 2200
    assert report.details["cost"]["ttsCharacters"] == len("Italy has Gucci and Prada.")


def test_a_bad_place_fails_with_its_path_and_suggestion(tmp_path):
    report = _probe(tmp_path, [{"kind": "highlight", "countries": ["ITA", "Itally"], "narration": "x"}])
    assert not report.ok
    assert report.details["error"]["path"] == "segments[0].countries[1]"
    assert "Italy" in report.details["error"]["hint"]


def test_a_voice_the_deployment_cannot_serve_fails_the_voice_check(tmp_path):
    report = _probe(tmp_path, [{"kind": "country", "country": "ITA", "narration": "x", "items": [{"name": "a"}]}],
                    voice="cartesia:abc")
    voice = next(c for c in report.checks if c.name == "voice")
    assert not voice.ok and not report.ok


def test_the_guide_explains_the_workflow():
    guide = MapReelRecipe.spec.guide
    assert any("find_places" in step for step in guide["workflow"])
    assert any("hook" in step.lower() for step in guide["workflow"])


def test_a_map_data_fetch_failure_still_reports_the_other_checks(tmp_path, monkeypatch):
    from reelmachine.core.errors import ProviderUnavailable
    from reelmachine.recipes.mapreel import recipe as mod

    def boom(settings):
        raise ProviderUnavailable("offline", hint="check the network")

    monkeypatch.setattr(mod, "atlas_for", boom)
    report = _probe(tmp_path, [{"kind": "country", "country": "ITA", "narration": "x", "items": [{"name": "a"}]}])
    assert not report.ok
    names = {c.name: c for c in report.checks}
    assert not names["map_data"].ok and "offline" in names["map_data"].detail
    assert "renderer" in names and "voice" in names
    assert report.details["cost"]["ttsCharacters"] == 1


def test_a_target_length_stretches_the_estimates(tmp_path):
    segs = [{"kind": "country", "country": "ITA", "narration": "Italy is here.", "items": [{"name": "a"}]}] * 3
    plain = _probe(tmp_path / "a", segs)
    fitted = _probe(tmp_path / "b", segs, length=30)
    assert fitted.details["estimatedMs"] >= plain.details["estimatedMs"]


ITALY = [{"kind": "country", "country": "ITA", "narration": "Italy has Gucci.", "items": [{"name": "Gucci"}]}]


def test_a_missing_music_file_fails_the_script_check(tmp_path):
    report = _probe(tmp_path, ITALY, music=str(tmp_path / "nope.mp3"))
    script = next(c for c in report.checks if c.name == "script")
    assert not report.ok and not script.ok and "music" in script.detail
    assert report.details["error"]["path"] == "music" and "auto" in report.details["error"]["hint"]


def test_auto_music_and_an_existing_file_probe_ok(tmp_path):
    assert _probe(tmp_path, ITALY, music="auto").ok
    track = tmp_path / "bed.wav"
    track.write_bytes(b"RIFF")
    assert _probe(tmp_path / "again", ITALY, music=str(track)).ok


def test_the_script_stage_refuses_a_missing_music_file(tmp_path):
    from types import SimpleNamespace

    import pytest

    from reelmachine.core.errors import InvalidInput
    from reelmachine.recipes.mapreel.stages import ScriptStage

    ctx = SimpleNamespace(config=_ctx(tmp_path).config)
    with pytest.raises(InvalidInput) as err:
        ScriptStage().run(ctx, {"segments": ITALY, "lint": "warn", "music": "~/no/such/track.mp3"})
    assert err.value.stage == "script" and err.value.details["path"] == "music"
    assert ScriptStage().run(ctx, {"segments": ITALY, "lint": "warn", "music": "auto"}).inputs.music == "auto"


def test_a_bad_region_fails_probe_with_its_path(tmp_path):
    ctx = _ctx(tmp_path)
    shutil.copy2(FIXTURE.parent / "regions.geojson", geodata.geo_cache(ctx.config) / geodata.FILES["regions"][0])
    segs = [{"kind": "regions", "country": "USA", "narration": "Texas.", "regions": [{"region": "Texsa"}]}]
    report = MapReelRecipe().probe(MapReelInputs.model_validate({"segments": segs, "lint": "warn"}), ctx)
    assert not report.ok and report.details["error"]["path"] == "segments[0].regions[0].region"
    segs[0]["regions"][0]["region"] = "Texas"
    assert MapReelRecipe().probe(MapReelInputs.model_validate({"segments": segs, "lint": "warn"}), ctx).ok


def test_a_weak_opener_fails_the_hook_check_in_a_build(tmp_path):
    seg = [{"kind": "country", "country": "ITA", "narration": "Welcome to Italy, it has Gucci.",
            "items": [{"name": "Gucci"}]}]
    report = _probe(tmp_path, seg, lint="strict")
    hook = next(c for c in report.checks if c.name == "hook")
    assert not hook.ok and not report.ok
    assert any(f["rule"] == "weak-opener" for f in report.details["lint"])


def test_the_script_stage_refuses_a_weak_hook_in_a_strict_build(tmp_path):
    from types import SimpleNamespace

    import pytest

    from reelmachine.core.errors import InvalidInput
    from reelmachine.recipes.mapreel.stages import ScriptStage

    ctx = SimpleNamespace(config=_ctx(tmp_path).config)
    weak = [{"kind": "country", "country": "ITA", "narration": "Welcome to Italy, it has Gucci.",
             "items": [{"name": "Gucci"}]}]
    with pytest.raises(InvalidInput) as err:
        ScriptStage().run(ctx, {"segments": weak})
    assert err.value.details == {"path": "segments[0].narration", "rule": "weak-opener"}
    out = ScriptStage().run(ctx, {"segments": weak, "mode": "storyboard"})
    assert any(f["rule"] == "weak-opener" and f["severity"] == "warning" for f in out.lint)


def test_a_weak_opener_only_warns_in_a_storyboard_or_with_lint_warn(tmp_path):
    seg = [{"kind": "country", "country": "ITA", "narration": "Welcome to Italy, it has Gucci.",
            "items": [{"name": "Gucci"}]}]
    for name, extra in (("a", {"mode": "storyboard"}), ("b", {"lint": "warn"})):
        report = _probe(tmp_path / name, seg, **extra)
        assert next(c for c in report.checks if c.name == "hook").ok, extra
        assert any(f["rule"] == "weak-opener" and f["severity"] == "warning" for f in report.details["lint"])
