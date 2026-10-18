import pytest

from reelmachine.recipes.dialoguereel.ruby import Run, parse_ruby, plain_text


def test_a_reading_attaches_to_the_kanji_run_before_it():
    runs, problems = parse_ruby("手伝{てつだ}いましょうか。")
    assert runs == [Run("手伝", "てつだ"), Run("いましょうか。", None)] and problems == []


def test_two_readings_in_one_line():
    runs, _ = parse_ruby("今日{きょう}は雨{あめ}です")
    assert [r.ruby for r in runs] == ["きょう", None, "あめ", None]


def test_text_without_braces_is_one_plain_run():
    assert parse_ruby("¿Puedo ayudar?") == ([Run("¿Puedo ayudar?", None)], [])


@pytest.mark.parametrize("text", ["{よみ}です", "漢字{", "漢字{}です", "かな{かな}"])
def test_malformed_braces_stay_literal_with_a_problem(text):
    runs, problems = parse_ruby(text)
    assert "".join(r.text for r in runs) == text and problems


def test_plain_text_drops_readings():
    assert plain_text(parse_ruby("手伝{てつだ}う")[0]) == "手伝う"


def test_empty_text_has_no_runs():
    assert parse_ruby("") == ([], [])
