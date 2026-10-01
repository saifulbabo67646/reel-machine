"""A minimal third-party-style recipe used by registry discovery tests.

It is deliberately *not* part of the reelmachine package: the registry must load it from
an entry point value exactly as it would load a recipe shipped by another distribution.
"""

from __future__ import annotations

from pydantic import BaseModel

from reelmachine.core.recipe import ProbeReport, RecipeSpec


class FixtureInputs(BaseModel):
    text: str = "hello"


class FixtureRecipe:
    spec = RecipeSpec(
        id="fixture-recipe",
        title="Fixture",
        summary="A recipe from outside the core package.",
        input_model=FixtureInputs,
        example={"text": "hello"},
    )

    def probe(self, inputs: BaseModel, ctx) -> ProbeReport:  # pragma: no cover - trivial
        return ProbeReport()

    def verify(self, ctx, result):  # pragma: no cover - trivial
        return None
