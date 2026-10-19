"""post.md: captions only, tidy sections, 8 to 12 hashtags."""

import re
from pathlib import Path

import pytest

from reelmachine.recipes import techreel
from reelmachine.recipes.techreel.models import TechReelInputs
from reelmachine.recipes.techreel.post import hashtags, post_markdown

EXAMPLES = sorted((Path(techreel.__file__).parent / "examples").glob("*.json"))


def load(path: Path) -> TechReelInputs:
    return TechReelInputs.model_validate_json(path.read_text(encoding="utf-8"))


def reel(**over):
    base = {"title": "Pocket LLM tricks", "language": "en", "voice": "fake:default", "beats": [
        {"kind": "hook", "lines": [{"say": "Your *old* laptop can run AI."}]},
        {"kind": "pain", "lines": [{"say": "Cloud bills hurt."}]},
        {"kind": "reveal", "lines": [{"say": "Run it locally."}]},
        {"kind": "cta", "lines": [{"say": "Follow for more."}, {"say": "Share it."}]},
    ]}
    return TechReelInputs.model_validate({**base, **over})


def test_sections_in_order_with_markup_stripped():
    text = post_markdown(reel(sources=["https://example.com/a"]))
    lines = text.splitlines()
    assert lines[0] == "Your old laptop can run AI."
    assert "- Cloud bills hurt." in lines and "- Run it locally." in lines
    assert "Follow for more. Share it." in lines
    assert lines.index("Sources:") + 1 == lines.index("- https://example.com/a")
    assert lines[-1].startswith("#") and "*" not in text


def test_no_sources_or_creator_leaves_no_header_or_none():
    text = post_markdown(reel())
    assert "Sources:" not in text and "None" not in text


def test_say_with_a_separate_text_never_appears():
    inputs = reel(language="hi", beats=[
        {"kind": "hook", "lines": [{"say": "नमस्ते दोस्तों", "text": "Namaste dosto"}]},
        {"kind": "pain", "lines": [{"say": "समस्या यहाँ है", "text": "Problem yahan hai"}]},
        {"kind": "cta", "lines": [{"say": "फ़ॉलो करो", "text": "Follow karo"}]},
    ])
    text = post_markdown(inputs)
    assert "Namaste dosto" in text and "Follow karo" in text
    assert not re.search(r"[ऀ-ॿ]", text)


def test_title_hashtags_dedupe_and_filter():
    tags = hashtags("en", "AI tools: Local-LLM 4 you & the Tech")
    assert [t.casefold() for t in tags].count("#ai") == 1 and tags.count("#tech") == 1
    assert "#tools" in tags and "#you" in tags and "#4" not in tags
    assert all(t[1:].isalnum() for t in tags) and 8 <= len(tags) <= 12


def test_devanagari_and_japanese_title_words_stay_intact():
    assert "#नमस्ते" in hashtags("hi", "नमस्ते दोस्तों AI")
    assert "#量子化" in hashtags("ja", "量子化 入門")


def test_other_languages_use_english_set_and_short_sets_are_topped_up():
    assert hashtags("fr", "x")[:6] == ["#ai", "#tech", "#aitools", "#opensource", "#llm", "#coding"]
    assert 8 <= len(hashtags("ja", "AI")) <= 12


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_every_example_makes_a_well_formed_post(path):
    inputs = load(path)
    text = post_markdown(inputs)
    lines = text.splitlines()
    assert text.endswith("\n") and "None" not in text and "*" not in text
    assert lines[0] and not lines[0].startswith("#")
    tags = lines[-1].split()
    assert 8 <= len(tags) <= 12 and all(t.startswith("#") and " " not in t for t in tags)
    assert len({t.casefold() for t in tags}) == len(tags)
    assert "Sources:" in lines
    for beat in inputs.beats:
        for line in beat.lines:
            if line.text and line.text.replace("*", "") != line.say:   # a real respelling, not just markup
                assert line.say not in text
