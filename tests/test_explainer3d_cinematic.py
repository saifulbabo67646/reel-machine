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
