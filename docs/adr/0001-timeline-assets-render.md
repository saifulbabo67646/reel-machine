# ADR-0001: A reel is a Timeline plus Assets; rendering is a separate step

Status: accepted

## Context

The old pipeline fused search, alignment, cutting, subtitle layout and burning into one
function (`reel.build_plan` + `reel.render`). Nothing could be reused or composed, and a
new kind of reel meant a new pipeline.

## Decision

- A **Timeline** is serializable data: duration, aspect, render mode, and tracks of video
  elements, audio elements, captions and overlays.
- **Assets** are content-addressed files on disk with provenance and licence metadata.
- **Rendering** is a function of `(timeline, assets, style)` producing a video file.
- Composition emits declarative video elements (`source_cut`, `background`,
  `image_sequence`, `stroke_scene`, `program_scene`); **media prep** materialises them into
  playable `clip` assets; the renderer refuses a timeline containing unprepared elements.

## Consequences

- Recipes share the timeline model and the render path; a new category is a new package.
- Every intermediate is resumable and cacheable by content hash.
- The render mode is declared data, so a static pan can never be reported as stroke
  animation.
