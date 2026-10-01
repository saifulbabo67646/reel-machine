"""The manifest: everything that affected an output, in one durable record.

A hosted product must be able to answer "where did this come from" for every job: the
resolved inputs and configuration, the providers and models that ran, the provenance and
licence of every asset, the render mode, and the verification result.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .assets import Asset, Provenance

ConfigSource = Literal["job", "recipe-env", "env", "default", "explicit"]


class ConfigValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any = None
    source: ConfigSource = "default"


class AssetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    asset: Asset


class ProviderPin(BaseModel):
    """Which provider (and model/voice) produced part of this output."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: str = ""
    models: list[str] = Field(default_factory=list)
    voice: str = ""
    notes: str = ""


class StageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    index: int = 0
    revision: int = 1
    cached: bool = False
    started_ms: int = 0
    finished_ms: int = 0
    duration_ms: int = 0
    output_hash: str = ""
    cache_key: str = ""
    providers: dict[str, str] = Field(default_factory=dict)
    error: str = ""


class QuotaSpend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = ""
    unit: str = ""
    units: int = 0
    caller: str = ""


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ok: bool
    detail: str = ""


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    checks: list[CheckResult] = Field(default_factory=list)
    notes: str = ""
    render_mode: str = ""
    visible_degradation: list[str] = Field(default_factory=list)


class TimelineRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    digest: str = ""
    artifact: str = ""
    duration_ms: int = 0
    render_mode: str = ""


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    job_id: str
    recipe: str
    recipe_version: int = 1
    created_ms: int = 0
    caller: str = "local"

    inputs: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, ConfigValue] = Field(default_factory=dict)
    render_mode: str = ""
    timeline: TimelineRef = Field(default_factory=TimelineRef)

    assets: list[AssetRecord] = Field(default_factory=list)
    stages: list[StageRecord] = Field(default_factory=list)
    providers: dict[str, ProviderPin] = Field(default_factory=dict)
    quota: dict[str, QuotaSpend] = Field(default_factory=dict)
    verification: VerificationReport | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    notes: str = ""

    def add_asset(self, asset: Asset, *, name: str = "") -> AssetRecord:
        record = AssetRecord(name=name, asset=asset)
        self.assets.append(record)
        return record

    def pin(
        self,
        role: str,
        provider_id: str,
        *,
        version: str = "",
        models: list[str] | None = None,
        voice: str = "",
        notes: str = "",
    ) -> ProviderPin:
        pin = ProviderPin(id=provider_id, version=version, models=list(models or []), voice=voice, notes=notes)
        self.providers[role] = pin
        return pin

    def provenance_of(self, asset_id: str) -> Provenance | None:
        for record in self.assets:
            if record.asset.id == asset_id:
                return record.asset.provenance
        return None
