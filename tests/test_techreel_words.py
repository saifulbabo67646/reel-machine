from reelmachine.core.timeline import WordSpan
from reelmachine.recipes.techreel.markup import units
from reelmachine.recipes.techreel.words import unit_times

S = [WordSpan(text="भाई,", start_ms=500, end_ms=1000), WordSpan(text="मैंने", start_ms=1000, end_ms=1400)]


def test_equal_counts_copy():
    assert unit_times(S, units("Bhai, maine", "hi"), 1600) == [(500, 1000), (1000, 1400)]


def test_unequal_counts_monotonic_in_range():
    t = unit_times(S, units("Bhai, maine apne", "hi"), 1600)
    assert len(t) == 3 and all(0 <= a <= b <= 1600 for a, b in t)
    assert [a for a, _ in t] == sorted(a for a, _ in t)
    assert t[0][0] == 500
    # spoken chars 4+5=9 span 500..1400; caption chars 5/5/4 of 14 -> unit 2 at x=9*5/14=3.21 -> 500+500*3.21/4 = 902
    assert [a for a, _ in t] == [500, 902, 1194]
    assert t[2][1] == 1400


def test_no_spans_spread_by_characters():
    assert unit_times([], units("ab abcd", "en"), 600) == [(0, 200), (200, 600)]


def test_japanese_characters_from_char_spans():
    spans = [WordSpan(text=c, start_ms=i * 100, end_ms=(i + 1) * 100) for i, c in enumerate("こんにちは")]
    assert unit_times(spans, units("こんにちは", "ja"), 600) == [(i * 100, (i + 1) * 100) for i in range(5)]


def test_empty_units():
    assert unit_times(S, [], 1000) == []


def test_equal_counts_starts_non_decreasing():
    spans = [WordSpan(text="a", start_ms=800, end_ms=900), WordSpan(text="b", start_ms=300, end_ms=400)]
    t = unit_times(spans, units("a b", "en"), 1000)
    assert t[0][0] <= t[1][0] and all(a <= b for a, b in t)


def test_span_past_speech_is_clamped():
    spans = [WordSpan(text="a", start_ms=0, end_ms=500), WordSpan(text="b", start_ms=500, end_ms=1500)]
    assert unit_times(spans, units("a b", "en"), 1000) == [(0, 500), (500, 1000)]
    assert unit_times(spans, units("a b c", "en"), 1000)[-1][1] == 1000
