"""The wordreel matcher and its captions — the parts that decide what a reel teaches.

No ffmpeg and no filesystem here: these are the decisions that make an English
learning reel correct or subtly wrong, and they are all pure functions.
"""

from __future__ import annotations

import pytest

from reelmachine.core.timeline import Caption
from reelmachine.render.captions import EMPHASIS_KEY, CaptionStyle, build_ass
from reelmachine.recipes.wordreel import matching


# ------------------------------------------------------------------ the matcher


def surfaces(matcher: matching.WordMatcher, text: str) -> list[str]:
    return [hit.surface for hit in matcher.match(text)]


def test_a_plain_word_matches_itself_and_nothing_else() -> None:
    matcher = matching.WordMatcher("light")
    assert surfaces(matcher, "She turned the light off.") == ["light"]


def test_a_word_does_not_match_inside_a_longer_word() -> None:
    """`light` must not fire on *lighthouse* — that is a different word to teach."""
    matcher = matching.WordMatcher("light")
    assert matcher.match("The lighthouse was dark.") == []


def test_inflected_forms_of_a_regular_word_match() -> None:
    matcher = matching.WordMatcher("help")
    for line in ("She helps me.", "He helped me.", "They are helping me."):
        assert matcher.match(line), line


def test_irregular_forms_match_through_the_curated_table() -> None:
    matcher = matching.WordMatcher("run")
    assert surfaces(matcher, "She was running away.") == ["running"]
    assert surfaces(matcher, "so he ran.") == ["ran"]


def test_an_irregular_word_does_not_expand_into_an_agent_noun() -> None:
    """`run` must not match *runner*: an agent noun is a different word."""
    matcher = matching.WordMatcher("run")
    assert matcher.match("He hired a runner.") == []


def test_a_regular_word_does_not_expand_into_an_agent_noun_either() -> None:
    matcher = matching.WordMatcher("teach")
    assert surfaces(matcher, "He teaches well.") == ["teaches"]
    assert matcher.match("She is a teacher.") == []


def test_light_finds_lit_through_the_table() -> None:
    matcher = matching.WordMatcher("light")
    assert surfaces(matcher, "He lit the fire.") == ["lit"]


def test_a_phrase_matches_a_run_of_adjacent_tokens() -> None:
    matcher = matching.WordMatcher("give up")
    assert surfaces(matcher, "I give up.") == ["give up"]


def test_a_phrase_matches_when_its_words_are_inflected() -> None:
    """The whole point of expanding per token: `gave up` is `give up`."""
    matcher = matching.WordMatcher("give up")
    assert surfaces(matcher, "She gave up on me.") == ["gave up"]
    assert surfaces(matcher, "He is giving up hope.") == ["giving up"]


def test_a_phrase_does_not_match_when_its_words_are_separated() -> None:
    matcher = matching.WordMatcher("give up")
    assert matcher.match("They gave it up.") == []


def test_matching_is_case_insensitive_by_default() -> None:
    matcher = matching.WordMatcher("light")
    assert surfaces(matcher, "Lights out.") == ["Lights"]


def test_exact_mode_refuses_an_inflected_form() -> None:
    matcher = matching.WordMatcher("run", mode="exact")
    assert matcher.match("She was running away, so he ran.") == []
    assert surfaces(matcher, "I run every day.") == ["run"]


def test_exact_mode_matches_a_literal_phrase() -> None:
    matcher = matching.WordMatcher("give up", mode="exact")
    assert surfaces(matcher, "I give up. / She gave up.") == ["give up"]


def test_regex_mode_hands_the_pattern_to_the_caller() -> None:
    matcher = matching.WordMatcher(r"blow\W+(?:\w+\W+){0,3}mind", mode="regex")
    assert surfaces(matcher, "it will blow your mind completely") == ["blow your mind"]


def test_an_unknown_mode_is_rejected_rather_than_silently_downgraded() -> None:
    with pytest.raises(ValueError, match="unknown match mode"):
        matching.WordMatcher("light", mode="fuzzy")


def test_an_empty_target_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        matching.WordMatcher("   ")


def test_match_offsets_point_at_the_matched_text() -> None:
    matcher = matching.WordMatcher("light")
    line = "She turned the light off."
    hit = matcher.find(line)
    assert hit is not None
    assert line[hit.start : hit.end] == "light"
    assert hit.exact is True


def test_an_inflected_hit_is_reported_as_not_exact() -> None:
    hit = matching.WordMatcher("light").find("He lit the fire.")
    assert hit is not None and hit.exact is False


def test_matches_do_not_overlap() -> None:
    """Adjacent repeats are separate hits; a hit is never counted twice over itself."""
    matcher = matching.WordMatcher("very")
    assert surfaces(matcher, "very very good") == ["very", "very"]
    # a phrase consumes its whole width, so the next scan starts after it
    phrase = matching.WordMatcher("give up")
    assert surfaces(phrase, "give up give up") == ["give up", "give up"]


def test_variants_never_contain_an_empty_string() -> None:
    for word in ("run", "light", "a", "city", "box", "carry"):
        assert "" not in matching.variants(word)


