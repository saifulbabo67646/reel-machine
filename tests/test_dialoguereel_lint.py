import pytest

from reelmachine.recipes.dialoguereel.lint import check
from reelmachine.recipes.dialoguereel.models import DialogueReelInputs, Line


def line(speaker="kid", text="こんにちは"):
    return Line(speaker=speaker, text=text, meaning="hello")


def script(lines=None, title="T", mode="storyboard"):
    return DialogueReelInputs(cast="x", title=title, mode=mode, lines=lines if lines is not None else [line()] * 3)


SP = {"kid", "mom"}


def test_unknown_speaker_is_an_error_naming_the_line():
    found = check(script(lines=[line("dad")] * 3), {"kid", "mom"})
    assert [(f.rule, f.severity, f.line) for f in found][:1] == [("unknown-speaker", "error", 0)]


@pytest.mark.parametrize("count, ok", [(2, False), (3, True), (12, True), (13, False)])
def test_line_count(count, ok):
    found = check(script(lines=[line()] * count), SP)
    assert ("line-count" not in {f.rule for f in found}) is ok


def test_an_empty_title_is_an_error():
    found = check(script(title=" "), SP)
    assert [(f.rule, f.severity) for f in found] == [("no-title", "error")]


def test_cast_mode_needs_no_lines():
    assert check(script(lines=[], mode="cast"), SP) == []


def test_bad_ruby_is_a_warning():
    found = check(script(lines=[line(text="a{b}")] * 3), SP)
    assert found and all(f.rule == "ruby" and f.severity == "warning" for f in found)
    assert found[0].line == 0


def test_every_finding_has_a_hint():
    for f in check(script(lines=[line("dad")], title=""), SP):
        assert f.hint
