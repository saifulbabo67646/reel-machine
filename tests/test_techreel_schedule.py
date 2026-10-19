import pytest

from reelmachine.recipes.techreel.markup import units
from reelmachine.recipes.techreel.models import Beat, Cue, Line, TechReelInputs
from reelmachine.recipes.techreel.schedule import (
    BeatSlot,
    LineSlot,
    cue_ms,
    estimate,
    exit_ms,
    schedule,
)


def _beats():
    return [
        Beat(lines=[Line(say="a b"), Line(say="c")]),
        Beat(lines=[Line(say="d")], transition="slide"),
    ]


def test_schedule():
    p = schedule(_beats(), [[1000, 800], [500]])
    assert (p.beats[0].start_ms, p.beats[0].end_ms) == (0, 1920)
    assert p.beats[0].lines == (LineSlot(0, 1000, 1000), LineSlot(1120, 800, 1920))
    assert (p.beats[1].start_ms, p.beats[1].end_ms) == (2170, 2670)
    assert p.duration_ms == 3870


SLOT = BeatSlot(1000, 3000, (LineSlot(1000, 1000, 2000), LineSlot(2120, 800, 2920)))
US = [units("mein *176 billion* mein 176", "en"), units("x", "en")]
TIMES = [[(0, 100), (100, 400), (400, 700), (700, 800), (800, 1000)], [(0, 800)]]


def test_cue_none_and_range():
    assert cue_ms(None, SLOT, TIMES, US) == (1000, True)
    assert cue_ms(Cue(line=5), SLOT, TIMES, US) == (1000, False)


def test_cue_word():
    assert cue_ms(Cue(line=0, word="*176*,"), SLOT, TIMES, US) == (1100, True)
    assert cue_ms(Cue(line=0, word="mein"), SLOT, TIMES, US) == (1000, True)    # first match wins
    assert cue_ms(Cue(line=0, word="nope"), SLOT, TIMES, US) == (1000, False)
    assert cue_ms(Cue(line=1, word="nope"), SLOT, TIMES, US) == (2120, False)


def test_cue_multiword_matches_first_token():
    assert cue_ms(Cue(line=0, word="billion mein"), SLOT, TIMES, US) == (1400, True)


def test_cue_frac():
    assert cue_ms(Cue(line=0, frac=0.5), SLOT, TIMES, US) == (1500, True)


def test_exit():
    assert exit_ms("beat", SLOT, 4000, TIMES, US) == 4000
    assert exit_ms("beat", SLOT, None, TIMES, US) == 3000 + 1200
    assert exit_ms(Cue(line=0, frac=1), SLOT, 4000, TIMES, US) == 2000


def test_estimate():
    inp = TechReelInputs(title="t", beats=_beats())
    p = estimate(inp)
    assert p.duration_ms > 0
    assert all(ln.speech_ms >= 400 for b in p.beats for ln in b.lines)


def test_schedule_refuses_mismatched_speech_lists():
    with pytest.raises(ValueError):
        schedule(_beats(), [[100, 100]])
    with pytest.raises(ValueError):
        schedule(_beats(), [[100, 100], [100], [100]])


def test_estimate_floors_one_character_say_at_400ms():
    plan = estimate(TechReelInputs(title="t", beats=[Beat(lines=[Line(say="a")])]))
    assert plan.beats[0].lines[0].speech_ms == 400


def test_word_index():
    from reelmachine.recipes.techreel.schedule import word_index

    us = units("Hello, *big* world", "en")
    assert word_index(Cue(line=0, word="BIG now"), us) == 1
    assert word_index(Cue(line=0, word="nope"), us) is None
    assert word_index(Cue(line=0, frac=0.5), us) is None


def test_word_index_matches_a_run_of_japanese_characters():
    from reelmachine.recipes.techreel.schedule import word_index

    us = units("小さいパラメータ、すごい。", "ja")
    assert word_index(Cue(line=0, word="パラメータ"), us) == 3
    assert word_index(Cue(line=0, word="すごい"), us) == 8
    assert word_index(Cue(line=0, word="パラメ"), us) == 3     # a run need not be a whole word
    assert word_index(Cue(line=0, word="メモリ"), us) is None


def test_word_index_prefers_a_single_unit_and_runs_skip_punctuation():
    from reelmachine.recipes.techreel.schedule import word_index

    us = units("AI、AIだ", "ja")      # units: A, I、, A, I, だ
    assert word_index(Cue(line=0, word="AI"), us) == 0
    assert word_index(Cue(line=0, word="だ"), us) == 4


def test_cue_word_on_a_japanese_run_lands_on_its_first_character():
    us = [units("小さいパラメータ", "ja")]
    times = [[(i * 100, i * 100 + 100) for i in range(len(us[0]))]]
    beat = BeatSlot(1000, 1800, (LineSlot(1000, 800, 1800),))
    assert cue_ms(Cue(line=0, word="パラメータ"), beat, times, us) == (1300, True)
