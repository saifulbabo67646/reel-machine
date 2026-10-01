"""Entry-point discovery: first-party machinery, third-party proof."""

from __future__ import annotations

from importlib.metadata import EntryPoint

import pytest

from reelmachine.core.errors import ProviderUnavailable
from reelmachine.core.registry import Registry, registry_from_entry_points

# importing the module first mirrors what an installed distribution would provide
from fixture_provider import FixtureRecipe  # noqa: F401  (imported for sys.modules)


def _entry(name: str, value: str, group: str = "reelmachine.recipes") -> EntryPoint:
    return EntryPoint(name=name, value=value, group=group)


def test_discovery_loads_a_third_party_recipe() -> None:
    registry = registry_from_entry_points(
        [_entry("fixture", "fixture_provider:FixtureRecipe")]
    )
    assert registry.names("recipes") == ["fixture"]
    recipe = registry.load("recipes", "fixture")
    assert recipe.spec.id == "fixture-recipe"
    assert registry.recipes() == {"fixture": recipe}


def test_unknown_provider_is_actionable() -> None:
    registry = registry_from_entry_points([_entry("fixture", "fixture_provider:FixtureRecipe")])
    with pytest.raises(ProviderUnavailable) as excinfo:
        registry.load("recipes", "nope")
    assert "nope" in str(excinfo.value)
    assert "fixture" in excinfo.value.hint


def test_broken_entry_point_is_isolated_not_fatal() -> None:
    registry = registry_from_entry_points(
        [
            _entry("good", "fixture_provider:FixtureRecipe"),
            _entry("broken", "fixture_provider:DoesNotExist"),
            _entry("missing", "definitely_not_a_module:Thing"),
        ]
    )
    results = registry.load_all("recipes")
    assert results["good"].ok
    assert not results["broken"].ok and "DoesNotExist" in results["broken"].error
    assert not results["missing"].ok
    # the good one still loads
    assert registry.recipes()["good"].spec.id == "fixture-recipe"
    with pytest.raises(ProviderUnavailable) as excinfo:
        registry.load("recipes", "broken")
    assert "failed to load" in str(excinfo.value)


def test_names_does_not_import_modules() -> None:
    registry = registry_from_entry_points([_entry("ghost", "definitely_not_a_module:Thing")])
    assert registry.names("recipes") == ["ghost"]


def test_providers_can_be_gated_by_configuration(monkeypatch) -> None:
    monkeypatch.setenv("REEL_PROVIDERS_DISABLED", "fixture")
    registry = registry_from_entry_points(
        [_entry("fixture", "fixture_provider:FixtureRecipe")]
    )
    with pytest.raises(ProviderUnavailable):
        registry.load("recipes", "fixture")
    results = registry.load_all("recipes")
    assert "disabled" in results["fixture"].error


def test_group_names_are_prefixed() -> None:
    registry = Registry(entry_points=lambda group: [])
    assert registry.group_name("recipes") == "reelmachine.recipes"
