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


# the whole-reel rules; the early tests look at one rule at a time and leave these out
_REEL = {"hook-slow", "beat-long", "static", "structure", "crowded", "length", "no-sources", "cta-no-creator"}


def _core(found):
    return [f for f in found if f.rule not in _REEL]


def test_clean_script_has_no_findings():
    assert _core(check(_inputs([_beat()]))) == []


def test_no_beats():
    f = _core(check(_inputs([])))
    assert [(x.rule, x.severity, x.path) for x in f] == [("no-beats", "error", "beats")]
    assert "no-beats" not in _rules(check(_inputs([_beat()])))


def test_empty_say():
    f = _core(check(_inputs([_beat(), _beat("   ")])))
    assert [(x.rule, x.path) for x in f] == [("empty-say", "beats[1].lines[0].say")]


def test_digit_in_say():
    assert "digit-in-say" in _rules(check(_inputs([_beat("GPT-4o is here")])))
    f = _core(check(_inputs([_beat("१७६")])))
    assert f[0].rule == "digit-in-say" and f[0].path == "beats[0].lines[0].say"
    assert "G P T four o" in f[0].hint
    assert _core(check(_inputs([_beat("एक सौ")], language="hi"))) == []


def test_markup_in_say():
    f = _core(check(_inputs([_beat("this is *bold*")])))
    assert [(x.rule, x.severity) for x in f] == [("markup-in-say", "error")]
    assert _core(check(_inputs([{"lines": [{"say": "this is bold", "text": "this is *bold*"}]}]))) == []


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
    f = _core(check(i, "fake"))
    assert [(x.rule, x.severity, x.path) for x in f] == [("voice-provider", "error", "voice")]
    assert _core(check(i, "elevenlabs")) == []
    assert _core(check(i)) == []


def test_cue_word_warning():
    miss = {"kind": "title", "text": "x", "at": {"line": 0, "word": "banana"}}
    f = _core(check(_inputs([_beat(visuals=[miss])])))
    assert [(x.rule, x.severity, x.path) for x in f] == [("cue-word", "warning", "beats[0].visuals[0].at.word")]
    hit = {"kind": "title", "text": "x", "at": {"line": 0, "word": "There,"}}
    assert _core(check(_inputs([_beat(visuals=[hit])]))) == []


def test_cue_word_uses_caption_text():
    line = {"say": "gee pee tee", "text": "GPT model"}
    v = {"kind": "title", "text": "x", "at": {"line": 0, "word": "gpt"}}
    assert _core(check(_inputs([{"lines": [line], "visuals": [v]}]))) == []


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
    f = _core(check(_inputs([_beat(visuals=[miss])])))
    assert [(x.rule, x.severity, x.path) for x in f] == [("cue-word", "warning", "beats[0].visuals[0].exit.word")]


def test_click_at_cue_is_checked():
    dialog = {"kind": "dialog", "title": "t", "click": "approve", "click_at": {"line": 3}}
    assert "cue-line" in _rules(check(_inputs([_beat(visuals=[dialog])])))
    dialog["click_at"] = {"line": 0, "word": "zzz"}
    assert "cue-word" in _rules(check(_inputs([_beat(visuals=[dialog])])))


# ---------------------------------------------------------------- the full lint


def _example(**kw):
    from copy import deepcopy

    from reelmachine.recipes.techreel.recipe import EXAMPLE

    return TechReelInputs.model_validate({**deepcopy(EXAMPLE), **kw})


def _words(n):
    return " ".join(["word"] * n)


def _at(found, rule):
    return [(f.severity, f.path) for f in found if f.rule == rule]


def test_the_example_is_lint_clean():
    assert check(_example()) == []


def test_svg_error():
    bad = {"kind": "svg", "svg": "<svg viewBox='0 0 10 10'><script/></svg>"}
    f = check(_inputs([_beat(visuals=[bad])]))
    assert _at(f, "svg") == [("error", "beats[0].visuals[0].svg")]
    good = {"kind": "svg", "svg": "<svg viewBox='0 0 10 10'><circle r='4'/></svg>"}
    assert "svg" not in _rules(check(_inputs([_beat(visuals=[good])])))


def test_image_local():
    local = {"kind": "image", "src": "/tmp/a.png"}
    f = check(_inputs([_beat(visuals=[local])]), allow_local=False)
    assert _at(f, "image-local") == [("error", "beats[0].visuals[0].src")]
    assert "image-local" not in _rules(check(_inputs([_beat(visuals=[local])])))
    url = {"kind": "image", "src": "https://example.com/a.png"}
    assert "image-local" not in _rules(check(_inputs([_beat(visuals=[url])]), allow_local=False))


@pytest.mark.parametrize("src", ["ftp://e.com/a.png", "file:///tmp/a.png", "data:image/png;base64,AAAA"])
def test_image_scheme(src):
    for allow_local in (True, False):
        f = check(_inputs([_beat(visuals=[{"kind": "image", "src": src}])]), allow_local=allow_local)
        assert _at(f, "image-scheme") == [("error", "beats[0].visuals[0].src")]
        assert "image-local" not in _rules(f)
    for ok in ("https://e.com/a.png", "a.png"):
        assert "image-scheme" not in _rules(check(_inputs([_beat(visuals=[{"kind": "image", "src": ok}])])))


