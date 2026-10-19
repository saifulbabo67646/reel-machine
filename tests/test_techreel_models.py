import pytest
from pydantic import ValidationError

from reelmachine.recipes.techreel.models import Beat, Cue, Line, TechReelInputs


def _beat(visual):
    return Beat.model_validate({"lines": [{"say": "x"}], "visuals": [visual]})


def test_text_defaults_to_say():
    assert Line(say="hello there").caption == "hello there"
    assert Line(say="hello", text="HELLO *there*").caption == "HELLO *there*"


def test_counter_accepts_from_alias():
    v = _beat({"kind": "counter", "from": 5, "to": 176}).visuals[0]
    assert (v.from_, v.to) == (5, 176)


def test_cue_refuses_word_and_frac():
    with pytest.raises(ValidationError):
        Cue(line=0, word="a", frac=0.5)
    assert Cue(line=0, word="a").frac is None


def test_unknown_visual_kind_refused():
    with pytest.raises(ValidationError):
        _beat({"kind": "hologram"})


def test_terminal_line_needs_exactly_one_of_cmd_out():
    ok = _beat({"kind": "terminal", "lines": [{"cmd": "ls"}, {"out": "a", "tone": "ok"}]})
    assert len(ok.visuals[0].lines) == 2
    for bad in ({}, {"cmd": "ls", "out": "x"}):
        with pytest.raises(ValidationError):
            _beat({"kind": "terminal", "lines": [bad]})


def test_visual_defaults_and_emoji_place():
    v = _beat({"kind": "emoji", "emoji": "x"}).visuals[0]
    assert v.place == "auto" and v.exit == "beat" and v.at is None
    assert _beat({"kind": "title", "text": "t"}).visuals[0].place == "stack"


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        Line(say="x", bogus=1)


def test_inputs_defaults_and_patterns():
    i = TechReelInputs(title="t")
    assert (i.language, i.voice, i.look, i.mode, i.lint, i.sfx) == ("en", "fake:default", "mint", "storyboard", "strict", True)
    for bad in ({"language": "not a lang"}, {"name": "-x"}, {"accent": "red"}, {"look": "neon"}):
        with pytest.raises(ValidationError):
            TechReelInputs(title="t", **bad)
    assert TechReelInputs(title="t", language="pt-BR", accent="#00ffaa", name="a.b").language == "pt-BR"


def test_beat_id_pattern_and_limits():
    with pytest.raises(ValidationError):
        Beat.model_validate({"id": "Bad", "lines": [{"say": "x"}]})
    with pytest.raises(ValidationError):
        Beat.model_validate({"lines": []})


def test_window_cursor_move():
    v = _beat({"kind": "window", "title": "w", "cursor": [{"to": [0.5, 0.2], "at": {"line": 0}}]}).visuals[0]
    assert v.cursor[0].to == (0.5, 0.2)
    with pytest.raises(ValidationError):
        _beat({"kind": "window", "title": "w", "cursor": [{"to": [1.5, 0.2], "at": {"line": 0}}]})
