# ADR-0011: An interface used by one recipe lives in that recipe's package

Status: accepted

## Context

The brief requires every interface to be justified by at least two of the three real
recipes, and says that an interface only one recipe needs belongs in that recipe's package.
The brief also lists synthesis providers (TTS, scene writer, rasteriser, stroke renderer,
program-animation renderer, background/music) as providers. These pull in opposite
directions: today, TTS, scene writing and the doodle renderers are only used by `doodle`.

## Decision

- Interfaces used by ≥2 recipes live in core: `SourceProvider`, `Storage`, `Renderer`
  dispatch, `StylePack`, and the Timeline/Assets/Jobs models.
- Interfaces used by exactly one recipe live in that recipe's package, while their entry
  point group is shared infrastructure:
  - `NarrationProvider` (TTS) and the scene writer, rasteriser, stroke and program
    renderers live in `reelmachine/recipes/doodle/` and register under
    `reelmachine.narration` / `reelmachine.renderers`.
  - The Quran corpus protocol lives in `reelmachine/recipes/quranic/corpora/`.
- If a second recipe needs one of those, the protocol graduates to core; the ADR is updated
  with the reasoning.

## Consequences

- No speculative core abstractions: the core surface stays the smallest thing the three
  recipes actually share.
- A third-party TTS provider implements doodle's protocol today; the protocol is versioned
  with the recipe (`RecipeSpec.version`).
