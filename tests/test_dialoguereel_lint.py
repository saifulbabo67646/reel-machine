import pytest

from reelmachine.recipes.dialoguereel.recipe import EXAMPLE
from reelmachine.recipes.dialoguereel.lint import check
from reelmachine.recipes.dialoguereel.models import DialogueReelInputs, Line


def line(speaker="kid", text="こんにちは", reading="konnichiwa", keys=()):
    return Line(speaker=speaker, text=text, reading=reading, meaning="hello", keys=list(keys))


def rules(found):
    return [f.rule for f in found]


def script(lines=None, title="T", mode="storyboard", **extra):
    return DialogueReelInputs(cast="x", title=title, mode=mode, lines=lines if lines is not None else [line()] * 3,
                              **extra)


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
    assert [(f.rule, f.severity) for f in found if f.severity == "error"] == [("no-title", "error")]


def test_cast_mode_needs_no_lines():
    assert check(script(lines=[], mode="cast"), SP) == []


def test_bad_ruby_is_a_warning():
    found = [f for f in check(script(lines=[line(text="a{b}")] * 3), SP) if f.rule == "ruby"]
    assert found and all(f.rule == "ruby" and f.severity == "warning" for f in found)
    assert found[0].line == 0


def test_every_finding_has_a_hint():
    for f in check(script(lines=[line("dad")], title=""), SP):
        assert f.hint


def test_no_title_is_skipped_in_cast_mode():
    assert check(script(lines=[], title="", mode="cast"), SP) == []


def test_no_reading_on_japanese_but_not_spanish():
    assert "no-reading" in rules(check(script(lines=[line(reading=None)] * 3), SP))
    spanish = [line(text="Hola amigo", reading=None)] * 3
    assert "no-reading" not in rules(check(script(lines=spanish, language="es"), SP))


def test_long_line_limits_by_script():
    def long(text):
        return "long-line" in rules(check(script(lines=[line(text=text)] * 3), SP))
    assert long("あ" * 23) and not long("あ" * 22)
    assert long("a" * 43) and not long("a" * 42)


def test_length_window():
    assert "length" in rules(check(script(lines=[line(text="はい")] * 3), SP))
    ok = [line(text="あ" * 20)] * 7
    assert "length" not in rules(check(script(lines=ok), SP))


def test_key_missing_names_its_path():
    from reelmachine.recipes.dialoguereel.models import KeyWord
    from reelmachine.recipes.dialoguereel.stages import lint_findings
    lines = [line(), line(keys=[KeyWord(text="さかな", meaning="fish")]), line()]
    inputs = script(lines=lines)
    miss = [f for f in lint_findings(inputs, SP) if f["rule"] == "key-missing"]
    assert [f["path"] for f in miss] == ["lines[1].keys[0]"]
    assert miss[0]["hint"]


def test_level_flags_a_harder_word():
    from reelmachine.recipes.dialoguereel.models import KeyWord
    from reelmachine.recipes.dialoguereel.stages import lint_findings

    def levels(word, level="N5"):
        lines = [line(), line(text=f"これは{word}です", keys=[KeyWord(text=word, meaning="m")]), line()]
        return [f for f in lint_findings(script(lines=lines, level=level), SP) if f["rule"] == "level"]
    hard = levels("一応")
    assert [f["path"] for f in hard] == ["lines[1].keys[0]"]
    assert not levels("一応", "N2") and not levels("ぬえぬえ")
    assert not levels("一応", "")


def test_key_count():
    from reelmachine.recipes.dialoguereel.models import KeyWord

    def count(n):
        keys = [KeyWord(text="こんにちは", meaning="hi")] * n
        lines = [line(keys=keys[i * 3:(i + 1) * 3]) for i in range(3)]
        return "key-count" in rules(check(script(lines=lines), SP))
    assert count(1) and not count(2) and not count(6) and count(7)


def test_same_speaker_run():
    def run(n):
        lines = [line("kid")] * n + [line("mom")]
        return "same-speaker" in rules(check(script(lines=lines), SP))
    assert run(4) and not run(3)


def test_quiz_line_out_of_range_is_an_error():
    from reelmachine.recipes.dialoguereel.stages import lint_findings
    bad = [f for f in lint_findings(script(quiz=3), SP) if f["rule"] == "quiz-line"]
    assert [(f["severity"], f["path"]) for f in bad] == [("error", "quiz")] and bad[0]["hint"]
    assert "quiz-line" not in rules(check(script(quiz=0), SP))


def test_reference_dialogue_regression():
    inputs = DialogueReelInputs(cast="x", title="", language="ja", lines=EXAMPLE["lines"])
    found = check(inputs, SP)
    assert [(f.rule, f.severity) for f in found if f.severity == "error"] == [("no-title", "error")]
    assert "key-count" in rules(found)
