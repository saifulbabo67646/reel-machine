"""Alignment maths, subtitle formatting and HLS parsing — no network needed."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import fftconvolve

from reelmachine.align import (
    HOP_MS,
    MIN_MARGIN,
    Anchor,
    locate,
    sliding_ncc,
    solve_timeline,
)
from reelmachine.sources.hls import Variant, parse_master, pick_variant
from reelmachine.subtitles import (
    Cue,
    Layout,
    ass_time,
    build_ass,
    furigana_runs,
    highlight,
    line_romaji,
    srt_time,
    wrap_ranges,
)


# ------------------------------------------------------------------ correlation


def test_sliding_ncc_matches_numpy_correlate():
    rng = np.random.default_rng(1)
    x = rng.standard_normal(4096)
    y = rng.standard_normal(256)
    expected = np.correlate(x - x.mean(), y - y.mean(), "valid")
    energy = np.sqrt(
        np.convolve((x - x.mean()) ** 2, np.ones(len(y)), "valid") * np.sum((y - y.mean()) ** 2)
    )
    np.testing.assert_allclose(sliding_ncc(x, y), expected / energy, atol=1e-9)
    # and the FFT identity the implementation relies on
    np.testing.assert_allclose(
        fftconvolve(x - x.mean(), (y - y.mean())[::-1], "valid"), expected, atol=1e-8
    )


@pytest.mark.parametrize("offset_ms", [0, 2500, 61_230, 143_990])
def test_locate_recovers_known_offset(offset_ms):
    rng = np.random.default_rng(7)
    clip = rng.standard_normal(180)  # 900 ms at a 5 ms hop
    episode = rng.standard_normal(40_000) * 0.4  # 200 s
    start = int(round(offset_ms / HOP_MS))
    episode[start : start + clip.size] += clip * 3.0  # a loud, distinctive line

    match = locate(episode, clip)
    assert abs(match.offset_ms - offset_ms) <= 20
    assert match.score > 0.8


def test_locate_is_robust_to_gain_and_noise():
    """Different encodes mean different levels; correlation must not care."""
    rng = np.random.default_rng(11)
    clip = rng.standard_normal(240)
    episode = rng.standard_normal(30_000) * 0.2
    start = int(9_000 / HOP_MS)  # envelopes are indexed in 10 ms hops
    episode[start : start + clip.size] += clip * 0.35  # quiet in the mix
    noisy_clip = clip * 12.0 + rng.standard_normal(clip.size) * 0.6

    match = locate(episode, noisy_clip)
    assert abs(match.offset_ms - 9_000) <= 30
    assert match.score > 0.6


def test_locate_respects_search_window():
    rng = np.random.default_rng(3)
    clip = rng.standard_normal(150)
    episode = rng.standard_normal(40_000) * 0.3
    # The same line appears twice (a repeated catchphrase).
    for at_ms in (5_000, 25_000):
        at = int(at_ms / HOP_MS)
        episode[at : at + clip.size] += clip

    near = locate(episode, clip, search_start_ms=24_800, margin_ms=1_000)
    assert abs(near.offset_ms - 25_000) <= 20

    far = locate(episode, clip, search_start_ms=5_000, margin_ms=1_000)
    assert abs(far.offset_ms - 5_000) <= 20


# --------------------------------------------------------------------- timeline


def _anchor(segment_id: str, src: int, found: int, score: float = 0.9) -> Anchor:
    return Anchor(segment_id=segment_id, src_ms=src, found_ms=found, score=score, clip_ms=1800)


def test_constant_offset_from_single_anchor():
    timeline = solve_timeline([_anchor("a", 10_000, 12_500)])
    assert timeline.ok
    assert timeline.offset_ms == 2_500
    assert timeline.a == 1.0
    assert timeline.to_local_ms(60_000) == 62_500


def test_affine_fit_recovers_drift():
    """A 0.1% rate difference (frame-rate mismatch) must show up as a slope."""
    src = [60_000, 300_000, 600_000, 900_000, 1_200_000]
    found = [int(round(2_000 + 1.001 * s)) for s in src]
    timeline = solve_timeline([_anchor(f"s{i}", s, f) for i, (s, f) in enumerate(zip(src, found))])
    assert timeline.ok
    assert timeline.a == pytest.approx(1.001, abs=1e-4)
    assert abs(timeline.max_residual_ms) <= 60


def test_outlier_anchor_is_ignored():
    anchors = [
        _anchor("a", 100_000, 102_000),
        _anchor("b", 400_000, 402_000),
        _anchor("c", 800_000, 802_000),
        _anchor("bogus", 1_200_000, 1_900_000),  # matched the wrong line
    ]
    timeline = solve_timeline(anchors)
    assert timeline.ok
    assert timeline.offset_ms == 2_000
    assert "bogus" not in {a.segment_id for a in timeline.anchors}


def test_low_scores_are_rejected():
    timeline = solve_timeline([_anchor("a", 1_000, 5_000, score=0.05)])
    assert not timeline.ok
    assert "below threshold" in timeline.reason


def test_ambiguous_match_is_not_trustworthy():
    """Two equally good positions must not be treated as a measurement."""
    rng = np.random.default_rng(5)
    clip = rng.standard_normal(200)
    episode = rng.standard_normal(20_000) * 0.3
    # The identical line twice: no amount of correlation can tell them apart.
    for at_ms in (10_000, 60_000):
        at = int(at_ms / HOP_MS)
        episode[at : at + clip.size] += clip

    match = locate(episode, clip)
    assert match.score > 0.5          # a strong peak...
    assert match.ambiguous            # ...but there are two of them
    assert not match.trustworthy


def test_unambiguous_match_is_trustworthy():
    rng = np.random.default_rng(6)
    clip = rng.standard_normal(200)
    episode = rng.standard_normal(20_000) * 0.3
    at = int(10_000 / HOP_MS)
    episode[at : at + clip.size] += clip

    match = locate(episode, clip)
    assert match.margin > MIN_MARGIN
    assert match.trustworthy
    assert not match.ambiguous


def test_window_restores_trust_in_a_repeated_line():
    """A prediction window is what disambiguates a repeated catchphrase."""
    rng = np.random.default_rng(8)
    clip = rng.standard_normal(200)
    episode = rng.standard_normal(20_000) * 0.3
    for at_ms in (10_000, 60_000):
        at = int(at_ms / HOP_MS)
        episode[at : at + clip.size] += clip

    # A window wide enough to contain a rival peak if there were one nearby.
    windowed = locate(episode, clip, search_start_ms=60_000, margin_ms=3_000)
    assert abs(windowed.offset_ms - 60_000) <= 20
    assert windowed.judged and windowed.trustworthy


def test_ignores_implausible_slope():
    """A false anchor must not be allowed to produce a wild rate."""
    anchors = [
        _anchor("a", 10_000, 12_000),
        _anchor("b", 20_000, 4_000),   # nonsense
        _anchor("c", 30_000, 32_000),
    ]
    timeline = solve_timeline(anchors)
    assert abs(timeline.a - 1.0) < 0.05
    assert timeline.offset_ms == 2_000


def test_anchors_bunched_together_give_constant_offset():
    anchors = [_anchor("a", 100_000, 102_500), _anchor("b", 100_400, 102_900)]
    timeline = solve_timeline(anchors)
    assert timeline.a == 1.0
    assert timeline.offset_ms == 2_500


def test_no_anchors_is_not_ok():
    timeline = solve_timeline([])
    assert not timeline.ok
    assert "no reference audio" in timeline.reason


# -------------------------------------------------------------------- subtitles


@pytest.mark.parametrize(
    "ms,expected",
    [(0, "0:00:00.00"), (1_234, "0:00:01.23"), (61_000, "0:01:01.00"), (3_661_500, "1:01:01.50")],
)
def test_ass_time(ms, expected):
    assert ass_time(ms) == expected


@pytest.mark.parametrize(
    "ms,expected",
    [(0, "00:00:00,000"), (1_234, "00:00:01,234"), (3_661_500, "01:01:01,500")],
)
def test_srt_time(ms, expected):
    assert srt_time(ms) == expected


def test_highlight_wraps_the_word():
    out = highlight("彼女は俺の幼馴染だ。", "彼女")
    assert out.startswith("{\\c&H0040D6FF\\b1}彼女")
    assert "幼馴染" in out


def test_highlight_uses_token_offsets_for_inflected_forms():
    class Token:
        s = "食べ"
        b = 0
        e = 2

    out = highlight("食べました。", "食べる", tokens=[Token()])
    assert out.startswith("{\\c&H0040D6FF\\b1}食べ")


def test_highlight_uses_the_apis_em_tags():
    """Live responses use <em>; the spec's <mark> never appears."""
    marked = "ハッ... <em>やはり</em>バッテリーか　えっと 充電方法"
    out = highlight("ハッ... やはりバッテリーか　えっと 充電方法", "やっぱり", marked=marked)
    # The variant form is highlighted even though "やっぱり" is nowhere in the line.
    assert out.startswith("ハッ... {\\c&H0040D6FF\\b1}やはり{\\c&H00FFFFFF\\b0}")
    assert "<em>" not in out and "</em>" not in out


