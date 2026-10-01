# ADR-0002: Alignment is a nadeshiko-cut strategy, not a pipeline stage

Status: accepted

## Context

Nadeshiko timestamps are offsets into an external source, so a cut must map them onto the
caller's copy. That machinery is specific to recipes that cut real footage: `quranic` has
no source file and no timestamps to map, and `doodle` generates its own media.

## Decision

Alignment is not a core stage. `align.py` keeps its maths and its public functions
untouched; the `nadeshiko-cut` recipe invokes it as a strategy while composing the
timeline (it only ever affects the `source_cut` windows of that recipe). Recipes that never
touch a source file do not see it, cannot import it accidentally through a stage context,
and are tested without it.

## Consequences

- The core engine has no alignment concept, and a test asserts an offline `quranic` render
  with no Nadeshiko key and no source media.
- Drift, confidence and the affine map stay in the plan and manifest, but as
  `nadeshiko-cut` data.
