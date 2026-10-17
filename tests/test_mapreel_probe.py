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
    inputs = MapReelInputs.model_validate({"segments": segments, **extra})
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
