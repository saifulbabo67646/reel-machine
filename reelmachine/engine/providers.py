"""Resolve a recipe's provider roles through the registry.

A recipe declares roles (`corpus`, `source`, …) with a group and a default name; the
engine turns that declaration into live providers for a deployment. `ProviderSet` doubles
as the `ProviderAccess` the stage context uses, so stage cache keys and the manifest get
provider pins from the same place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.errors import ProviderUnavailable
from ..core.recipe import ProviderReq, RecipeSpec
from ..core.registry import GROUPS, Registry


@dataclass
class ProviderSet:
    resolved: dict[str, Any] = field(default_factory=dict)

    def get(self, role: str) -> Any:
        try:
            return self.resolved[role]
        except KeyError as exc:
            raise KeyError(f"no provider bound to role {role!r}") from exc

    def pin(self, role: str) -> str:
        """`name[:version]` — part of every stage cache key, so a provider that changes
        its parsing invalidates the outputs it produced rather than serving them again."""
        provider = self.resolved.get(role)
        if provider is None:
            return ""
        name = getattr(provider, "name", "") or type(provider).__name__
        version = str(getattr(provider, "version", "") or "")
        return f"{name}:{version}" if version else name

    def __getitem__(self, role: str) -> Any:
        return self.get(role)

    def __contains__(self, role: str) -> bool:
        return role in self.resolved

    def close(self) -> None:
        for provider in self.resolved.values():
            close = getattr(provider, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - closing must never mask the run
                    pass

    def __enter__(self) -> "ProviderSet":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _default_name(role: str, req: ProviderReq, settings: Any) -> str:
    if req.group == "narration":
        # the deployment decides which voice provider it serves (REEL_TTS)
        return getattr(settings, "tts", "") or req.default or "fake"
    if req.default:
        # a recipe that names a default source gets it (storyreel: vidvault, because
        # it downloads the whole film); REEL_SOURCE steers the recipes that declare
        # none (nadeshiko-cut), so one deployment can serve both
        return req.default
    if req.group == "sources":
        return getattr(settings, "source", "") or "local"
    raise ProviderUnavailable(
        f"recipe role {role!r} has no default provider",
        hint=f"configure a {req.group} provider for this deployment",
        details={"role": role, "group": req.group},
    )


def resolve_recipe_providers(
    spec: RecipeSpec,
    settings: Any,
    *,
    overrides: dict[str, str] | None = None,
    registry: Registry | None = None,
) -> ProviderSet:
    registry = registry or Registry()
    overrides = dict(overrides or {})
    resolved: dict[str, Any] = {}
    for role, req in spec.providers.items():
        name = overrides.get(role) or _default_name(role, req, settings)
        provider_class = registry.load(req.group, name)
        try:
            resolved[role] = provider_class(settings)
        except TypeError as exc:
            raise ProviderUnavailable(
                f"{req.group} provider {name!r} could not be constructed",
                hint="providers take a single settings argument",
                details={"role": role, "error": str(exc)},
            ) from exc
    return ProviderSet(resolved)


def provider_health(registry: Registry | None = None, settings: Any = None) -> list[dict[str, Any]]:
    """A row per discoverable provider: ok / missing / error / disabled.

    Providers whose requirements depend on the deployment (sources, corpora, narration,
    subtitles, story, renderers) are instantiated so `missing()` can be reported; storage
    and styles report load status only.
    """
    INSPECTED = {"sources", "corpora", "narration", "subtitles", "story", "renderers"}
    registry = registry or Registry()
    rows: list[dict[str, Any]] = []
    for group in GROUPS:
        for name, result in registry.load_all(group).items():
            row: dict[str, Any] = {"group": group, "name": name, "status": "ok", "detail": ""}
            if not result.ok:
                row["status"] = "disabled" if "disabled" in result.error else "error"
                row["detail"] = result.error
                rows.append(row)
                continue
            value = result.value
            if settings is not None and isinstance(value, type) and group in INSPECTED:
                try:
                    instance = value(settings)
                except Exception as exc:  # noqa: BLE001 - a provider that cannot start is a row
                    row.update(status="error", detail=f"{type(exc).__name__}: {exc}")
                    rows.append(row)
                    continue
                missing_fn = getattr(instance, "missing", None)
                if callable(missing_fn):
                    try:
                        missing = list(missing_fn())
                    except Exception:  # noqa: BLE001
                        missing = []
                    if missing:
                        row.update(status="missing", detail="; ".join(missing))
                close = getattr(instance, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # noqa: BLE001
                        pass
            rows.append(row)
    return rows
