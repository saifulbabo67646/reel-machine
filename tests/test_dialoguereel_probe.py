"""dialoguereel's probe: script, lint, renderer and voice checks, without writing anything."""

import copy
import dataclasses
import json
import tempfile
from pathlib import Path

from reelmachine.config import get_settings
from reelmachine.core.job import JobRequest
from reelmachine.core.recipe import ProbeContext
from reelmachine.core.stage import StaticProviders
from reelmachine.engine import Engine
from reelmachine.recipes.dialoguereel import cast
from reelmachine.recipes.dialoguereel.models import CastSpec, DialogueReelInputs
from reelmachine.recipes.dialoguereel.recipe import EXAMPLE, DialogueReelRecipe
from reelmachine.recipes.doodle.narration.fake import FakeNarration
from reelmachine.recipes.explainer3d.fake import FakeShotRenderer

HOSTILE = '<svg viewBox="0 0 400 400"><script>alert(1)</script></svg>'


def _settings(tmp_path):
    return dataclasses.replace(get_settings(), workdir=tmp_path / "w", shared_cache=tmp_path / "shared")


def probe(raw, tmp_path=None, *, root=None):
    base = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    settings = _settings(base)
    providers = StaticProviders({"narration": FakeNarration(settings), "renderer": FakeShotRenderer(settings)})
    ctx = ProbeContext(config=settings, providers=providers, caller_root=root)
    return DialogueReelRecipe().probe(DialogueReelInputs.model_validate(raw), ctx)


def with_line_speaker(raw, index, speaker):
    out = copy.deepcopy(raw)
    out["lines"][index]["speaker"] = speaker
    return out


def _check(report, name):
    return next(c for c in report.checks if c.name == name)


def test_the_example_probes_clean():
    report = probe(EXAMPLE)
    assert report.ok, report.checks
    assert report.details["cast"].startswith("cast_") and report.details["estimatedMs"] > 1500
    assert len(report.details["lines"]) == len(EXAMPLE["lines"])
    assert report.details["lines"][0] == {"index": 0, "estimatedMs": report.details["lines"][0]["estimatedMs"]}
    assert report.details["cost"]["ttsCharacters"] == sum(len(line["text"]) for line in EXAMPLE["lines"])
    assert {c.name for c in report.checks} == {"script", "lint", "renderer", "voice"}


def test_an_unknown_speaker_fails_with_its_path():
    report = probe(with_line_speaker(EXAMPLE, 2, "dad") | {"mode": "full"})
    assert not report.ok and not _check(report, "lint").ok
    assert "lines[2].speaker" in report.details["lint"][0]["message"] + json.dumps(report.details)


def test_lint_errors_are_suggestions_outside_a_strict_full_build():
    for extra in ({"mode": "storyboard"}, {"mode": "full", "lint": "warn"}):
        report = probe(with_line_speaker(EXAMPLE, 2, "dad") | extra)
        assert _check(report, "lint").ok and report.details["lint"][0]["rule"] == "unknown-speaker"


def test_a_voice_from_another_provider_fails_the_voice_check():
    raw = copy.deepcopy(EXAMPLE)
    raw["cast"]["characters"][1]["voice"] = "elevenlabs:Rachel"
    report = probe(raw)
    voice = _check(report, "voice")
    assert not report.ok and not voice.ok and "elevenlabs" in voice.detail and "mom" in voice.detail


def test_hostile_svg_fails_the_script_check_with_its_path():
    raw = copy.deepcopy(EXAMPLE)
    raw["cast"]["characters"][0]["source"] = {"kind": "svg", "svg": HOSTILE}
    report = probe(raw)
    script = _check(report, "script")
    assert not report.ok and not script.ok
    assert report.details["error"]["path"] == "cast.characters[0].source.svg"
    assert script.detail.startswith("cast.characters[0].source.svg:")


def test_probe_writes_nothing(tmp_path):
    root = tmp_path / "caller"
    report = probe(EXAMPLE, tmp_path, root=root)
    assert report.ok
    assert not list(tmp_path.rglob("casts")) and not root.exists()