def test_mark_tag_is_still_handled():
    marked = "でも <mark>やっぱり</mark>..."
    out = highlight("でも やっぱり...", "やっぱり", marked=marked)
    assert "\\c&H0040D6FF" in out
    assert "<mark>" not in out


def test_strip_marks_removes_both_flavours():
    from reelmachine.subtitles import strip_marks

    assert strip_marks("<em>a</em><mark>b</mark>c") == "abc"


def test_highlight_escapes_braces():
    out = highlight("a{b}c", "zzz")
    assert "\\{" in out and "\\}" in out


def test_build_ass_has_both_languages_and_valid_header():
    cues = [
        Cue(start_ms=0, end_ms=2_000, japanese="彼女は俺の幼馴染だ。", english="She's my childhood friend.", word="彼女", source="Bakuman · ep 1"),
    ]
    text = build_ass(cues, layout=Layout.for_aspect("vertical"), watermark="@chan")
    assert "[Script Info]" in text and "[V4+ Styles]" in text and "[Events]" in text
    assert "Style: JA," in text and "Style: EN," in text and "Style: WM," in text
    assert "Dialogue: 0,0:00:00.00,0:00:02.00,JA" in text
    assert "She's my childhood friend." in text
    assert "@chan" in text


def test_build_ass_vertical_layout_keeps_subs_above_the_bottom_band():
    vertical = Layout.for_aspect("vertical")
    square = Layout.for_aspect("square")
    # Video band, then the caption stack, then the bottom of the frame.
    assert vertical.video_y + vertical.video_h < vertical.en_y < vertical.ja_y < vertical.play_y
    assert square.video_y + square.video_h < square.en_y < square.ja_y < square.play_y
    assert vertical.play_y == 1920 and square.play_y == 1080
    # Vertical has a card band; the 16:9 letterbox has no room for one.
    assert vertical.card_h > 0
    assert Layout.for_aspect("original").card_h == 0