def test_hook_slow():
    title = {"kind": "title", "text": "x"}
    slow = _inputs([{"lines": [{"say": _words(12)}], "visuals": [title]}])
    assert _at(check(slow), "hook-slow") == [("warning", "beats[0]")]
    bare = _inputs([_beat(_words(4))])
    assert _at(check(bare), "hook-slow") == [("warning", "beats[0]")]
    fast = _inputs([{"lines": [{"say": _words(6)}], "visuals": [title]}])
    assert "hook-slow" not in _rules(check(fast))


def test_beat_long():
    title = {"kind": "title", "text": "x"}
    five = {"lines": [{"say": _words(3)}] * 5, "visuals": [title]}
    assert _at(check(_inputs([five])), "beat-long") == [("warning", "beats[0]")]
    seconds = {"lines": [{"say": _words(30)}], "visuals": [title]}
    assert "beat-long" in _rules(check(_inputs([seconds])))
    four = {"lines": [{"say": _words(3)}] * 4, "visuals": [title]}
    assert "beat-long" not in _rules(check(_inputs([four])))


def test_static():
    title = {"kind": "title", "text": "x"}
    late = {"kind": "title", "text": "y", "at": {"line": 1}}
    lines = [{"say": _words(8)}, {"say": _words(8)}]
    gap = _inputs([{"lines": lines, "visuals": [title]}])
    assert _at(check(gap), "static") == [("warning", "beats[0]")]
    ok = _inputs([{"lines": lines, "visuals": [title, late]}])
    assert "static" not in _rules(check(ok))


def test_structure():
    f = check(_inputs([_beat(kind="pain")]))
    assert _at(f, "structure") == [("warning", "beats[0].kind"), ("warning", "beats[0].kind"),
                                   ("warning", "beats")]
    full = check(_example())
    assert "structure" not in _rules(full)
    no_honest = _example()
    no_honest.beats[:] = [b for b in no_honest.beats if b.kind != "honest"]
    assert _at(check(no_honest), "structure") == [("warning", "beats")]


def test_cta_no_creator():
    ex = _example(creator=None)
    assert _at(check(ex), "cta-no-creator") == [("warning", "creator")]
    assert "cta-no-creator" not in _rules(check(_example()))


def test_crowded():
    chips = [{"kind": "emoji", "emoji": "x"}] * 5
    lines = [{"say": _words(4)}]
    crowded = _inputs([{"lines": lines, "visuals": chips}])
    assert _at(check(crowded), "crowded") == [("warning", "beats[0].visuals")]
    heroes = [{"kind": "hero", "state": "pulse"}] * 3
    fine = _inputs([{"lines": lines, "visuals": chips[:4] + heroes}])
    assert "crowded" not in _rules(check(fine))
    leaving = chips[:4] + [{"kind": "emoji", "emoji": "x", "at": {"line": 1}}]
    two = _inputs([{"lines": lines * 2, "visuals": [{**v, "exit": {"line": 1}} for v in leaving[:4]] + leaving[4:]}])
    assert "crowded" not in _rules(check(two))


def test_length():
    title = {"kind": "title", "text": "x"}
    short = check(_inputs([{"lines": [{"say": _words(4)}], "visuals": [title]}]))
    assert _at(short, "length") == [("warning", "beats")]
    assert "length" not in _rules(check(_example()))
    huge = _inputs([{"lines": [{"say": _words(8)}] * 8, "visuals": [title]}] * 10)
    assert "length" in _rules(check(huge))


def test_text_mismatch():
    off = {"lines": [{"say": "this is what is said aloud", "text": "ok"}]}
    assert _at(check(_inputs([off])), "text-mismatch") == [("warning", "beats[0].lines[0].text")]
    ok = {"lines": [{"say": "this is what is said aloud", "text": "this is *what* is said"}]}
    assert "text-mismatch" not in _rules(check(_inputs([ok])))
    assert "text-mismatch" not in _rules(check(_inputs([_beat()])))
    padded = {"lines": [{"say": "  hi there " + " " * 40, "text": "hi there"}]}
    assert "text-mismatch" not in _rules(check(_inputs([padded])))   # what is said, not its padding


def test_hindi_latin():
    f = check(_inputs([_beat("this is latin")], language="hi"))
    assert _at(f, "hindi-latin") == [("warning", "beats[0].lines[0].say")]
    assert "hindi-latin" not in _rules(check(_inputs([_beat("एक सौ")], language="hi")))
    assert "hindi-latin" not in _rules(check(_inputs([_beat("this is latin")])))


def test_no_sources():
    assert _at(check(_inputs([_beat()])), "no-sources") == [("warning", "sources")]
    assert "no-sources" not in _rules(check(_inputs([_beat()], sources=["https://example.com"])))


def test_active_tab():
    w = {"kind": "window", "title": "w", "tabs": ["a", "b"], "active": 2}
    f = check(_inputs([_beat(visuals=[w])]))
    assert _at(f, "active-tab") == [("warning", "beats[0].visuals[0].active")]
    assert "active-tab" not in _rules(check(_inputs([_beat(visuals=[{**w, "active": 1}])])))
    assert "active-tab" not in _rules(check(_inputs([_beat(visuals=[{"kind": "window", "title": "w"}])])))