def test_a_saved_cast_id_is_found_in_the_callers_root(tmp_path):
    root = tmp_path / "caller"
    ident = cast.save(root, CastSpec.model_validate(EXAMPLE["cast"]))
    report = probe(EXAMPLE | {"cast": ident}, tmp_path, root=root)
    assert report.ok, report.checks
    assert report.details["cast"] == ident


def test_an_unknown_cast_id_fails_the_script_check(tmp_path):
    report = probe(EXAMPLE | {"cast": "cast_000000000000"}, tmp_path, root=tmp_path / "caller")
    assert not report.ok and report.details["error"]["path"] == "cast"
    assert "make_cast" in report.details["error"]["hint"]


def test_the_engine_probes_with_the_callers_root(tmp_path):
    settings = _settings(tmp_path)
    engine = Engine(settings, provider_overrides={"renderer": "explainer3d-fake"})
    try:
        ident = cast.save(engine.store.caller_dir("someone"), CastSpec.model_validate(EXAMPLE["cast"]))
        assert engine.probe("dialoguereel", EXAMPLE | {"cast": ident}, caller="someone").ok
        assert not engine.probe("dialoguereel", EXAMPLE | {"cast": ident}, caller="other").ok
    finally:
        engine.close()


def test_the_spec_declares_its_providers_and_artifacts():
    spec = DialogueReelRecipe.spec
    assert [s.stage.id for s in spec.stages] == ["script", "narration", "compose", "preview", "render"]
    assert spec.providers["narration"].default == "fake" and spec.providers["renderer"].default == "hyperframes"
    assert set(spec.artifacts) == {"reel.mp4", "captions.srt", "reel.json", "storyboard.jpg", "cast.png",
                                   "script.resolved.json"}
    assert spec.example["mode"] == "storyboard" and JobRequest(recipe="dialoguereel", inputs=dict(spec.example))


def test_caption_text_keeps_full_width_spaces():
    from reelmachine.recipes.dialoguereel.stages import caption_text

    assert caption_text("はい　そうです", "Yes  that") == "はい　そうです\nYes  that"
    assert caption_text("雨{あめ}\r\nです", "Rain\n\nit is") == "雨 です\nRain it is"


def test_plan_of_round_trips_every_section():
    from reelmachine.recipes.dialoguereel.models import LineWindowModel, NarrationOutput, ScriptOutput
    from reelmachine.recipes.dialoguereel.schedule import schedule
    from reelmachine.recipes.dialoguereel.stages import plan_of

    plan = schedule([900, 1300, 700], slow=[True, False, True], listen_first=True, recap=True, quiz_line=1)

    def model(w):
        return LineWindowModel(start_ms=w.start_ms, speech_ms=w.speech_ms, end_ms=w.end_ms, meaning_ms=w.meaning_ms,
                               slow_start_ms=w.slow_start_ms, slow_ms=w.slow_ms)

    script = ScriptOutput(inputs=DialogueReelInputs.model_validate(EXAMPLE), cast=cast.resolve(
        CastSpec.model_validate(EXAMPLE["cast"])))
    narration = NarrationOutput(
        script=script, duration_ms=plan.duration_ms, lines=[model(w) for w in plan.lines],
        listen=[model(w) for w in plan.listen], bridge_ms=plan.bridge_ms, recap_ms=plan.recap_ms,
        quiz={"line": 1, "start_ms": plan.quiz.start_ms, "reveal_ms": plan.quiz.reveal_ms,
              "end_ms": plan.quiz.end_ms})
    assert plan_of(narration) == plan
    plain = schedule([900, 1300])
    assert plan_of(NarrationOutput(script=script, duration_ms=plain.duration_ms,
                                   lines=[model(w) for w in plain.lines])) == plain


def test_the_example_teaches_with_keys_that_match_and_fit_its_level():
    report = probe(EXAMPLE)
    rules = {f["rule"] for f in report.details["lint"]}
    assert not rules & {"key-missing", "level", "key-count"}, report.details["lint"]
    keyed = [line for line in EXAMPLE["lines"] if line.get("keys")]
    assert 4 <= len(keyed) <= 5 and EXAMPLE["quiz"] is True and EXAMPLE["scene"]
    assert len({line.get("emotion", "neutral") for line in EXAMPLE["lines"]} - {"neutral"}) >= 2
    assert EXAMPLE["lines"][1]["slow"] is True
