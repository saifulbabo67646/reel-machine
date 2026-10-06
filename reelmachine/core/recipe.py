"""Recipe declarations: what can be made, and what it needs.

A recipe is a declaration (input model, ordered stages, provider requirements, cost,
artifacts) plus a small amount of category-specific logic. It lives in its own package
and registers through the same entry point mechanism as any third-party recipe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from .manifest import CheckResult, VerificationReport
from .stage import Stage, StageContext


@dataclass(frozen=True)
class ProviderReq:
    """One provider a recipe's stages need, by role."""

    group: str
    default: str = ""
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class StageSpec:
    stage: Stage
    uses: tuple[str, ...] = ()
    feeds: str | None = None  # id of the stage whose output is this stage's payload


@dataclass(frozen=True)
class CostNote:
    unit: str = ""
    estimate: str = ""
    notes: str = ""
    providers: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecipeSpec:
    id: str
    title: str
    summary: str
    input_model: type[BaseModel]
    version: int = 1
    schema_version: int = 1
    stages: tuple[StageSpec, ...] = ()
    providers: Mapping[str, ProviderReq] = field(default_factory=dict)
    cost: CostNote = field(default_factory=CostNote)
    artifacts: tuple[str, ...] = ()
    render_modes: tuple[str, ...] = ()
    example: Mapping[str, Any] = field(default_factory=dict)

    def schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()


class ProbeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    checks: list[CheckResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    quota_free: bool = True


@dataclass(slots=True)
class ProbeContext:
    config: Any = None
    providers: Any = None
    workdir: Any = None
    log: Any = None


@runtime_checkable
class Recipe(Protocol):
    spec: RecipeSpec

    def probe(self, inputs: BaseModel, ctx: ProbeContext) -> ProbeReport: ...

    def verify(self, ctx: StageContext, result: Any) -> VerificationReport | None: ...


def stage_ids(spec: RecipeSpec) -> Sequence[str]:
    return [stage.stage.id for stage in spec.stages]


def instantiate_recipe(value: Any) -> Recipe:
    """Entry points may point at a class or at a ready-made object."""
    return value() if isinstance(value, type) else value