def test_build_ass_card_carries_word_reading_meaning_and_kanji():
    """The card is the lesson: romaji, meaning, kana and the kanji form."""
    from reelmachine.vocab import VocabEntry

    vocab = VocabEntry(word="団長", reading="だんちょう", level=3, meaning="leader of a group")
    cues = [
        Cue(start_ms=0, end_ms=2_000, japanese="団長はどこだ", english="Where is the commander?",
            word="団長", reading="danchou wa doko da", vocab=vocab),
    ]
    text = build_ass(cues, layout=Layout.for_aspect("vertical"))
    assert "Style: CardWord," in text and "Style: CardKanji," in text
    assert "Danchou" in text          # romaji heading
    assert "leader of a group" in text
    assert "だんちょう" in text        # kana
    assert "団長" in text             # kanji
    assert "N3" in text


def test_card_names_both_kana_forms_and_boxes_only_kanji():
    """やっぱり/やはり are one word with two spellings; a kana word has no box."""
    from reelmachine.vocab import VocabEntry, describe

    entry = describe("やっぱり")
    kana_cues = [
        Cue(start_ms=0, end_ms=2_000, japanese="やっぱり!", english="I knew it!",
            word="やっぱり", vocab=entry),
    ]
    kana_text = build_ass(kana_cues, layout=Layout.for_aspect("vertical"))
    assert "Yahari/Yappari" in kana_text  # both romanisations
    assert "やはり/やっぱり" in kana_text  # both spellings
    assert "N4" in kana_text
    assert "\\p1" in kana_text            # the card background panel is drawn
    assert "\\bord3" not in kana_text, "a kana word must not be framed"
    assert "Style: CardKanji," in kana_text, "the taught word is still shown large"

    kanji_entry = VocabEntry(word="団長", reading="だんちょう", level=3, meaning="leader")
    kanji_cues = [
        Cue(start_ms=0, end_ms=2_000, japanese="団長", english="the commander",
            word="団長", vocab=kanji_entry),
    ]
    kanji_text = build_ass(kanji_cues, layout=Layout.for_aspect("vertical"))
    assert "\\bord3" in kanji_text, "a kanji headword keeps its box"


def test_furigana_is_placed_over_the_kanji_it_reads():
    """Furigana must sit above its own characters, not the whole line."""
    from reelmachine.models import Token

    line = "確かに昔 そんな約束をしたのかもしれないけど"
    tokens = [
        Token(s="確か", r="タシカ", b=0, e=2),
        Token(s="に", r="ニ", b=2, e=3),
        Token(s="昔", r="ムカシ", b=3, e=4),
        Token(s=" ", r="キゴウ", b=4, e=5),
        Token(s="そんな", r="ソンナ", b=5, e=8),
        Token(s="約束", r="ヤクソク", b=8, e=10),
    ]
    runs = furigana_runs(line, tokens)
    # Kanji runs only, in hiragana, with the kana-only `に`/`そんな` skipped.
    assert [(s, e, r) for s, e, r in runs] == [(0, 2, "たしか"), (3, 4, "むかし"), (8, 10, "やくそく")]

    # Each row's furigana stays inside that row: a token straddling the wrap
    # point is dropped rather than annotated over the wrong characters.
    ranges = wrap_ranges(line, font_size=56, max_width=940)
    assert len(ranges) == 2
    for low, high in ranges:
        for start, end, _ in furigana_runs(line, tokens, limit=(low, high)):
            assert low <= start and end <= high
    first = [(s, e) for s, e, _ in furigana_runs(line, tokens, limit=ranges[0])]
    assert first == [(0, 2), (3, 4), (8, 10)]
    # ...and a token split across the boundary appears in neither row.
    straddling = [Token(s=line[16:19], r="テスト", b=16, e=19)]
    assert furigana_runs(line, straddling, limit=ranges[0]) == []
    assert furigana_runs(line, straddling, limit=ranges[1]) == []


