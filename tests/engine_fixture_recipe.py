"""A tiny recipe used to exercise the engine without ffmpeg or network.

It is loaded through an entry point by the engine tests, exactly as a third-party recipe
would be.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict

from reelmachine.core.errors import ProviderUnavailable
from reelmachine.core.manifest import CheckResult, VerificationReport
from reelmachine.core.recipe import CostNote, ProbeReport, RecipeSpec, StageSpec
from reelmachine.core.stage import StageContext, StageOutput


class EngineInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = "x"
    sleep_s: float = 0.0
    fail: bool = False


class EmitOutput(StageOutput):
    value: str = ""
    artifact: str = ""


class EmitStage:
    id = "emit"
    revision = 1
    cacheable = False
    output_model = EmitOutput

    def run(self, ctx: StageContext, payload: dict) -> EmitOutput:
        inputs = EngineInputs.model_validate(payload)
        if inputs.sleep_s:
            # cooperative, like the real stages: poll between units of work
            deadline = time.monotonic() + inputs.sleep_s
            while time.monotonic() < deadline:
                ctx.cancel.raise_if_cancelled()
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        ctx.cancel.raise_if_cancelled()
        if inputs.fail:
            raise ProviderUnavailable(
                "the fixture was asked to fail",
                hint="set fail=false",
                stage=self.id,
            )
        path = ctx.workdir / "value.txt"
        path.write_text(inputs.value, encoding="utf-8")
        ctx.progress.detail(f"wrote {path.name}")
        return EmitOutput(value=inputs.value, artifact=str(path))


class EngineFixtureRecipe:
    spec = RecipeSpec(
        id="engine-fixture",
        title="Engine fixture",
        summary="A minimal recipe used to test the engine itself.",
        input_model=EngineInputs,
        stages=(StageSpec(stage=EmitStage()),),
        cost=CostNote(unit="none", estimate="0"),
        artifacts=("manifest.json",),
        render_modes=("fixture",),
        example={"value": "hello"},
    )

    def probe(self, inputs: EngineInputs, ctx) -> ProbeReport:
        return ProbeReport(ok=not inputs.fail, details={"value": inputs.value})

    def verify(self, ctx: StageContext, result) -> VerificationReport:
        return VerificationReport(
            ok=True,
            render_mode="fixture",
            checks=[CheckResult(name="emitted", ok=True, detail=ctx.job.id)],
        )
