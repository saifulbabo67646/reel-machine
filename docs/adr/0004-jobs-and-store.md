# ADR-0004: Durable jobs on a local filesystem store; no database or queue

Status: accepted

## Context

The engine's primary client is becoming an agent, over MCP. Agents call tools whose
timeouts are seconds, while a render takes minutes. Jobs must therefore be addressable,
pollable, cancellable and durable — without pulling a database or a worker fleet into a
local-first product.

## Decision

- A job request is plain serializable data: recipe id, inputs, caller, idempotency key,
  config overrides.
- A job is a file: `jobs/<caller>/<job_id>/job.json`, written atomically, with its work
  directory beside it (`stages/`, `assets/`, `artifacts/`).
- The `JobStore` is an interface; the default implementation is local filesystem. No
  database-backed implementation is built.
- Execution is a bounded in-process thread pool. Cancellation is cooperative between
  stages and terminates registered subprocesses. Heartbeats detect jobs interrupted by a
  dead process; because stages are content-cached, resubmitting the same idempotency key
  resumes cheaply.
- Idempotency keys are scoped per caller; a duplicate key returns the existing job.

## Consequences

- One process serves many callers, and a restart does not lose job history.
- Scaling out is deliberately out of scope; the store interface is the seam if that
  changes.
