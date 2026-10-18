"""The shipped dialoguereel examples parse, and lint clean of the rules a learner would notice."""

from pathlib import Path

import pytest

from reelmachine.recipes import dialoguereel
from reelmachine.recipes.dialoguereel import lint
from reelmachine.recipes.dialoguereel.models import DialogueReelInputs

EXAMPLES = sorted((Path(dialoguereel.__file__).parent / "examples").glob("*.json"))


def speakers_of(inputs: DialogueReelInputs) -> set[str]:
    return {c.id for c in inputs.cast.characters}


def test_two_examples_ship():
    assert {p.name for p in EXAMPLES} == {"konbini-n5-ja.json", "cafe-topik1-ko.json"}


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_lints_clean(path):
    inputs = DialogueReelInputs.model_validate_json(path.read_text())
    found = lint.check(inputs.model_copy(update={"mode": "full"}), speakers_of(inputs))
    assert [f for f in found if f.severity == "error"] == []
    assert [f.rule for f in found if f.rule in ("level", "key-missing", "no-reading", "long-line")] == []
