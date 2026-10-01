"""The doodle recipe: deterministic specs, two render modes, and the quality rules.

Fast tests cover spec determinism, structure choice, the narration-driven reveal
schedule, the cloud narration parsers and the loud voice failure. Slow tests render:
stroke mode with fakes (plus the ink-accumulation assertion), program mode behind its
optional dependencies, preflight refusal and the no-browser/no-Node check.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import httpx
import numpy as np
import pytest

from reelmachine import ffmpeg
from reelmachine.config import get_settings
from reelmachine.core.errors import VoiceUnavailable
from reelmachine.core.job import JobRequest, JobState
from reelmachine.core.timeline import WordSpan
from reelmachine.engine import Engine
from reelmachine.recipes.doodle.models import Beat, DoodleInputs
from reelmachine.recipes.doodle.narration.cloud import (
    CartesiaNarration,
    ElevenLabsNarration,
    parse_cartesia,
    parse_elevenlabs,
    words_from_characters,
)
from reelmachine.recipes.doodle.scenes.spec import (
    choose_structure,
    program_scene,
    stroke_scene,
)
from reelmachine.recipes.doodle.scenes.stroke import StrokeRenderer, build_annotation

WORDS = [
    WordSpan(text="Every", start_ms=0, end_ms=300),
    WordSpan(text="idea", start_ms=320, end_ms=700),
    WordSpan(text="starts", start_ms=900, end_ms=1400),
    WordSpan(text="small", start_ms=1600, end_ms=2000),
    WordSpan(text="Then", start_ms=2600, end_ms=2900),
    WordSpan(text="it", start_ms=2950, end_ms=3100),
    WordSpan(text="grows", start_ms=3200, end_ms=3700),
]


def _beat() -> Beat:
    return Beat(id="beat-1", narration="Every idea starts small. Then it grows.", keywords=["Idea", "Growth"])


def _settings(tmp_path: Path):
    return dataclasses.replace(
        get_settings(),
        workdir=tmp_path / "work",
        outdir=tmp_path / "out",
        mock_dir=tmp_path / "mock",
    )


def _submit_doodle(tmp_path, inputs: dict, *, timeout_s: float = 900):
    engine = Engine(_settings(tmp_path))
    try:
        job = engine.submit(JobRequest(recipe="doodle", inputs=inputs), caller="local")
        finished = engine.wait(job.id, caller="local", timeout_s=timeout_s)
    finally:
        engine.close()
    return finished


# ------------------------------------------------------------------ fast: specs


def test_program_specs_are_deterministic_and_carry_precise_text() -> None:
    first = program_scene(_beat(), 1, canvas=(1080, 1920), duration_ms=4000, words=WORDS)
    second = program_scene(_beat(), 1, canvas=(1080, 1920), duration_ms=4000, words=WORDS)
    assert first.model_dump() == second.model_dump()

    texts = [element.text for element in first.elements]
    assert "Idea" in texts  # the keyword is authoritative on screen
    assert any(element.kind == "card" for element in first.elements)
    assert any(element.kind == "arrow" for element in first.elements), "a two-card relationship is drawn"
    # entries are anchored to word timings, not evenly divided
    asserts = {element.enter_ms for element in first.elements}
    assert 0 in asserts and any(word.start_ms in asserts for word in WORDS[1:])
    assert any(WORDS[2].start_ms <= value <= WORDS[3].start_ms for value in asserts)


def test_structure_choice_prefers_multi_act_for_vertical() -> None:
    beats = [Beat(id=f"beat-{index}", narration="line") for index in range(1, 4)]
    vertical = DoodleInputs(topic="x", aspect="vertical")
    landscape = DoodleInputs(topic="x", aspect="landscape")
    assert choose_structure(vertical, beats) == "multi_act"
    assert choose_structure(landscape, beats) == "single"
    assert choose_structure(vertical, beats[:1]) == "single"
    assert choose_structure(DoodleInputs(topic="x", scene_structure="dual_islands"), beats) == "dual_islands"


def test_reveal_schedule_is_read_from_narration_timings() -> None:
    scene = stroke_scene(
        _beat(),
        1,
        structure="dual_islands",
        words=WORDS,
        duration_ms=4600,
        canvas=(1080, 1920),
    )
    from reelmachine.recipes.doodle.scenes.spec import split_words

    left_words, right_words = split_words(WORDS, 0.5)
    left, right = scene.regions
    # the seam is the word where the narration splits — not the midpoint of the window
    assert left.start_ms == left_words[0].start_ms
    assert right.start_ms == right_words[0].start_ms
    assert left.duration_ms >= left_words[-1].end_ms - left_words[0].start_ms
    assert left.duration_ms > 0 and right.duration_ms > 0
    # the ink finishes before the window does, leaving room for the gaze tail
    assert right.start_ms + right.duration_ms <= 4600 - 400


def test_stroke_annotation_is_canvas_checked() -> None:
    scene = stroke_scene(_beat(), 1, structure="single", words=WORDS, duration_ms=4000, canvas=(1000, 500))
    annotation = build_annotation(scene, art_size=(500, 500))
    assert annotation.canvas == {"width": 500, "height": 500}
    assert annotation.elements[0].region.width == 500

    bad = scene.model_copy(
        update={
            "regions": [
                scene.regions[0].model_copy(
                    update={"region": scene.regions[0].region.model_copy(update={"x": 900, "width": 400})}
                )
            ]
        }
    )
    with pytest.raises(ValueError):
        build_annotation(bad, art_size=(500, 500))


# ------------------------------------------------------------- fast: narration


def test_character_alignment_becomes_word_spans() -> None:
    spans = words_from_characters(
        list("hi there"),
        [0.0, 0.05, 0.1, 0.2, 0.3, 0.35, 0.4, 0.45],
        [0.05, 0.1, 0.2, 0.3, 0.35, 0.4, 0.45, 0.5],
    )
    assert [span.text for span in spans] == ["hi", "there"]
    assert spans[0].start_ms == 0 and spans[0].end_ms == 100
    assert spans[1].start_ms == 200 and spans[1].end_ms == 500


def test_cloud_payload_parsers() -> None:
    import base64

    audio, spans = parse_elevenlabs(
        {
            "audio_base64": base64.b64encode(b"mp3-bytes").decode(),
            "alignment": {
                "characters": list("ok go"),
                "character_start_times_seconds": [0.0, 0.1, 0.2, 0.3, 0.4],
                "character_end_times_seconds": [0.1, 0.2, 0.3, 0.4, 0.5],
            },
        }
    )
    assert audio == b"mp3-bytes"
    assert [span.text for span in spans] == ["ok", "go"]

    audio, spans = parse_cartesia(
        {
            "audio": base64.b64encode(b"wav").decode(),
            "word_timestamps": [{"word": "hello", "start": 0.0, "end": 0.4}],
        }
    )
    assert audio == b"wav"
    assert spans[0].text == "hello" and spans[0].end_ms == 400


def test_cloud_narration_requires_a_key(monkeypatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    provider = ElevenLabsNarration()
    assert provider.missing()
    with pytest.raises(VoiceUnavailable):
        provider.synthesize("hello", voice="elevenlabs:Rachel")


def test_elevenlabs_synthesis_over_a_stubbed_transport(tmp_path, monkeypatch) -> None:
    import base64

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1/voices":
            return httpx.Response(200, json={"voices": [{"voice_id": "Rachel", "name": "Rachel"}]})
        return httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(b"audio").decode(),
                "alignment": {
                    "characters": list("hello world"),
                    "character_start_times_seconds": [index * 0.1 for index in range(11)],
                    "character_end_times_seconds": [(index + 1) * 0.1 for index in range(11)],
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = ElevenLabsNarration(client=client, base_url="https://api.example.test", workdir=tmp_path)
    speech = provider.synthesize("hello world", voice="elevenlabs:Rachel")
    assert speech.path.read_bytes() == b"audio"
    assert [span.text for span in speech.segments[0].words] == ["hello", "world"]
    assert provider.missing() == []

    with pytest.raises(VoiceUnavailable):
        provider.synthesize("hello", voice="elevenlabs:Definitely-Not-There")


def test_cartesia_missing_key_is_reported(monkeypatch) -> None:
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    provider = CartesiaNarration()
    assert "CARTESIA_API_KEY" in provider.missing()[0]
    assert provider.describe()["group"] == "narration"


def test_narration_timings_are_global_and_drive_a_real_reveal(tmp_path) -> None:
    """A beat's words arrive on the beat's clock; they must be shifted onto the reel's.

    When they were not, the second scene's reveal window collapsed to its 200 ms floor
    and the art appeared at once — found by driving the MCP server live.
    """
    from reelmachine.core.assets import AssetStore
    from reelmachine.core.job import Job, JobRequest
    from reelmachine.core.stage import NullProgress, StageContext, StaticProviders
    from reelmachine.recipes.doodle.narration.fake import FakeNarration
    from reelmachine.recipes.doodle.stages import NarrationStage, ScenesStage, ScriptStage

    settings = get_settings()
    ctx = StageContext(
        job=Job(id="t", recipe="doodle", request=JobRequest(recipe="doodle")),
        workdir=tmp_path,
        assets=AssetStore(tmp_path / "assets"),
        config=settings,
        providers=StaticProviders({"narration": FakeNarration(settings)}),
        progress=NullProgress(),
    )
    script = ScriptStage().run(
        ctx,
        {
            "script": [
                {"narration": "First line.", "keywords": ["One"]},
                {"narration": "Second line here.", "keywords": ["Two"]},
            ],
            "mode": "stroke",
        },
    )
    narration = NarrationStage().run(ctx, script)
    scenes = ScenesStage().run(ctx, narration)

    second = narration.segments[1]
    assert second.words, "the fake voice reports word timings"
    assert all(second.start_ms <= word.start_ms < second.end_ms for word in second.words)
    assert second.words[-1].end_ms > narration.segments[0].end_ms  # global, not beat-local

    scene = scenes.scenes[1]
    region = scene.regions[0]
    assert region.start_ms == 0
    assert region.duration_ms > 500, "the reveal window must be the narration's, not a floor"
    assert region.duration_ms <= scene.duration_ms - 400  # room for the gaze tail


def test_doodle_probe_validates_the_voice() -> None:
    """A voice the provider does not serve fails the probe with a clear check."""
    from reelmachine.core.recipe import ProbeContext
    from reelmachine.core.stage import StaticProviders
    from reelmachine.recipes.doodle.narration.fake import FakeNarration
    from reelmachine.recipes.doodle.recipe import DoodleRecipe
    from reelmachine.recipes.doodle.scenes.program import ProgramRenderer
    from reelmachine.recipes.doodle.scenes.stroke import StrokeRenderer

    settings = get_settings()
    ctx = ProbeContext(
        config=settings,
        providers=StaticProviders(
            {
                "narration": FakeNarration(settings),
                "renderer_stroke": StrokeRenderer(settings),
                "renderer_program": ProgramRenderer(settings),
            }
        ),
    )
    good = DoodleRecipe().probe(DoodleInputs(topic="x", mode="stroke"), ctx)
    assert good.ok is True, [(check.name, check.detail) for check in good.checks]

    bad = DoodleRecipe().probe(DoodleInputs(topic="x", voice="fake:missing"), ctx)
    assert bad.ok is False
    assert any(check.name == "voice_exists" and not check.ok for check in bad.checks)


# ------------------------------------------------------------------ slow: jobs


@pytest.mark.slow
def test_missing_voice_fails_loudly_with_no_artifact(tmp_path) -> None:
    finished = _submit_doodle(
        tmp_path,
        {
            "script": [{"narration": "One line.", "keywords": ["One"]}],
            "mode": "program",
            "voice": "elevenlabs:Rachel",  # the deployment serves the fake voice
            "name": "no-voice",
        },
    )
    assert finished.state is JobState.FAILED
    assert finished.error is not None and finished.error.code == "VOICE_UNAVAILABLE"
    assert "REEL_TTS" in finished.error.hint
    names = {artifact.name for artifact in (finished.result.artifacts if finished.result else [])}
    assert "reel.mp4" not in names


@pytest.mark.slow
def test_visible_fallback_is_recorded_never_silent(tmp_path) -> None:
    finished = _submit_doodle(
        tmp_path,
        {
            "script": [{"narration": "One line.", "keywords": ["One"]}],
            "mode": "program",
            "voice": "elevenlabs:Rachel",
            "allow_visible_fallback": True,
            "name": "fallback",
        },
    )
    assert finished.state is JobState.SUCCEEDED, finished.error
    manifest = json.loads(Path(finished.result.manifest).read_text(encoding="utf-8"))
    assert manifest["verification"]["visible_degradation"], "the substitution must be visible"
    assert "fell back to the fake voice" in " ".join(manifest["verification"]["visible_degradation"])


@pytest.mark.slow
def test_program_mode_renders_and_never_claims_stroke(tmp_path) -> None:
    pytest.importorskip("PIL")
    finished = _submit_doodle(
        tmp_path,
        {
            "script": [
                {"narration": "Compound interest builds slowly.", "keywords": ["Compound"]},
                {"narration": "Then it accelerates.", "keywords": ["Growth"]},
            ],
            "mode": "program",
            "name": "program-reel",
        },
    )
    assert finished.state is JobState.SUCCEEDED, finished.error
    manifest = json.loads(Path(finished.result.manifest).read_text(encoding="utf-8"))
    assert manifest["render_mode"] == "program"
    checks = {check["name"]: check for check in manifest["verification"]["checks"]}
    assert checks["render_mode"]["ok"] is True
    assert "stroke" not in checks["render_mode"]["detail"]
    video = next(a for a in finished.result.artifacts if a.name == "reel.mp4")
    info = ffmpeg.probe(video.path)
    assert info.has_video and info.has_audio


@pytest.mark.slow
def test_stroke_preflight_refuses_blank_line_art(tmp_path) -> None:
    from PIL import Image

    blank = tmp_path / "blank.png"
    Image.new("RGB", (600, 900), "white").save(blank)
    finished = _submit_doodle(
        tmp_path,
        {
            "script": [{"narration": "Nothing to draw here.", "keywords": ["Blank"]}],
            "mode": "stroke",
            "line_art": [str(blank)],
            "name": "blank-art",
        },
    )
    assert finished.state is JobState.FAILED
    assert finished.error is not None and finished.error.code == "PREFLIGHT_FAILED"
    names = {artifact.name for artifact in (finished.result.artifacts if finished.result else [])}
    assert "reel.mp4" not in names


@pytest.mark.slow
def test_stroke_mode_inks_progressively_with_no_browser_and_no_node(tmp_path) -> None:
    """The acceptance test for stroke mode, plus the machine-checkable 'not a pan'."""
    finished = _submit_doodle(
        tmp_path,
        {
            "script": [
                {"narration": "Every idea starts small.", "keywords": ["Idea"]},
                {"narration": "Then it grows.", "keywords": ["Growth"]},
            ],
            "mode": "stroke",
            "scene_structure": "dual_islands",
            "name": "stroke-reel",
        },
    )
    assert finished.state is JobState.SUCCEEDED, finished.error
    manifest = json.loads(Path(finished.result.manifest).read_text(encoding="utf-8"))
    assert manifest["render_mode"] == "stroke"

    # sample frames across the first region's reveal and assert ink accumulates
    prep_artifact = next(a for a in finished.result.artifacts if a.name == "timeline.json")
    timeline = json.loads(Path(prep_artifact.path).read_text(encoding="utf-8"))
    video = next(a for a in finished.result.artifacts if a.name == "reel.mp4")
    counts = _ink_over_time(Path(video.path), start_ms=200, end_ms=900, samples=4)
    assert counts == sorted(counts), f"ink must accumulate, got {counts}"
    assert counts[-1] > counts[0] + 200, f"ink barely changed: {counts}"
    assert timeline["render_mode"] == "stroke"


@pytest.mark.slow
def test_stroke_render_needs_no_browser_and_no_node(tmp_path) -> None:
    """Render a scene in a subprocess whose PATH has no node and whose imports block browsers."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "sitecustomize.py").write_text(
        textwrap.dedent(
            """
            import sys

            BLOCKED = {"playwright", "selenium", "pyppeteer", "websockets", "pyppeteer_stealth"}

            class _Blocker:
                def find_module(self, name, path=None):  # pragma: no cover - legacy hook
                    return None

                def find_spec(self, name, path=None, target=None):
                    root = name.split(".")[0]
                    if root in BLOCKED:
                        raise ImportError(f"{root} must not be needed by the stroke route")
                    return None

            sys.meta_path.insert(0, _Blocker())
            """
        ),
        encoding="utf-8",
    )
    script = sandbox / "render.py"
    script.write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path
            from reelmachine.config import get_settings
            from reelmachine.recipes.doodle.models import Beat
            from reelmachine.recipes.doodle.scenes.raster import sketch
            from reelmachine.recipes.doodle.scenes.spec import stroke_scene
            from reelmachine.recipes.doodle.scenes.stroke import StrokeRenderer

            work = Path({str(sandbox)!r}) / "out"
            art = sketch(work / "art.png", size=(600, 900), keyword="No browser")
            scene = stroke_scene(Beat(id="b1", narration="x", keywords=["No browser"]), 1,
                                 structure="single", words=[], duration_ms=1500, canvas=(600, 900))
            renderer = StrokeRenderer(get_settings())
            assert not renderer.missing(), renderer.missing()
            path = renderer.render(scene, art=art, dest=work / "scene.mp4", fps=24, total_ms=1500)
            assert path.is_file() and path.stat().st_size > 0
            print("OK")
            """
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(sandbox)
    env["PATH"] = os.pathsep.join(
        part for part in env.get("PATH", "").split(os.pathsep) if part and not (Path(part) / "node").exists()
    )
    proc = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, cwd=str(sandbox)
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "OK" in proc.stdout


# ------------------------------------------------------------------- helpers


def _ink_over_time(video: Path, *, start_ms: int, end_ms: int, samples: int) -> list[int]:
    """Ink pixels (below mid-grey) at several timestamps of a rendered reel."""
    settings = get_settings()
    step = (end_ms - start_ms) / max(1, samples - 1)
    counts: list[int] = []
    for index in range(samples):
        at_s = (start_ms + step * index) / 1000.0
        proc = subprocess.run(
            [
                settings.ffmpeg, "-hide_banner", "-nostdin",
                "-ss", f"{at_s:.3f}", "-i", str(video),
                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.returncode == 0, proc.stderr[-300:]
        pixels = np.frombuffer(proc.stdout or b"", dtype=np.uint8)
        counts.append(int((pixels < 160).sum()))
    return counts
