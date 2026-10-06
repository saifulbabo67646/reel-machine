"""Provider discovery through entry points.

Built-ins register through exactly the same mechanism as third-party packages. Discovery
takes an injectable `entry_points()` provider so tests can prove third-party registration
without installing a distribution, and so the real path and the test path run identical
lookup code.

Loading is lazy and failure-isolated: a broken entry point is reported as unavailable
instead of breaking the registry, and only recipes that require it fail.
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import os
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .errors import ProviderUnavailable

GROUP_PREFIX = "reelmachine"

GROUPS = (
    "recipes",
    "sources",
    "corpora",
    "narration",
    "scripts",
    "renderers",
    "backgrounds",
    "styles",
    "storage",
)

EntryPointsProvider = Callable[[str], Iterable[importlib_metadata.EntryPoint]]


def default_entry_points(group: str) -> Iterable[importlib_metadata.EntryPoint]:
    return importlib_metadata.entry_points(group=group)


@dataclass(slots=True)
class LoadResult:
    name: str
    ok: bool
    value: Any = None
    error: str = ""


class Registry:
    def __init__(
        self,
        *,
        entry_points: EntryPointsProvider | None = None,
        prefix: str = GROUP_PREFIX,
    ) -> None:
        self._entry_points = entry_points or default_entry_points
        self._prefix = prefix
        self._cache: dict[str, dict[str, LoadResult]] = {}

    def group_name(self, group: str) -> str:
        return f"{self._prefix}.{group}"

    def entry_points_for(self, group: str) -> list[importlib_metadata.EntryPoint]:
        return list(self._entry_points(self.group_name(group)))

    def names(self, group: str) -> list[str]:
        return sorted(ep.name for ep in self.entry_points_for(group))

    def disabled(self) -> set[str]:
        raw = os.environ.get("REEL_PROVIDERS_DISABLED", "")
        return {part.strip().lower() for part in raw.split(",") if part.strip()}

    def load(self, group: str, name: str) -> Any:
        result = self.load_all(group).get(name)
        if result is None:
            available = ", ".join(sorted(self.load_all(group))) or "none"
            raise ProviderUnavailable(
                f"no {group} provider named {name!r}",
                hint=f"available {group} providers: {available}",
                details={"group": group, "name": name},
            )
        if not result.ok:
            raise ProviderUnavailable(
                f"{group} provider {name!r} is installed but failed to load: {result.error}",
                hint="check the package install and its requirements",
                details={"group": group, "name": name, "error": result.error},
            )
        return result.value

    def load_all(self, group: str, *, refresh: bool = False) -> dict[str, LoadResult]:
        if not refresh and group in self._cache:
            return self._cache[group]
        results: dict[str, LoadResult] = {}
        disabled = self.disabled()
        for entry_point in self.entry_points_for(group):
            if entry_point.name.lower() in disabled:
                results[entry_point.name] = LoadResult(
                    name=entry_point.name, ok=False, error="disabled by REEL_PROVIDERS_DISABLED"
                )
                continue
            try:
                value = entry_point.load()
            except Exception as exc:  # noqa: BLE001 - a broken provider must not break the registry
                results[entry_point.name] = LoadResult(
                    name=entry_point.name, ok=False, error=f"{type(exc).__name__}: {exc}"
                )
            else:
                results[entry_point.name] = LoadResult(name=entry_point.name, ok=True, value=value)
        self._cache[group] = results
        return results

    def available(self, group: str) -> dict[str, bool]:
        return {name: result.ok for name, result in self.load_all(group).items()}

    def recipes(self) -> dict[str, Any]:
        return {
            name: result.value
            for name, result in self.load_all("recipes").items()
            if result.ok
        }

    def find_recipe(self, recipe_id: str) -> Any:
        return self.load("recipes", recipe_id)


def registry_from_entry_points(
    entry_points: Iterable[importlib_metadata.EntryPoint], *, prefix: str = GROUP_PREFIX
) -> Registry:
    """Build a registry over a fixed set of entry points (used by tests)."""
    by_group: dict[str, list[importlib_metadata.EntryPoint]] = {}
    for entry_point in entry_points:
        by_group.setdefault(entry_point.group, []).append(entry_point)

    def provider(group: str) -> Iterable[importlib_metadata.EntryPoint]:
        return by_group.get(group, [])

    return Registry(entry_points=provider, prefix=prefix)