def test_line_romaji_is_built_from_token_readings():
    """Kanji cannot be romanised from their surface form, so readings are used."""
    from reelmachine.models import Token

    tokens = [
        Token(s="確か", r="タシカ", b=0, e=2),
        Token(s="に", r="ニ", b=2, e=3),
        Token(s=" ", r="キゴウ", b=3, e=4),
        Token(s="約束", r="ヤクソク", b=4, e=6),
    ]
    assert line_romaji("確かに 約束", tokens) == "tashika ni yakusoku"


def test_metrics_advance_uses_per_character_widths():
    from reelmachine.subtitles import Metrics, text_width_em

    m = Metrics(cjk=0.8, latin=0.5, space=0.25, ideographic_space=0.8)
    assert m.advance("確") == 0.8
    assert m.advance("あ") == 0.8
    assert m.advance("x") == 0.5
    assert m.advance(" ") == 0.25
    assert m.advance("\u3000") == 0.8          # ideographic space is not " "
    # Widths thread through the wrappers.
    assert text_width_em("確確", m) == pytest.approx(1.6)
    assert text_width_em("確 確", m) == pytest.approx(1.85)


def test_measured_font_metrics_are_size_independent():
    """The advance ratio is a property of the font, so it must not vary with size.

    This is the bug the calibration exists to catch: a first attempt measured
    the same font at 0.80 em at one size and 0.40 at another, because a 1px
    seam at the frame edge was being counted as ink.
    """
    from reelmachine.subtitles import DEFAULT_METRICS, measure_metrics

    small = measure_metrics("Hiragino Sans", 46)
    large = measure_metrics("Hiragino Sans", 80)
    if small == DEFAULT_METRICS and large == DEFAULT_METRICS:
        pytest.skip("font/ffmpeg unavailable for calibration")
    assert small.cjk == pytest.approx(large.cjk, abs=0.02)
    # A CJK glyph is conventionally one em, but no real font here renders it
    # that wide — which is exactly why this is measured rather than assumed.
    assert 0.4 < small.cjk < 1.2
    assert small.cjk != pytest.approx(1.0, abs=0.001)


def test_measured_metrics_change_the_furigana_positions():
    """Different fonts must produce different reading positions."""
    from reelmachine.models import Token
    from reelmachine.subtitles import DEFAULT_METRICS, make_positions, measure_metrics

    text = "確かに昔"
    tokens = [Token(s="確か", r="タシカ", b=0, e=2), Token(s="昔", r="ムカシ", b=3, e=4)]
    got = {}
    for font in ("Hiragino Sans", "YuGothic"):
        m = measure_metrics(font, 56)
        if m == DEFAULT_METRICS:
            pytest.skip("font/ffmpeg unavailable for calibration")
        got[font] = [x for x, _reading in make_positions(text, tokens, font_size=56, metrics=m)]
    assert got["Hiragino Sans"] != got["YuGothic"]


# ------------------------------------------------------------------------- hls


MASTER = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
360/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
720/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080
1080/index.m3u8
"""


def test_parse_master_and_pick_variant():
    variants, media = parse_master(MASTER, "https://cdn.example/hls/show/master.m3u8")
    assert not media
    assert [v.height for v in variants] == [360, 720, 1080]
    assert variants[0].uri == "https://cdn.example/hls/show/360/index.m3u8"

    chosen = pick_variant(variants, "1080")
    assert chosen is not None and chosen.height == 1080
    # ask for something between rungs -> the highest one that fits
    assert pick_variant(variants, "900").height == 720
    # ask for more than exists -> the biggest available
    assert pick_variant(variants, "2160").height == 1080
    # unparseable quality -> highest bandwidth
    assert pick_variant(variants, "best").height == 1080


def test_parse_media_playlist_is_not_treated_as_master():
    text = "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10.0,\nseg1.ts\n"
    variants, media = parse_master(text, "https://cdn.example/hls/show/720/index.m3u8")
    assert variants == []


def test_variant_str():
    assert str(Variant(uri="x", height=1080, bandwidth=6_000_000)).startswith("1080p")
