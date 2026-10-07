# ADR-0012: Subtitle acquisition and the story brain are provider groups

Status: accepted

## Context

`storyreel` needs two things no earlier recipe needed:

1. **The film's script.** vidvault downloads the film but ships no subtitles, and the
   creative steps read the subtitle text. Sources differ in coverage, cost and — crucially
   — timing: a database subtitle was authored against *some other* release, while a
   subtitle embedded in our own download matches it exactly. The choice must be
   configurable per deployment (keys, quotas, disabled providers), and every source's
   provenance must reach the manifest.

2. **The creative work.** The workflow (top-5 concepts, storytelling script, micro-clip
   plan, SEO) can be done by the calling agent or by a model the deployment serves. A
   second recipe needing an LLM makes "the model does X" a shared capability rather than
   doodle's private concern.

## Decision

Two new entry-point groups, both with their protocols in the recipe package (ADR-0011):

- `reelmachine.subtitles` — providers implement `fetch(SubtitleRequest) -> SubtitleFetch`.
  Shipped: `embedded` (extract the text subtitle stream from the downloaded film — keyless,
  and exactly synced to the footage the micro-clips come from), `subdl` and
  `opensubtitles` (TMDB-native databases), `auto` (the chain, skipping providers whose
  `missing()` is non-empty and recording every attempt), and `fake` (tests).
- `reelmachine.story` — providers implement `concepts(...)` and `script(...)`. Shipped:
  `llm` (any OpenAI-compatible `/chat/completions` endpoint; concepts are found
  map-reduce across scene batches, so a two-hour film never needs one giant prompt) and
  `fake` (deterministic, for tests and offline runs).

Two supporting decisions:

- **Caller-supplied creative work always wins**, exactly as a caller's script wins in
  doodle. `concepts` and `story` inputs are used as given; the `story` group only runs
  when they are absent. That is what lets an agent be the brain over MCP.
- **`mode` is data, not a different recipe**: `transcript` halts after the readable
  script, `plan` after the five concepts, `build` goes through the render. `halt_pipeline`
  was already the engine's mechanism; the concepts stage's cache key deliberately excludes
  `mode`, so the build job after a plan job reuses the very concepts the viewer chose
  from.

## Consequences

- A deployment can pick its sources with `REEL_SUBTITLES_ORDER` and fall back cleanly;
  `reel doctor` reports each provider's unmet requirement.
- Timing risk from downloaded subtitles is explicit: `synced` is recorded on the output,
  verification warns when it is false, and `subtitle_offset_ms` / an ffsubsync-on-PATH
  pass are the fixes.
- `NarrationProvider` is now used by two recipes in practice; its protocol still lives
  in doodle's package. Graduating it to core is deliberate follow-up work, not part of
  this change — storyreel consumes it through the registry and its own tiny
  `provider:voice` helper, so nothing in core needs to move for this to work.
- vidvault's rendition audio preference became a parameter (`audio_pref`): `None` keeps
  nadeshiko's Japanese-only rule byte-for-byte, `[]` accepts anything (storyreel mutes the
  film), and a tag list prefers a language while still probing untagged renditions.
