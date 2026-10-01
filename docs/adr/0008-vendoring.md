# ADR-0008: Vendor the MIT whiteboard runtime; nothing else is copied

Status: accepted

## Context

The doodle study reference (`hand-drawn-explainer-video-nikola`, Apache-2.0) bundles a
stroke-animation runtime, `srt-whiteboard-animation`, under MIT. The brief permits
vendoring exactly that one runtime with upstream notices; nothing else from the reference
may be copied. Its workflow, quality bar and production discipline are study material to
re-express in our own interfaces.

## Decision

- Vendor `vendor/srt-whiteboard-animation/` **verbatim**, including its `LICENSE` and an
  `UPSTREAM.md` recording the upstream project, commit and local modifications (none).
- Adapt it through our own module: our `SceneSpec` is converted to the runtime's
  annotation schema, its validator gates the render, and its renderer produces the picture
  track as a subprocess.
- `THIRD_PARTY_NOTICES.md` records the vendored project, its licence, and the notices for
  anything shipped as a style pack.
- Third-party visual IP (for example the reference's "small black character" style) is
  never hard-coded: it may only arrive through a style pack that declares its licence and
  provenance.

## Consequences

- `package-data` includes the vendored tree so wheels ship it.
- Updating the runtime means re-vendoring the snapshot and updating `UPSTREAM.md`, not
  patching it in place.
