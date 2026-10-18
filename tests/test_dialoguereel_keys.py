from reelmachine.recipes.dialoguereel.keys import KeySpan, match_keys
from reelmachine.recipes.dialoguereel.levels import jlpt_level, n_level
from reelmachine.recipes.dialoguereel.models import KeyWord


def kw(text):
    return KeyWord(text=text, meaning="m")


def test_a_dictionary_form_matches_its_conjugation():
    assert match_keys("手伝{てつだ}いましょうか。", [kw("手伝う")]) == [KeySpan(0, 0, 2)]


def test_ruby_is_stripped_before_matching():
    assert match_keys("明日{あした}は雨{あめ}です。", [kw("雨")]) == [KeySpan(0, 3, 4)]


def test_exact_text_beats_stem():
    assert match_keys("食べる。食べ", [kw("食べる")]) == [KeySpan(0, 0, 3)]


def test_latin_prefix_match():
    assert match_keys("¿Puedo ayudarte?", [kw("ayudar")])[0] == KeySpan(0, 7, 11)


def test_latin_is_case_insensitive_and_word_anchored():
    assert match_keys("Cat concat", [kw("CAT")]) == [KeySpan(0, 0, 3)]


def test_a_repeated_word_and_overlapping_keys():
    spans = match_keys("ねこ と ねこ", [kw("ねこ"), kw("ねこ")])
    assert spans == [KeySpan(0, 0, 2), KeySpan(1, 5, 7)]
    assert match_keys("abc abc", [kw("abc"), kw("abc"), kw("abc")])[2] is None


def test_no_match_is_none():
    assert match_keys("こんにちは", [kw("犬")]) == [None]


def test_jlpt_levels():
    assert jlpt_level("あさって") == 5 and jlpt_level("zzzz") is None
    assert jlpt_level("zzzz", "あさって") == 5
    assert n_level("N4") == 4 and n_level("n3") == 3 and n_level("TOPIK 2") is None


def test_a_key_with_ruby_matches_by_its_plain_text():
    assert match_keys("手伝{てつだ}う？", [kw("手伝{てつだ}う")]) == [KeySpan(0, 0, 3)]
    assert match_keys("手伝{てつだ}いましょうか。", [kw("手伝{てつだ}う")]) == [KeySpan(0, 0, 2)]
