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
        provider = self.resolved.get(role)
        if provider is None:
            return ""
        return getattr(provider, "name", "") or type(provider).__name__

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
    if req.default:
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

    Providers in the source and corpus groups are instantiated so their declared
    requirements can be reported; everything else reports load status only.
    """
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
            if settings is not None and isinstance(value, type) and group in {"sources", "corpora"}:
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
