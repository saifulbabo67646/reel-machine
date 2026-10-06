# ADR-0009: Config resolution order, and every output-affecting value in the manifest

Status: accepted

## Context

Existing deployments depend on `.env` compatibility and the `REEL_*` names. A multi-recipe
engine needs per-recipe and per-job overrides, and a hosted product must be able to explain
why an output came out the way it did.

## Decision

Resolution order, highest first:

1. job request `config` overrides,
2. `REEL_<RECIPE_ID>_<SETTING>` (recipe-namespaced env),
3. `REEL_<SETTING>` (existing names, unchanged),
4. built-in default.

`resolve_config(recipe_id, overrides)` returns a frozen view (`ResolvedConfig`) whose
`.provenance` records the winning layer per setting. Every resolved value that affects
output is written into the manifest with that provenance. `ffmpeg`, `align` and
`subtitles` accept an optional settings argument (default `None` → `get_settings()`) so a
job's configuration reaches them without changing behaviour.

## Consequences

- No silent configuration: the manifest explains every choice.
- Recipe-namespaced overrides let deployments tune one recipe without moving global
  defaults.
