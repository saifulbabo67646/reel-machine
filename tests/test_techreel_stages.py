"""techreel stages without a render: sound-effect cues, terminal typing starts, the script stage's lint gate, the
silence trim on each spoken line."""

import dataclasses
import shutil

import pytest

from reelmachine import ffmpeg
from reelmachine.config import get_settings
from reelmachine.core.assets import AssetStore
from reelmachine.core.errors import InvalidInput
from reelmachine.core.job import Job, JobRequest
from reelmachine.core.stage import StageContext, StaticProviders
from reelmachine.core.timeline import WordSpan
from reelmachine.recipes.doodle.narration.fake import FakeNarration
from reelmachine.recipes.techreel.recipe import _lint_counts
from reelmachine.recipes.techreel.stages import (
    COUNTER_MS,
    MAX_SFX,
    STILL_AFTER_MS,
    ScriptStage,
    sfx_cues,
    shift_words,
    storyboard_times,
    terminal_starts,
    trim_clip,
)


def _visual(kind, enter, cls="block", **kw):
    return {"kind": kind, "id": f"{kind}{enter}", "enterMs": enter, "exitMs": enter + 1000, "class": cls, **kw}


def _doc(beats, sfx=True):
    return {"beats": [{"startMs": s, "endMs": s + 900, "visuals": vs} for s, vs in beats], "sfx": sfx}


DOC = _doc([
    (0, [_visual("counter", 300, to=9), _visual("stamp", 700, cls="overlay")]),
    (1200, [_visual("terminal", 1500, lines=[{"cmd": "ls"}, {"out": "a"}, {"cmd": "cat a"}]),
            _visual("emoji", 1600, cls="overlay")]),
    (2400, [_visual("stamp", 2500, cls="overlay"), _visual("title", 2600)]),
])


def _named(cues, name):
    return [at for at, n in cues if n == name]


def test_sfx_cues_follow_the_document():
    cues = sfx_cues(DOC)
    assert _named(cues, "whoosh") == [1200, 2400]                 # every beat start but the first
    assert _named(cues, "thud") == [700, 2500]                    # one per stamp
    assert _named(cues, "ding") == [300 + 900]                    # the counter finishing
    assert _named(cues, "pop") == [300, 1500, 2600]               # block visuals only; overlays have their own
    assert _named(cues, "tick") == terminal_starts(DOC["beats"][1]["visuals"][0])[::2]
    assert [at for at, _ in cues] == sorted(at for at, _ in cues)


def test_the_storyboard_waits_for_a_counter_to_land():
    doc = _doc([(0, [_visual("counter", 300, to=9), _visual("title", 200)]),
                (3000, [_visual("title", 3100), _visual("counter", 3200, to=4), _visual("chips", 3300)])])
    times = storyboard_times(doc, 10_000)
    assert 300 + COUNTER_MS in times                                # the counter on its final value
    assert max(3200 + COUNTER_MS, 3300 + STILL_AFTER_MS) in times   # whichever lands last


def test_the_probe_counts_lint_errors_and_warnings_apart():
    def found(*severities):
        return [{"severity": s} for s in severities]

    assert _lint_counts(found("error", "error", "warning", "warning", "warning")) == \
        "2 errors (not blocking), 3 warnings"
    assert _lint_counts(found("error", "warning")) == "1 error (not blocking), 1 warning"
    assert _lint_counts(found()) == "0 warnings"


def test_no_sfx_when_switched_off():
    assert sfx_cues({**DOC, "sfx": False}) == []


def test_a_crowded_reel_drops_pops_first():
    beats = [(b * 10_000, [_visual("title", b * 10_000 + 100 + k * 75) for k in range(24)]) for b in range(5)]
    cues = sfx_cues(_doc(beats))   # 120 pops and 4 whooshes
    assert MAX_SFX == 80 and len(cues) == 80
    assert _named(cues, "whoosh") == [10_000, 20_000, 30_000, 40_000]
    kept = _named(cues, "pop")
    assert all(b * 10_000 + 100 in kept or b * 10_000 + 175 in kept for b in range(5))   # thinned, not cut off


def test_dropped_pops_are_those_closest_to_another_cue():
    # 81 cues: the one pop sitting on top of a whoosh goes, the lonely ones stay
    pops = [_visual("title", 100_000 + k * 1000) for k in range(79)]
    doc = _doc([(0, pops), (5_000_000, [_visual("title", 5_000_010)])])
    cues = sfx_cues(doc)
    assert len(cues) == 80
    assert (5_000_010, "pop") not in cues and (5_000_000, "whoosh") in cues


