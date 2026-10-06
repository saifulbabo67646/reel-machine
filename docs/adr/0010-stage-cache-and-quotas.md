# ADR-0010: Content-hash stage caching, resumable jobs, per-caller quota attribution

Status: accepted

## Context

A render spends real money and time: Nadeshiko quota (search + reference clips), cloud TTS
characters, bandwidth. Re-running a job that already did most of the work must not repeat
those spends. A multi-tenant server must attribute every spend to the caller that caused
it.

## Decision

- Stage cache key = `sha256(stage.id | stage.revision | json(input payload) |
  config-subset | provider pins)`. A hit reloads the persisted output instead of running
  the stage; a job restarted after an interruption resumes from its cached stages.
- Quota ledgers are per caller (`jobs/<caller>/quota/<provider>.json`) and per provider
  (Nadeshiko requests, TTS characters). Every spend is recorded against the caller that
  caused it.
- A job records which caller spent what in its manifest, and quota exhaustion is refused
  up front with a structured `QUOTA_EXCEEDED` (or raised mid-run and attributed to the
  stage that caused it).
- Per-caller concurrency and disk limits are enforced at submit and between stages.

## Consequences

- Re-rendering looks and feel changes cost no API quota, as before — now enforced across
  recipes.
- Quota accounting is auditable from the job store alone.
