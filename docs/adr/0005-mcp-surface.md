# ADR-0005: MCP surface — two transports, eight tools, versioned schemas

Status: accepted

## Context

The MCP server is the agent-facing product surface. An unbounded tool list invites
ambiguity for the model and drift in the API.

## Decision

- One server, two transports: `stdio` (local agents) and `streamable-http` (hosted).
- Exactly eight tools: `list_recipes`, `describe_recipe`, `probe`, `start_job`, `get_job`,
  `list_jobs`, `cancel_job`, `get_artifact`. No MCP resources, prompts or sampling.
- `start_job` never blocks on a render: it validates, enforces quotas, enqueues and
  returns a job id. Callers poll `get_job`.
- Every tool description is written for a model and carries exactly one example.
- Tool results carry a `schemaVersion`; within a major version, changes are additive only.
- Domain failures are structured results (`code`, `message`, `hint`, `details`), never
  tracebacks; unexpected exceptions are sanitized and logged server-side.

## Consequences

- The MCP dependency is an optional extra; `pip install reel-machine` works without it and
  the engine never imports it.
- Clients written against schema version 1 keep working across additive releases.
