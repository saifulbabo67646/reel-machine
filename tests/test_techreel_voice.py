import pytest

from reelmachine.core.timeline import WordSpan
from reelmachine.recipes.techreel.models import Line
from reelmachine.recipes.techreel.voice import drop_tags, spoken


def test_a_line_without_an_expression_is_said_as_written():
    assert spoken(Line(say="hello there"), "supertonic") == "hello there"


@pytest.mark.parametrize(("provider", "at", "want"), [
    ("supertonic", "end", "nice one <laugh>"),
    ("supertonic", "start", "<laugh> nice one"),
    ("cartesia", "end", "nice one [laughter]"),
    ("fake", "end", "nice one"),
    ("macos", "start", "nice one"),
])
def test_a_laugh_becomes_the_providers_tag(provider, at, want):
    assert spoken(Line(say="nice one", expression="laugh", expression_at=at), provider) == want


def test_elevenlabs_laughs_only_on_v3(monkeypatch):
    line = Line(say="nice one", expression="laugh")
    monkeypatch.delenv("REEL_ELEVENLABS_MODEL", raising=False)
    assert spoken(line, "elevenlabs") == "nice one"
    monkeypatch.setenv("REEL_ELEVENLABS_MODEL", "eleven_v3")
    assert spoken(line, "elevenlabs") == "nice one [laughs]"


def test_tags_timed_as_words_are_dropped():
    words = [WordSpan(text="<laugh>", start_ms=0, end_ms=400), WordSpan(text="मिलो", start_ms=400, end_ms=600),
             WordSpan(text="[laughter]", start_ms=600, end_ms=900), WordSpan(text="से।", start_ms=900, end_ms=1000)]
    assert [w.text for w in drop_tags(words)] == ["मिलो", "से।"]


def test_an_unknown_expression_is_refused():
    with pytest.raises(ValueError):
        Line(say="x", expression="sigh")
