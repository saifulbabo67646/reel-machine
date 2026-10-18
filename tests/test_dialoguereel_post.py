import copy

import pytest

from reelmachine.recipes.dialoguereel import cast as casts
from reelmachine.recipes.dialoguereel import post
from reelmachine.recipes.dialoguereel.models import CastSpec, DialogueReelInputs
from reelmachine.recipes.dialoguereel.recipe import EXAMPLE
from reelmachine.recipes.dialoguereel.strings import STRINGS


def _example(**over):
    data = copy.deepcopy(EXAMPLE) | over
    inputs = DialogueReelInputs.model_validate(data)
    return inputs, casts.resolve(CastSpec.model_validate(data["cast"]))


def test_hashtags_japanese_n5():
    tags = post.hashtags("ja", "N5")
    assert "#JLPTN5" in tags and "#日本語" in tags and "#jlpt" in tags and 5 <= len(tags) <= 10
    assert all(t.startswith("#") and " " not in t for t in tags)


@pytest.mark.parametrize("lang,level", [("ko", "TOPIK 1"), ("es", "A2"), ("zh", "HSK2"), ("de", ""), ("ja", ""),
                                        ("ja-JP", "N3"), ("ko", "")])
def test_hashtags_count(lang, level):
    tags = post.hashtags(lang, level)
    assert 5 <= len(tags) <= 10 and len(set(tags)) == len(tags)
    assert all(t.startswith("#") and " " not in t for t in tags)


def test_hashtag_level_tags():
    assert "#TOPIK1" in post.hashtags("ko", "TOPIK 1")
    assert "#A2" in post.hashtags("es", "A2")
    assert "#A2" in post.hashtags("es", "a2") and "#HSK2" in post.hashtags("zh", "hsk 2")
    assert "#HSK2" in post.hashtags("zh", "HSK2")
    assert "#JLPTN5" not in post.hashtags("ja", "beginner")


def test_every_language_has_a_dialogue_heading():
    assert all(table.get("dialogue") for table in STRINGS.values())


def test_post_for_the_example():
    inputs, cast = _example()
    text = post.post_markdown(inputs, cast)
    assert text.startswith("Helping Mom at home 🧺\nCan you understand this?\n\n**Dialogue**\n")
    assert "Cupcake: おかあさん。\nOkaasan.\n→ Mom\n\n" in text
    assert "Mug: せんたくをしてくれる？" in text
    assert "**Words from this video**" in text
    assert "- せんたく (sentaku) — laundry" in text
    assert "Comment your answer" in text and "Follow for daily N5" in text
    assert text.rstrip().splitlines()[-1].startswith("#learnjapanese")


def test_post_without_keys_has_no_words_and_unknown_speaker_falls_back():
    inputs, cast = _example()
    data = inputs.model_dump()
    for line in data["lines"]:
        line["keys"] = []
    data["lines"][0]["speaker"] = "ghost"
    text = post.post_markdown(DialogueReelInputs.model_validate(data), cast)
    assert "Words from this video" not in text and "ghost: おかあさん。" in text


def test_post_dedups_and_caps_the_words():
    inputs, cast = _example()
    data = inputs.model_dump()
    data["lines"][0]["keys"] = [{"text": "せんたく", "reading": "sentaku", "meaning": "laundry"}]
    text = post.post_markdown(DialogueReelInputs.model_validate(data), cast)
    assert text.count("- せんたく (") == 1


def test_post_escapes_markdown():
    inputs, cast = _example()
    data = inputs.model_dump()
    data["lines"][0] |= {"text": "# Title", "meaning": "*bold* <b> [x](y)"}
    text = post.post_markdown(DialogueReelInputs.model_validate(data), cast)
    assert "\n# " not in text and "<b>" not in text and "[x](y)" not in text and "\\](y)" in text
