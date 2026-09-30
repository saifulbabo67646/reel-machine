"""Which clips actually say the word being taught.

Nadeshiko searches by lexeme: `やっぱり` also returns `やはり` and `やっぱ`.  For a
dictionary that is right; for a reel titled with one word the audio has to say
that word.  These cases came from live data.
"""

from __future__ import annotations

import pytest

from reelmachine.matching import accepted_forms, describe, is_word_match, matched_forms
from reelmachine.models import Segment, TextJa, Token, Translation


def seg(content: str, highlight: str | None = None, tokens: list | None = None) -> Segment:
    return Segment(
        publicId="V1StGXR8_Z5d",
        position=0,
        status="ACTIVE",
        startTimeMs=0,
        endTimeMs=1000,
        contentRating="SAFE",
        episode=1,
        mediaPublicId="m",
        textJa=TextJa(content=content, highlight=highlight, tokens=tokens),
        textEn=Translation(content="x"),
        textEs=Translation(content="x"),
        urls={"imageUrl": "i", "audioUrl": "a", "videoUrl": "v"},
    )


# Real live payloads.
YAPPARI = seg("やっぱり... やっぱり!", "<em>やっぱり</em>... <em>やっぱり</em>!")
YAHARI = seg("ハッ... やはりバッテリーか　えっと 充電方法",
             "ハッ... <em>やはり</em>バッテリーか　えっと 充電方法",
             [Token(s="やはり", d="やはり", r="ヤハリ", b=5, e=8, p="副詞")])
YAPPA = seg("やっぱ お前 いいヤツだな", "<em>やっぱ</em> お前 いいヤツだな",
            [Token(s="やっぱ", d="やっぱり", r="ヤッパ", b=0, e=3, p="副詞")])


def test_the_real_yahari_clip_is_rejected() -> None:
    """The bug this exists for: audio saying やはり in a reel about やっぱり."""
    assert matched_forms(YAHARI) == ["やはり"]
    assert accepted_forms(YAHARI, "やっぱり") == []
    assert not is_word_match(YAHARI, "やっぱり", "strict")


def test_literal_matches_are_kept() -> None:
    assert accepted_forms(YAPPARI, "やっぱり") == ["やっぱり", "やっぱり"]
    assert is_word_match(YAPPARI, "やっぱり", "strict")


def test_contraction_is_kept() -> None:
    """やっぱ is やっぱり said faster -- still the word being taught."""
    assert accepted_forms(YAPPA, "やっぱり") == ["やっぱ"]
    assert is_word_match(YAPPA, "やっぱり", "strict")


def test_inflected_form_is_kept_through_its_dictionary_form() -> None:
    """A search for 食べる must accept a clip that says 食べた."""
    eaten = seg("食べた", "<em>食べた</em>",
                [Token(s="食べ", d="食べる", r="タベ", b=0, e=2, p="動詞")])
    assert is_word_match(eaten, "食べる", "strict")


def test_unrelated_word_is_rejected() -> None:
    other = seg("猫が好き", "<em>猫</em>が好き")
    assert not is_word_match(other, "やっぱり", "strict")


def test_api_mode_accepts_everything() -> None:
    assert is_word_match(YAHARI, "やっぱり", "api")
    assert is_word_match(seg("なんでも", None), "やっぱり", "api")


def test_unmarked_segment_falls_back_to_the_content() -> None:
    with_text = seg("やっぱり そうだ")
    assert is_word_match(with_text, "やっぱり", "strict")
    without = seg("ぜんぜん ちがう")
    assert not is_word_match(without, "やっぱり", "strict")


def test_describe_labels_a_variant() -> None:
    assert "variant" in describe(YAHARI, "やっぱり")
    assert "variant" not in describe(YAPPARI, "やっぱり")


@pytest.mark.parametrize("word,content,form,keep", [
    ("やっぱり", "やっぱり!", "やっぱり", True),
    ("やっぱり", "やっぱ!", "やっぱ", True),
    ("やっぱり", "やはり...", "やはり", False),
    ("猫", "猫がいる", "猫", True),
    ("猫", "猫じゃない", "猫", True),
])
def test_matrix(word: str, content: str, form: str, keep: bool) -> None:
    s = seg(content, f"<em>{form}</em>")
    assert is_word_match(s, word, "strict") is keep
