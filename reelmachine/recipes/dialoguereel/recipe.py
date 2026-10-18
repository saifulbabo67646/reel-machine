"""The dialoguereel recipe: two characters act out a short conversation as a vertical reel."""

from __future__ import annotations

from typing import Any

from ...core.manifest import VerificationReport
from ...core.recipe import ProbeContext, ProbeReport, RecipeSpec
from ...core.stage import StageContext
from .models import DialogueReelInputs


class DialogueReelRecipe:
    spec = RecipeSpec(
        id="dialoguereel",
        title="Dialogue reel",
        summary="Two cute characters act out a short language-learning conversation as a vertical reel.",
        input_model=DialogueReelInputs,
        version=1,
        stages=(),
    )

    def probe(self, inputs: DialogueReelInputs, ctx: ProbeContext) -> ProbeReport:
        return ProbeReport(ok=True)

    def verify(self, ctx: StageContext, result: dict[str, Any]) -> VerificationReport | None:
        return None
