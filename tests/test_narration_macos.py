import shutil

import pytest

from reelmachine.core.registry import Registry
from reelmachine.recipes.doodle.narration.macos import MacOSNarration, clauses, spread, syllables


def test_clauses_end_at_punctuation():
    assert clauses("Five countries, one map. Ready?  Go") == [["Five", "countries,"], ["one", "map."], ["Ready?"], ["Go"]]


def test_syllables_are_rough_but_never_zero():
    assert syllables("Gucci") == 2
    assert syllables("make") == 1
    assert syllables("IKEA!") == 2
    assert syllables("1990") == 4
    assert syllables("—") == 1


def test_spread_fills_the_clause_in_order():
    spans = spread(["Italy", "has", "Gucci,"], 1000, 2000)
    assert spans[0].start_ms == 1000 and spans[-1].end_ms == 2000
    assert all(a.end_ms <= b.start_ms + 1 for a, b in zip(spans, spans[1:]))
    assert spans[0].end_ms - spans[0].start_ms > spans[1].end_ms - spans[1].start_ms  # Italy outlasts has


def test_registered_as_a_narration_provider():
    assert Registry().load("narration", "macos") is MacOSNarration


@pytest.mark.slow
@pytest.mark.skipif(not shutil.which("say"), reason="macOS only")
def test_real_voice_times_every_word(tmp_path):
    speech = MacOSNarration(workdir=tmp_path).synthesize("Five countries make almost everything, number five will surprise you.",
                                                         voice="macos:default")
    words = speech.segments[0].words
    assert [w.text for w in words][0] == "Five" and len(words) == 10
    assert 1500 < speech.duration_ms < 8000
    assert words[-1].end_ms <= speech.duration_ms
    assert all(w.start_ms < w.end_ms for w in words)
    assert speech.path.is_file()