def test_terminal_starts_mirror_the_runtime():
    # the same visual as runtime/tests/pure.test.mjs: 1000 + 2 chars at 28 cps, + 250 ms, + 5 chars
    v = {"enterMs": 1000, "lines": [{"cmd": "ls"}, {"out": "a b"}, {"out": "c"}, {"cmd": "cat a"}, {"out": "hi"}]}
    assert terminal_starts(v) == [1000, 1071, 1071, 1321, 1500]
    assert terminal_starts({"enterMs": 400, "lines": [{"out": "booting"}, {"cmd": "x"}]}) == [400, 400]
    assert terminal_starts({"enterMs": 0, "lines": [{"cmd": "😀😀"}, {"out": "y"}]}) == [0, 71]   # code points


def _ctx(tmp_path):
    settings = dataclasses.replace(get_settings(), workdir=tmp_path / "w", shared_cache=tmp_path / "shared")
    return StageContext(job=Job(id="t", recipe="techreel", request=JobRequest(recipe="techreel")),
                        workdir=tmp_path, assets=AssetStore(tmp_path / "assets"), config=settings,
                        providers=StaticProviders({"narration": FakeNarration(settings)}))


SCRIPT = {"title": "t", "beats": [{"lines": [{"say": "GPT-4o is here", "text": "GPT-4o is here"}]}]}


def test_a_strict_full_build_stops_on_a_digit_in_say(tmp_path):
    with pytest.raises(InvalidInput) as exc:
        ScriptStage().run(_ctx(tmp_path), SCRIPT | {"mode": "full"})
    assert exc.value.details == {"path": "beats[0].lines[0].say", "rule": "digit-in-say"}
    assert exc.value.stage == "script"


def test_a_storyboard_only_warns(tmp_path):
    out = ScriptStage().run(_ctx(tmp_path), SCRIPT)
    assert [f["rule"] for f in out.lint] == ["digit-in-say"]
    assert out.lint[0]["path"] == "beats[0].lines[0].say" and out.lint[0]["severity"] == "error"
    assert out.warnings and out.warnings[0].startswith("digit-in-say: ")
    full_warn = ScriptStage().run(_ctx(tmp_path), SCRIPT | {"mode": "full", "lint": "warn"})
    assert [f["rule"] for f in full_warn.lint] == ["digit-in-say"]


def _clip(path, *parts):
    """A WAV of ("quiet" | "tone", ms) parts in order."""
    settings = get_settings()
    inputs, labels = [], []
    for n, (kind, ms) in enumerate(parts):
        src = "anullsrc=r=48000:cl=mono" if kind == "quiet" else "sine=frequency=440:sample_rate=48000"
        inputs += ["-f", "lavfi", "-t", f"{ms / 1000:.3f}", "-i", src]
        labels.append(f"[{n}:a]")
    ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", *inputs, "-filter_complex",
                f"{''.join(labels)}concat=n={len(parts)}:v=0:a=1", str(path)])
    return path


@pytest.mark.slow
@pytest.mark.skipif(shutil.which(get_settings().ffmpeg) is None, reason="ffmpeg not available")
def test_trim_clip_cuts_the_lead_and_tail_silence(tmp_path):
    src = _clip(tmp_path / "in.wav", ("quiet", 500), ("tone", 800), ("quiet", 700))
    out, lead, ms = trim_clip(src, tmp_path / "out.wav", settings=get_settings())
    assert abs(lead - 440) <= 25
    assert abs(ms - 920) <= 30
    assert abs(ffmpeg.probe(out).duration_s * 1000 - ms) <= 25
    assert abs(ffmpeg.probe(src).duration_s * 1000 - 2000) <= 25      # the source is left alone


@pytest.mark.slow
@pytest.mark.skipif(shutil.which(get_settings().ffmpeg) is None, reason="ffmpeg not available")
def test_trim_clip_keeps_a_clip_with_no_silence(tmp_path):
    src = _clip(tmp_path / "in.wav", ("tone", 600))
    out, lead, ms = trim_clip(src, tmp_path / "out.wav", settings=get_settings())
    assert lead == 0 and abs(ms - 600) <= 25 and out.is_file()


def test_word_spans_follow_the_trim():
    words = [WordSpan(text="a", start_ms=400, end_ms=700), WordSpan(text="b", start_ms=900, end_ms=1500)]
    assert [(w.start_ms, w.end_ms) for w in shift_words(words, 440, 920)] == [(0, 260), (460, 920)]
