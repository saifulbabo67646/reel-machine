import dataclasses

import pytest

from reelmachine.recipes.techreel.lint import Finding, blocks, check
from reelmachine.recipes.techreel.models import TechReelInputs


def _inputs(beats, **kw):
    return TechReelInputs.model_validate({"title": "t", "beats": beats, **kw})


def _beat(say="hello there", **kw):
    return {"lines": [{"say": say}], **kw}


def _rules(found):
    return {f.rule for f in found}


def test_clean_script_has_no_findings():
    assert check(_inputs([_beat()])) == []


def test_no_beats():
    f = check(_inputs([]))
    assert [(x.rule, x.severity, x.path) for x in f] == [("no-beats", "error", "beats")]
    assert "no-beats" not in _rules(check(_inputs([_beat()])))


def test_empty_say():
    f = check(_inputs([_beat(), _beat("   ")]))
    assert [(x.rule, x.path) for x in f] == [("empty-say", "beats[1].lines[0].say")]


def test_digit_in_say():
    assert "digit-in-say" in _rules(check(_inputs([_beat("GPT-4o is here")])))
    f = check(_inputs([_beat("१७६")]))
    assert f[0].rule == "digit-in-say" and f[0].path == "beats[0].lines[0].say"
    assert "G P T four o" in f[0].hint
    assert check(_inputs([_beat("एक सौ")], language="hi")) == []


def test_markup_in_say():
    f = check(_inputs([_beat("this is *bold*")]))
    assert [(x.rule, x.severity) for x in f] == [("markup-in-say", "error")]
    assert check(_inputs([{"lines": [{"say": "this is bold", "text": "this is *bold*"}]}])) == []


def test_cue_line():
    visuals = [
        {"kind": "title", "text": "x", "at": {"line": 1}},
        {"kind": "title", "text": "y", "exit": {"line": 3}},
        {"kind": "window", "title": "w", "cursor": [{"to": [0.5, 0.5], "at": {"line": 2}}]},
        {"kind": "title", "text": "ok", "at": {"line": 0}, "exit": "beat"},
    ]
    f = [x for x in check(_inputs([_beat(visuals=visuals)])) if x.rule == "cue-line"]
    assert [x.path for x in f] == [
        "beats[0].visuals[0].at.line",
        "beats[0].visuals[1].exit.line",
        "beats[0].visuals[2].cursor[0].at.line",
    ]
    assert all(x.severity == "error" for x in f)


def test_voice_provider():
    i = _inputs([_beat()], voice="elevenlabs:Rachel")
    f = check(i, "fake")
    assert [(x.rule, x.severity, x.path) for x in f] == [("voice-provider", "error", "voice")]
    assert check(i, "elevenlabs") == []
    assert check(i) == []


def test_cue_word_warning():
    miss = {"kind": "title", "text": "x", "at": {"line": 0, "word": "banana"}}
    f = check(_inputs([_beat(visuals=[miss])]))
    assert [(x.rule, x.severity, x.path) for x in f] == [("cue-word", "warning", "beats[0].visuals[0].at.word")]
    hit = {"kind": "title", "text": "x", "at": {"line": 0, "word": "There,"}}
    assert check(_inputs([_beat(visuals=[hit])])) == []


def test_cue_word_uses_caption_text():
    line = {"say": "gee pee tee", "text": "GPT"}
    v = {"kind": "title", "text": "x", "at": {"line": 0, "word": "gpt"}}
    assert check(_inputs([{"lines": [line], "visuals": [v]}])) == []


def test_blocks():
    assert blocks(_inputs([], mode="full", lint="strict"))
    assert not blocks(_inputs([], mode="full", lint="warn"))
    assert not blocks(_inputs([], mode="storyboard", lint="strict"))


def test_finding_is_frozen():
    f = Finding("r", "error", "m", "h", "p")
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.rule = "x"  # type: ignore[misc]


def test_cue_word_warning_on_exit_cue():
    miss = {"kind": "title", "text": "x", "exit": {"line": 0, "word": "banana"}}
    f = check(_inputs([_beat(visuals=[miss])]))
    assert [(x.rule, x.severity, x.path) for x in f] == [("cue-word", "warning", "beats[0].visuals[0].exit.word")]
