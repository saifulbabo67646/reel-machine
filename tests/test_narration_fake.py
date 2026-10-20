"""The fake voice: exact word timings, and representative lengths for unspaced scripts."""

from itertools import pairwise

from reelmachine.recipes.doodle.narration.fake import FakeNarration
from reelmachine.recipes.explainer3d.timing import estimate_speech_ms


def test_spaced_language_times_one_span_per_word(tmp_path):
    speech = FakeNarration(workdir=tmp_path).synthesize("Hello there, big world.", language="en")
    words = speech.segments[0].words
    assert [w.text for w in words] == ["Hello", "there,", "big", "world."]
    # the spaced timings are what they always were: 260 ms + 18 per letter mod 5, then the pause
    assert (words[0].start_ms, words[0].end_ms) == (0, 260)
    assert words[1].start_ms == 260 + 60
    assert speech.duration_ms == words[-1].end_ms + 320


def test_japanese_line_lasts_about_the_estimate_with_one_span_per_character(tmp_path):
    text = "小さいモデルでも、スマホで動く時代になりました。"       # 24 characters, 22 without punctuation
    speech = FakeNarration(workdir=tmp_path).synthesize(text, language="ja")
    want = estimate_speech_ms(text, "ja")
    assert abs(speech.duration_ms - want) <= 100
    words = speech.segments[0].words
    # punctuation rides on the character before it, as the caption units do
    assert "".join(w.text for w in words) == text and len(words) == 22
    assert words[7].text == "も、" and words[-1].text == "た。"
    assert all(a.end_ms <= b.start_ms for a, b in pairwise(words))
    assert words[0].start_ms == 0 and words[-1].end_ms <= speech.duration_ms


def test_twenty_character_japanese_line(tmp_path):
    text = "あいうえおかきくけこさしすせそたちつてと"
    speech = FakeNarration(workdir=tmp_path).synthesize(text, language="ja-JP")
    assert abs(speech.duration_ms - estimate_speech_ms(text, "ja")) <= 100
    assert len(speech.segments[0].words) == 20


def test_chinese_is_unspaced_too(tmp_path):
    speech = FakeNarration(workdir=tmp_path).synthesize("我们 今天", language="zh")
    assert [w.text for w in speech.segments[0].words] == ["我", "们", "今", "天"]


def test_language_is_normalised_like_the_callers(tmp_path):
    # "ja_JP" and " Ja " are Japanese too: one span per character, not one word for the whole line
    text = "あいうえお"
    voice = FakeNarration(workdir=tmp_path)
    for language in ("ja_JP", " Ja ", "JA-jp"):
        assert len(voice.synthesize(text, language=language).segments[0].words) == 5, language
