"""Stages: typed units of work with a cacheable, resumable result.

A stage writes its output to the job work directory. A stage is cacheable by content
hash: `sha256(stage id | revision | payload | config subset | provider pins)`. A cache
hit reloads the output instead of running the stage, which is what makes a job resumable
and a repeated render cheap.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .assets import AssetStore
from .job import Job


@runtime_checkable
class CancelToken(Protocol):
    @property
    def cancelled(self) -> bool: ...

    def raise_if_cancelled(self) -> None: ...

    def register_process(self, process: Any) -> None: ...


@runtime_checkable
class ProgressReporter(Protocol):
    def stage(self, stage_id: str, index: int, total: int) -> None: ...

    def percent(self, value: float) -> None: ...

    def detail(self, text: str) -> None: ...


@runtime_checkable
class ProviderAccess(Protocol):
    def get(self, role: str) -> Any: ...

    def pin(self, role: str) -> str: ...


@runtime_checkable
class QuotaAccount(Protocol):
    def spend(self, provider: str, units: int, *, unit: str = "", note: str = "") -> None: ...


class NullProgress:
    def stage(self, stage_id: str, index: int, total: int) -> None:
        return None

    def percent(self, value: float) -> None:
        return None

    def detail(self, text: str) -> None:
        return None


class NeverCancel:
    cancelled = False

    def raise_if_cancelled(self) -> None:
        return None

    def register_process(self, process: Any) -> None:
        return None


class NullQuota:
    def spend(self, provider: str, units: int, *, unit: str = "", note: str = "") -> None:
        return None


class StaticProviders:
    """Resolve roles from a fixed mapping; used by tests and simple runs."""

    def __init__(self, providers: dict[str, Any] | None = None) -> None:
        self._providers = dict(providers or {})

    def get(self, role: str) -> Any:
        try:
            return self._providers[role]
        except KeyError as exc:
            raise KeyError(f"no provider bound to role {role!r}") from exc

    def pin(self, role: str) -> str:
        provider = self._providers.get(role)
        return getattr(provider, "name", "") if provider is not None else ""


@dataclass(slots=True)
class StageContext:
    job: Job
    workdir: Path
    assets: AssetStore
    config: Any = None
    providers: Any = field(default_factory=StaticProviders)
    progress: Any = field(default_factory=NullProgress)
    cancel: Any = field(default_factory=NeverCancel)
    quota: Any = field(default_factory=NullQuota)
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("reelmachine"))
    extra: dict[str, Any] = field(default_factory=dict)


class Stage(Protocol):
    id: str
    revision: int

    def run(self, ctx: StageContext, payload: Any) -> Any: ...


class StageCache:
    """Content-hash keyed stage output store on disk."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @staticmethod
    def make_key(
        stage_id: str,
        revision: int,
        payload: Any,
        *,
        config_subset: dict[str, Any] | None = None,
        provider_pins: dict[str, str] | None = None,
    ) -> str:
        blob = json.dumps(
            {
                "stage": stage_id,
                "revision": revision,
                "payload": payload,
                "config": config_subset or {},
                "providers": provider_pins or {},
            },
            sort_keys=True,
            default=str,
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def path_for(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def load(self, key: str) -> Any | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, key: str, output: Any) -> str:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(output, default=str, ensure_ascii=False)
        path.write_text(text, encoding="utf-8")
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
