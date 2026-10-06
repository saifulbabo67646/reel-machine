# ADR-0007: Renderers behind entry points; default install needs no browser and no Node

Status: accepted

## Context

One study reference for doodle renders program animation with HTML/SVG + GSAP in a
browser, and its stroke runtime is a Python package using classic image algorithms. The
engine must not require a browser or Node, but it must not forbid a heavyweight renderer
either.

## Decision

- Renderers are providers (`reelmachine.renderers`). The core ships the ffmpeg composer;
  doodle ships a stroke renderer and a program renderer.
- The stroke route works with classic image algorithms (no browser, no GPU); its optional
  dependencies live in the `[stroke]` extra.
- The program renderer is deterministic and Pillow-based, in the `[program]` extra. A
  Node/browser renderer may be registered through the same entry point later; nothing in
  the core, the default install, or the stroke route requires it.
- Extras: `[mcp]`, `[stroke]`, `[program]`, `[s3]`, `[all]`. The default install is
  local-first and works without any of them.

## Consequences

- CI runs the full suite with extras, plus a bare-install job that proves the default
  install passes with skips.
- A test renders stroke mode in a subprocess with `node` removed from `PATH` and import
  hooks blocking browser automation packages.
