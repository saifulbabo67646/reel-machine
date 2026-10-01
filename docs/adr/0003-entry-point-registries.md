# ADR-0003: Entry-point registries; first-party is not special

Status: accepted

## Context

Adding a category must be "a new package plus a recipe declaration, never a change to the
core". The old code selected providers with a hard-coded `if/elif` chain.

## Decision

Recipes, sources, corpora, narration, renderers, styles and storage are discovered through
distribution entry points (`reelmachine.recipes`, `reelmachine.sources`, …). Built-in
providers register through the same mechanism as third-party ones; the core has no
first-party registry entries beyond the entry-point table in `pyproject.toml`.

Discovery is injectable (`entry_points()` argument) so tests can prove third-party
registration without installing a distribution, and so the real path and the test path
exercise the same lookup code. Loading is lazy and failure-isolated: a broken entry point
is reported as unavailable instead of breaking the registry, and only recipes that require
it fail, with an actionable error.

## Consequences

- `list_recipes` is data from entry points; installing a package adds a recipe with no
  core edit.
- Provider modules must not import heavy or optional dependencies at module scope.
