import pytest
from pydantic import ValidationError

from reelmachine.recipes.dialoguereel.models import BuiltinSource, DialogueReelInputs

KID = {"id": "kid", "name": "Cupcake", "side": "left", "source": {"kind": "builtin", "name": "cupcake"}}
MOM = {"id": "mom", "name": "Mug", "side": "right", "source": {"kind": "builtin", "name": "mug"}}
SCRIPT = {
    "title": "Helping Mom at home",
    "cast": {"characters": [KID, MOM]},
    "lines": [
        {"speaker": "kid", "text": "お母さん、手伝うよ", "reading": "okaasan, tetsudau yo", "meaning": "Mom, I'll help"},
        {"speaker": "mom", "text": "ありがとう", "reading": "arigatou", "meaning": "Thank you"},
        {"speaker": "kid", "text": "何をしようか", "reading": "nani o shiyou ka", "meaning": "What shall I do?"},
    ],
}
ONE_CHARACTER = {"characters": [KID]}
SAME_SIDE = {"characters": [KID, {**MOM, "side": "left"}]}
SAME_ID = {"characters": [KID, {**MOM, "id": "kid"}]}


def test_a_two_character_script_validates():
    inputs = DialogueReelInputs.model_validate(SCRIPT)
    assert inputs.cast.characters[0].source.kind == "builtin" and inputs.mode == "storyboard"


@pytest.mark.parametrize("cast", [ONE_CHARACTER, SAME_SIDE, SAME_ID])
def test_a_cast_needs_two_distinct_characters_on_both_sides(cast):
    with pytest.raises(ValidationError):
        DialogueReelInputs.model_validate({**SCRIPT, "cast": cast})


def test_a_cast_id_string_is_accepted_as_is():
    assert DialogueReelInputs.model_validate({**SCRIPT, "cast": "cast_0123456789ab"}).cast == "cast_0123456789ab"


def test_colours_must_be_hex():
    with pytest.raises(ValidationError):
        BuiltinSource(kind="builtin", name="mug", colors={"primary": "blue"})


def test_the_recipe_is_registered():
    from reelmachine.core.registry import Registry
    assert Registry().find_recipe("dialoguereel") is not None


@pytest.mark.parametrize("name", ["my-reel", "Reel_01.v2", "a", "x" * 80])
def test_a_safe_output_name_is_accepted(name):
    assert DialogueReelInputs.model_validate({**SCRIPT, "name": name}).name == name


@pytest.mark.parametrize("name", ["../escape", "a/b", "", ".hidden", "-flag", "x" * 81, "a b", "a\\b", "名前"])
def test_an_output_name_that_is_not_a_plain_file_stem_is_refused(name):
    with pytest.raises(ValidationError):
        DialogueReelInputs.model_validate({**SCRIPT, "name": name})
