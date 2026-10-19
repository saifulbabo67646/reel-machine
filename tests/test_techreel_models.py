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


def test_window_rows():
    assert _beat({"kind": "window", "title": "w"}).visuals[0].rows == []
    v = _beat({"kind": "window", "title": "w", "rows": ["> namaste", "hi! main offline hoon"]}).visuals[0]
    assert v.rows == ["> namaste", "hi! main offline hoon"]
    with pytest.raises(ValidationError):
        _beat({"kind": "window", "title": "w", "rows": ["x" * 49]})
    with pytest.raises(ValidationError):
        _beat({"kind": "window", "title": "w", "rows": ["a", "b", "c", "d", "e"]})


def test_window_cursor_move():
    v = _beat({"kind": "window", "title": "w", "cursor": [{"to": [0.5, 0.2], "at": {"line": 0}}]}).visuals[0]
    assert v.cursor[0].to == (0.5, 0.2)
    with pytest.raises(ValidationError):
        _beat({"kind": "window", "title": "w", "cursor": [{"to": [1.5, 0.2], "at": {"line": 0}}]})


# ---------------------------------------------------------------- plan 2 catalogue

_MINIMAL = {
    "equation": {"kind": "equation", "rows": ["a"], "result": "b"},
    "chat": {"kind": "chat", "messages": [{"from": "me", "text": "hi"}]},
    "dialog": {"kind": "dialog", "title": "Allow?"},
    "error": {"kind": "error", "title": "Oops", "body": "bad"},
    "receipts": {"kind": "receipts", "items": [{"title": "a"}]},
    "checklist": {"kind": "checklist", "items": ["a"]},
    "people": {"kind": "people", "total": 10, "lit": 3},
    "versus": {"kind": "versus", "left": {"label": "a", "value": "1"}, "right": {"label": "b", "value": "2"}},
    "nodes": {"kind": "nodes", "items": [{"label": "a"}, {"label": "b"}]},
    "bars": {"kind": "bars", "items": [{"label": "a", "value": 1}, {"label": "b", "value": 2}]},
    "flash": {"kind": "flash", "text": "boom"},
    "heartbeat": {"kind": "heartbeat"},
    "comment": {"kind": "comment", "keyword": "GUIDE"},
    "follow": {"kind": "follow"},
    "image": {"kind": "image", "src": "https://x.test/a.png"},
    "svg": {"kind": "svg", "svg": "<svg/>"},
}
_BAD = {
    "equation": {"rows": []}, "chat": {"messages": []}, "dialog": {"title": "x" * 41},
    "error": {"button": "x" * 13}, "receipts": {"items": []}, "checklist": {"items": ["a"] * 7},
    "people": {"total": 0}, "versus": {"left": {"label": "x" * 21, "value": "1"}},
    "nodes": {"items": [{"label": "a"}]}, "bars": {"items": [{"label": "a", "value": -1}, {"label": "b", "value": 1}]},
    "flash": {"text": "x" * 41}, "heartbeat": {"label": "x" * 41}, "comment": {"keyword": "x" * 25},
    "follow": {"extra": 1}, "image": {"src": ""}, "svg": {"svg": "x" * 60001},
}


@pytest.mark.parametrize("kind", sorted(_MINIMAL))
def test_new_visual_validates_and_refuses(kind):
    assert _beat(_MINIMAL[kind]).visuals[0].kind == kind
    with pytest.raises(ValidationError):
        _beat({**_MINIMAL[kind], **_BAD[kind]})


def test_chat_message_accepts_from_and_sender():
    msgs = _beat(_MINIMAL["chat"]).visuals[0].messages
    assert msgs[0].sender == "me" and msgs[0].bot is False
    from reelmachine.recipes.techreel.models import ChatMessage
    assert ChatMessage(sender="me", text="x").sender == "me"


def test_people_lit_cannot_exceed_total():
    with pytest.raises(ValidationError):
        _beat({"kind": "people", "total": 10, "lit": 11})


def test_dialog_click_and_image_highlight():
    d = _beat({"kind": "dialog", "title": "t", "click": "approve", "click_at": {"line": 0}}).visuals[0]
    assert d.click == "approve" and d.click_at.line == 0
    with pytest.raises(ValidationError):
        _beat({"kind": "image", "src": "x", "highlight": [0, 0, 1.2, 1]})
    assert _beat({"kind": "image", "src": "x", "highlight": [0, 0.1, 0.5, 1]}).visuals[0].highlight


def test_hero_spec_svg_and_src():
    from reelmachine.recipes.techreel.models import HeroSpec
    for kind in ("chip", "phone", "robot"):
        HeroSpec(kind=kind)
    with pytest.raises(ValidationError):
        HeroSpec(kind="svg")
    with pytest.raises(ValidationError):
        HeroSpec(kind="image")
    with pytest.raises(ValidationError):
        HeroSpec(kind="orb", svg="<svg/>")
    with pytest.raises(ValidationError):
        HeroSpec(kind="orb", src="a.png")
    with pytest.raises(ValidationError):
        HeroSpec(kind="svg", svg="<svg/>", src="a.png")
    assert HeroSpec(kind="svg", svg="<svg/>").svg == "<svg/>"
    assert HeroSpec(kind="image", src="a.png").src == "a.png"


def test_hero_states_locked_split():
    v = _beat({"kind": "hero", "state": "split", "count": 4, "labels": ["a", "b"]}).visuals[0]
    assert (v.count, v.labels) == (4, ["a", "b"])
    assert _beat({"kind": "hero", "state": "locked"}).visuals[0].count == 3
    with pytest.raises(ValidationError):
        _beat({"kind": "hero", "state": "split", "count": 1})
    with pytest.raises(ValidationError):
        _beat({"kind": "hero", "state": "split", "labels": ["x" * 17]})


def test_dialog_click_at_needs_click():
    with pytest.raises(ValidationError, match="click_at needs click"):
        _beat({"kind": "dialog", "title": "t", "click_at": {"line": 0}})
    assert _beat({"kind": "dialog", "title": "t", "click": "decline"}).visuals[0].click_at is None


def test_bars_highlight_within_items():
    items = [{"label": "a", "value": 1}, {"label": "b", "value": 2}]
    assert _beat({"kind": "bars", "items": items, "highlight": 1}).visuals[0].highlight == 1
    for bad in (2, -1):
        with pytest.raises(ValidationError):
            _beat({"kind": "bars", "items": items, "highlight": bad})
