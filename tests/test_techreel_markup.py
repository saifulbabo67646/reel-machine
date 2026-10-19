from reelmachine.recipes.techreel.markup import (
    Unit,
    chunks,
    is_unspaced,
    norm,
    plain,
    units,
)


def test_units_accent_and_split():
    assert units("Aur laptop *zinda* hai.", "hi") == [Unit("Aur", False), Unit("laptop", False), Unit("zinda", True), Unit("hai.", False)]


def test_units_multiword_accent():
    assert units("*176 billion* params", "en")[:2] == [Unit("176", True), Unit("billion", True)]
    assert units("*176 billion* params", "en")[2].accent is False


def test_units_unclosed_star_is_literal():
    assert units("5 * 3", "en")[1].text == "*"
    assert [u.accent for u in units("a *b c", "en")] == [False, False, False]


def test_units_japanese_by_character():
    assert [u.text for u in units("百二十五億。", "ja")] == ["百", "二", "十", "五", "億。"]
    assert [(u.text, u.accent) for u in units("百*二十*五", "ja")] == [("百", False), ("二", True), ("十", True), ("五", False)]


def test_is_unspaced_and_plain():
    assert is_unspaced("ja-JP") and is_unspaced("zh") and not is_unspaced("en")
    assert plain("a *b* c") == "a b c"


def test_norm():
    assert norm("*Zinda*,") == "zinda"
    assert norm("हैं।") == "हैं"


def test_chunks_six_and_punctuation():
    us = units("one two, three four five six seven eight", "en")
    assert chunks(us, "en") == [[0, 1], [2, 3, 4, 5, 6, 7]]


def test_chunks_single_unit_punct_does_not_break():
    us = units("Hi, there friend", "en")
    assert chunks(us, "en") == [[0, 1, 2]]


def test_chunks_japanese_fourteen():
    us = units("あ" * 30 + "。" + "い" * 5, "ja")
    cs = chunks(us, "ja")
    assert all(sum(len(us[i].text) for i in c) <= 14 for c in cs)
    assert [i for c in cs for i in c] == list(range(len(us)))


def test_chunks_japanese_breaks_after_period():
    us = units("あいう。えお", "ja")
    assert chunks(us, "ja") == [[0, 1, 2], [3, 4]]


def test_units_partial_word_accent_stays_one_unit():
    us = units("What is *R*etrieval *A*ugmented?", "en")
    assert [u.text for u in us] == ["What", "is", "Retrieval", "Augmented?"]
    assert us[2] == Unit("Retrieval", False, ((0, 1),))
    assert us[3] == Unit("Augmented?", False, ((0, 1),))


def test_units_partial_accent_mid_word_and_several_runs():
    assert units("re*mark*able", "en") == [Unit("remarkable", False, ((2, 6),))]
    assert units("*a*b*c*", "en") == [Unit("abc", False, ((0, 1), (2, 3)))]


def test_units_whole_word_accent_ignores_punctuation_outside_the_stars():
    # "*ChatGPT*," and "(*RAG*)" are still accent words: the punctuation is not part of the word
    assert units("*ChatGPT*, (*RAG*)", "en") == [Unit("ChatGPT,", True), Unit("(RAG)", True)]