def test_variants_of_an_irregular_word_are_the_curated_set() -> None:
    forms = matching.variants("give")
    assert {"give", "gives", "gave", "given", "giving"} <= forms
    assert "gived" not in forms


# ------------------------------------------------------------------ best_match


def test_best_match_prefers_the_literal_form_over_an_inflected_one() -> None:
    matcher = matching.WordMatcher("light")
    line = "The lit room and the light itself."
    hit = matching.best_match(line, matcher)
    assert hit is not None and hit.surface == "light" and hit.exact


# -------------------------------------------------------------------- scoring


def test_a_short_line_scores_below_a_usable_one() -> None:
    matcher = matching.WordMatcher("light")
    short = matching.score_example(
        "Light.", matcher.find("Light."), start_ms=0, end_ms=400
    )
    good = matching.score_example(
        "She turned the light off slowly.",
        matcher.find("She turned the light off slowly."),
        start_ms=0,
        end_ms=3_000,
    )
    assert good.score > short.score
    assert "short" in short.reasons or "no-context" in short.reasons


def test_a_line_that_is_only_the_target_scores_low() -> None:
    matcher = matching.WordMatcher("light")
    scored = matching.score_example(
        "light", matcher.find("light"), start_ms=0, end_ms=2_500
    )
    assert "no-context" in scored.reasons


def test_score_is_deterministic() -> None:
    matcher = matching.WordMatcher("light")
    line = "She turned the light off."
    first = matching.score_example(line, matcher.find(line), start_ms=0, end_ms=3_000)
    second = matching.score_example(line, matcher.find(line), start_ms=0, end_ms=3_000)
    assert first == second


# ------------------------------------------------------------------- word spans


def test_word_spans_cover_the_whole_window_without_gaps() -> None:
    spans = matching.word_spans("She turned the light off", 1_000, 4_000)
    assert [span.text for span in spans] == ["She", "turned", "the", "light", "off"]
    assert spans[0].start_ms == 1_000
    assert spans[-1].end_ms == 4_000
    for earlier, later in zip(spans, spans[1:]):
        assert earlier.end_ms == later.start_ms


def test_word_spans_are_deterministic() -> None:
    line = "One two three four"
    assert matching.word_spans(line, 0, 2_000) == matching.word_spans(line, 0, 2_000)


def test_word_spans_are_empty_for_an_empty_line() -> None:
    assert matching.word_spans("", 0, 1_000) == []


# --------------------------------------------------------------- content words


def test_stopwords_are_not_teachable_content_words() -> None:
    words = matching.content_words("It was the light of the world")
    assert "light" in words and "world" in words
    assert not {"the", "of", "it", "was"} & set(words)


def test_content_words_keep_first_appearance_order_and_dedupe() -> None:
    assert matching.content_words("light water light") == ["light", "water"]


# --------------------------------------------------------- caption emphasis


def _ass_for(text: str, *, needle: str, karaoke: bool) -> str:
    style = CaptionStyle(name="W", karaoke=karaoke, highlight="&H0000D7FF")
    caption = Caption(
        start_ms=0,
        end_ms=2_000,
        text=text,
        lang="en",
        style_ref="w",
        words=matching.word_spans(text, 0, 2_000) if karaoke else [],
        meta={EMPHASIS_KEY: needle},
    )
    body = build_ass([caption], styles={"w": style}, width=1080, height=1920)
    return next(line for line in body.splitlines() if line.startswith("Dialogue"))


def test_emphasis_colours_the_target_inside_a_karaoke_line() -> None:
    event = _ass_for("She turned the light off", needle="light", karaoke=True)
    assert "{\\k" in event  # karaoke survives
    assert "\\c&H0000D7FF\\b1}light{\\c&H00FFFFFF\\b0}" in event


def test_emphasis_leaves_the_other_words_uncoloured() -> None:
    event = _ass_for("She turned the light off", needle="light", karaoke=True)
    body = event.split(",", 9)[9]
    assert body.count("\\c&H0000D7FF") == 1


def test_emphasis_can_cover_a_phrase_across_word_spans() -> None:
    event = _ass_for("Do not give up now", needle="give up", karaoke=True)
    assert "\\c&H0000D7FF\\b1}give{\\c&H00FFFFFF\\b0}" in event
    assert "\\c&H0000D7FF\\b1}up{\\c&H00FFFFFF\\b0}" in event


def test_emphasis_without_karaoke_still_colours_the_target() -> None:
    event = _ass_for("She turned the light off", needle="light", karaoke=False)
    assert "\\c&H0000D7FF\\b1}light{\\c&H00FFFFFF\\b0}" in event
    assert "{\\k" not in event


def test_a_caption_without_emphasis_is_rendered_exactly_as_before() -> None:
    style = CaptionStyle(name="W", karaoke=False)
    caption = Caption(start_ms=0, end_ms=1_000, text="Plain line", style_ref="w")
    body = build_ass([caption], styles={"w": style}, width=1080, height=1920)
    assert "Plain line" in body
    assert "\\c" not in body.split("[Events]")[1]


def test_an_emphasis_that_is_not_in_the_line_changes_nothing() -> None:
    event = _ass_for("She turned it off", needle="light", karaoke=False)
    assert "\\c&H0000D7FF" not in event
