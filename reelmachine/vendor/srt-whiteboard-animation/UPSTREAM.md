# Upstream provenance

- Project: `srt-whiteboard-animation`
- Upstream: https://github.com/geeklee/srt-whiteboard-animation
- Licence: MIT; see `LICENSE` in this directory (kept verbatim).
- Vendored commit: `696a7243c0e6ffb6827676e539c2ca5ebae2bf6b` (2026-07-28, "feat: initial
  release of srt-whiteboard-animation skill").
- Vendored verbatim, unmodified: `scripts/` (the renderer and its helpers) and
  `assets/drawing-hand.png` (the hand used by the stroke renderer).

## Why it is vendored

`doodle`'s stroke mode inks raster line art stroke by stroke with classic image
algorithms — no browser, no GPU, no Node. That runtime is this project's own finding;
re-implementing it would be copying by another name. It is kept byte-identical so its
provenance stays checkable, and adapted through `reelmachine/recipes/doodle/scenes/stroke.py`,
which converts our `SceneSpec` into the annotation schema the renderer reads and invokes
it as a subprocess.

## What was not bundled

Upstream's `README.md`, `SKILL.md`, `agents/` metadata and `examples/` are not bundled:
this package ships one runtime, not a second skill. The third-party notices for the
vendored runtime and for anything shipped as a style pack live in `THIRD_PARTY_NOTICES.md`
at the repository root.

## Updating

Re-vendor the snapshot (files unchanged), update this file with the new commit, and rerun
the stroke tests — do not patch the vendored files in place.
