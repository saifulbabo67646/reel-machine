"""dialoguereel's verify step: the dialogue is never silent for long and every line's bubble is drawn."""

from types import SimpleNamespace

import numpy as np

from reelmachine.config import get_settings
from reelmachine.recipes.dialoguereel import verify
from reelmachine.recipes.dialoguereel.recipe import DialogueReelRecipe
from reelmachine.recipes.dialoguereel.schedule import schedule


def test_long_silences_clips_to_span():
    assert verify.long_silences([(0, 1500), (2000, 4000), (9000, 9600)], (1500, 9200)) == [(2000, 4000)]


def test_parse_silences_open_end():
    err = ("[silencedetect @ 0x1] silence_start: 1.2\n"
           "[silencedetect @ 0x1] silence_end: 3.0 | silence_duration: 1.8\n"
           "[silencedetect @ 0x1] silence_start: 8.5\n")
    assert verify.parse_silences(err, 10_000) == [(1200, 3000), (8500, 10_000)]


def test_dialogue_span_listen_first():
    plan = schedule([1000] * 4, slow=[False, True, False, False], listen_first=True, recap=True, quiz_line=1)
    start, end = verify.dialogue_span(plan)
    assert start == plan.listen[0].start_ms
    assert end == max(w.slow_start_ms + w.slow_ms if w.slow_start_ms is not None else w.end_ms for w in plan.lines)
    assert verify.long_silences([], (start, end)) == []


def test_dialogue_span_without_listen_ends_with_the_slow_replay():
    plan = schedule([1000, 1000], slow=[False, True])
    assert verify.dialogue_span(plan) == (plan.lines[0].start_ms, plan.lines[1].slow_start_ms + plan.lines[1].slow_ms)


def test_text_energy_flat_vs_text():
    flat = np.tile(np.linspace(40, 60, 270, dtype=np.float32), (60, 1))
    text = flat.copy()
    text[20:40, ::6] = 255
    assert verify.text_energy(flat) < verify.TEXT_MIN < verify.text_energy(text)


def test_storyboard_verifies_without_a_video():
    ctx = SimpleNamespace(config=get_settings())
    inputs = SimpleNamespace(mode="cast")
    preview = SimpleNamespace(sheet="cast.png", compose=SimpleNamespace(narration=SimpleNamespace(
        script=SimpleNamespace(inputs=inputs))))
    report = verify.verify_dialoguereel(ctx, {"preview": preview})
    assert report.ok and report.render_mode == "hyperframes" and not report.checks and report.notes == "cast"


def test_the_recipe_verifies():
    ctx = SimpleNamespace(config=get_settings())
    assert DialogueReelRecipe().verify(ctx, {"preview": SimpleNamespace(sheet="")}).ok
