# ADR-0006: get_artifact returns a signed, expiring URL — never bytes

Status: accepted

## Context

MCP tool results are for reasoning, not for shipping media. Inlining a video into a tool
result would blow up context windows, and a path-only answer leaks the server's filesystem
layout and gives a hosted deployment no way to hand out access safely.

## Decision

`get_artifact` returns metadata plus a URL:

- **streamable-http**: `GET /artifacts/<caller>/<job>/<name>?expires=<ts>&sig=<hmac>` on
  the same ASGI app as the MCP endpoint, signed with `REEL_MCP_SIGNING_KEY` over
  caller/job/name/expiry, with a short expiry (15 minutes by default).
- **stdio**: no HTTP listener exists, so the URL is a `file://` URL; documented in
  `docs/MCP.md` as a local-only transport.

The result always carries `{url, expiresAt, sha256, bytes, mime, kind, provenance,
licence}` so a caller can verify what it fetched.

## Consequences

- No bytes in tool results, ever.
- A hosted deployment must set a stable signing key across restarts; otherwise URLs minted
  before a restart stop verifying, which is safe but noisy.
- Artifact access requires ownership of the job: the route resolves the caller's own job
  directory only.
