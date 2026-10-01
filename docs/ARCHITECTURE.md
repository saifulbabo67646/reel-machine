# reel-machine architecture

reel-machine is a **reel production engine** with three first-party recipes
(`nadeshiko-cut`, `quranic`, `doodle`), exposed through a thin CLI and an MCP server. This
document is the design of record. Load-bearing decisions are recorded as ADRs in
[`docs/adr/`](adr/).

The previous version of this codebase was one pipeline with Nadeshiko search baked into
it. This document describes the split that makes adding a category a new package plus a
recipe declaration, never a change to the core.

---

## 1. Principles

1. **A reel is a Timeline plus Assets.** Rendering is a separate step that turns
   `(timeline, assets, style)` into a video file. Nothing else is central.
2. **Alignment is not a pipeline stage.** It is the strategy `nadeshiko-cut` uses to build
   the video elements of recipes that cut real footage. Recipes that never touch a source
   file never see it ([ADR-0002](adr/0002-alignment-is-a-strategy.md)).
3. **First-party is not special.** Built-in recipes, sources, renderers and styles register
   through the same entry points as third-party ones ([ADR-0003](adr/0003-entry-point-registries.md)).
4. **Move, don't improve.** `align.py` maths, the ffmpeg filtergraph and the
   subtitle/furigana machinery keep their behaviour; only their position in the
   architecture changes.
5. **Every external dependency is behind an interface with one real implementation and one
   deterministic fake.** Tests never touch the network ([§6](#6-providers-and-registries)).
6. **A job request is plain data.** Jobs are durable, addressable, cancellable, and every
   input that affected the output is recorded in a manifest
   ([§5](#5-jobs-and-the-manifest)).
7. **The smallest abstraction the three recipes actually share.** An interface used by one
   recipe lives in that recipe's package ([ADR-0011](adr/0011-provider-scope.md)).

---

## 2. System shape

```
                    ┌──────────────────────────────────────────────────┐
   CLI (reel …) ───┐ │                  ENGINE (library)                │
                   ├▶│  Engine: list / describe / probe / submit / get /│
   MCP server ─────┘ │          list / cancel / wait / artifact         │
   (stdio + http)    │  Runner: threads, cancel, progress, quotas       │
                     └───────┬───────────────┬──────────────┬──────────┘
                             │               │              │
                     ┌───────▼──────┐ ┌──────▼──────┐ ┌─────▼────────┐
                     │   RECIPES    │ │  PROVIDERS  │ │  JOB STORE  │
                     │ (entry pts)  │ │ (registries)│ │  / STORAGE  │
                     └───────┬──────┘ └──────┬──────┘ └─────┬────────┘
             nadeshiko-cut ·  quranic · doodle              │
                                                  jobs/<caller>/<job>/
                             │
                     ┌───────▼─────────────────────────────────────────┐
                     │ TIMELINE + ASSETS → RENDER → video + MANIFEST   │
                     └─────────────────────────────────────────────────┘
```

The engine is a library (`reelmachine.engine`) and must not import CLI or MCP concerns.
The CLI and the MCP server are both thin clients of the same API.

### Package layout

```
reelmachine/
  config.py                 env/`.env` settings + per-recipe/per-job resolution
  core/                     models and protocols (no I/O policy)
    errors.py  assets.py  timeline.py  style.py  recipe.py  stage.py
    job.py  manifest.py  registry.py
  engine/                   Engine facade · stage runner · executor (jobs, limits, manifest)
  jobs/                     JobStore protocol · LocalJobStore · InMemoryJobStore
  storage/                  Storage protocol · LocalStorage · S3Storage ([s3]) · InMemoryStorage
  render/                   the plain/karaoke caption writer · shared audio profiles
  observability.py          structured logs + secret redaction
  sources/                  local / hls / vidvault / mock (ABC unchanged)
  recipes/
    nadeshiko_cut/          select · acquire · compose (alignment strategy) · prep · render
    quranic/                corpora (quran_com · alquran_cloud · fake) · timings · compose
    doodle/                 script · narration (cloud · fake) · scenes (stroke · program)
  mcp/                      server · tools · auth · tenants · artifacts
  cli.py                    thin client
  data/                     jlpt_vocab.json · styles/*.json
  vendor/srt-whiteboard-animation/   MIT runtime, verbatim (ADR-0008)
```

---

## 3. Core models

### 3.1 Timeline and elements (`core/timeline.py`)

```python
class Timeline(BaseModel):
    duration_ms: int
    aspect: Literal["vertical", "square", "landscape"] = "vertical"
    render_mode: str            # "cut" | "quranic" | "stroke" | "program" — declared
    style: str                  # StylePack id
    video: list[VideoElement]
    audio: list[AudioElement]
    captions: list[Caption]
    overlays: list[Overlay]
```

Video elements are a discriminated union on `kind`:

| kind | fields | produced by | consumed by |
|---|---|---|---|
| `source_cut` | asset, src_in_ms, src_out_ms, start_ms | compose (recipes that cut footage) | **prep** (→ `clip`) |
| `background` | spec (gradient/file/asset), start_ms, duration_ms | compose (quranic, doodle) | prep (→ `clip`) |
| `image_sequence` | frames_dir, fps, start_ms, duration_ms | compose | prep or render |
| `generated_scene` | renderer name + spec + source assets | any recipe that wants the engine's renderer dispatch | prep (→ `clip`) via the named renderer |
| `clip` | asset, start_ms, duration_ms, origin | **prep** | render |

doodle's scenes are recipe-local `SceneSpec`s (regions + reveal schedule for stroke,
typed elements for program) that prep materialises into `clip`s through its renderer
providers; `GeneratedScene` is the core form for a recipe that would rather declare a
scene and let the engine dispatch it.

Composition emits *declarative* elements; **media prep materialises every non-`clip`
video element into a `clip`** (a content-addressed asset). The render stage refuses a
timeline that still contains unprepared elements, so "what got rendered" is always
explicit.

Audio elements: `timed_audio` (asset + placement: narration, recitation),
`music`, `room_tone`. Captions carry `text`, `lang`, `style_ref` and optional word
spans for karaoke highlighting (Quranic word-by-word). Overlays carry images, SVG or
text with an anchor.

### 3.2 Assets (`core/assets.py`)

An `Asset` is a content-addressed file: `id = sha256(content)[:32]`, stored as
`<store>/<id[:2]>/<id><ext>` with a JSON sidecar carrying kind, size, mime,
`Provenance` (provider, provider_version, source, source_id, upstream) and `Licence`
(name, url, attribution). Storing is idempotent. The manifest carries every asset's
provenance and licence, so a hosted product can answer "where did this come from".

### 3.3 Style packs (`core/style.py`)

A `StylePack` is a named preset (palette, fonts, aspect defaults, stroke/scene
parameters) with mandatory licence and provenance metadata. Packs are declarative JSON
in `data/styles/` or discovered through `reelmachine.styles`. Neutral defaults only:
third-party visual IP may be added by a style pack, never hard-coded in core.

---

## 4. Recipes, stages, providers

### 4.1 Recipe declaration (`core/recipe.py`)

```python
@dataclass(frozen=True)
class RecipeSpec:
    id: str; version: int; title: str; summary: str
    input_model: type[BaseModel]              # JSON Schema for CLI and MCP
    stages: tuple[StageSpec, ...]             # ordered
    providers: Mapping[str, ProviderReq]      # role -> (group, default, required)
    cost: CostNote                            # unit, estimate, notes, providers
    artifacts: tuple[str, ...]                # stable artifact names
    render_modes: tuple[str, ...]
    example: Mapping[str, Any]
    schema_version: int = 1

class Recipe(Protocol):
    spec: RecipeSpec
    def stages(self) -> Sequence[Stage]
    def probe(self, inputs, ctx) -> ProbeReport        # validate, no quota, no render
    def verify(self, ctx, result) -> VerificationReport | None
```

### 4.2 Stages (`core/stage.py`)

```python
class Stage(Protocol):
    id: str                     # "select", "nadeshiko.compose", "doodle.scenes", …
    revision: int = 1           # bump invalidates the cache
    def run(self, ctx: StageContext, payload: Any) -> Any: ...
```

A `StageContext` carries the job, the caller, resolved config, the stage work directory,
the asset store, resolved providers (`ctx.providers.get("corpus")`), a progress
reporter, a cancel token, a structured logger, and the caller's quota ledger.

**Resumability and caching.** Each stage writes its output to
`stages/<nn>-<id>/output.json` plus assets. A stage is cacheable by content hash:

```
key = sha256(stage.id | stage.revision | json(payload) | config-subset | provider pins)
```

A cached stage is skipped and its output reloaded, which makes a re-run of the same job
resume instead of repeating work — quota is spent once ([ADR-0010](adr/0010-stage-cache-and-quotas.md)).

### 4.3 Providers (`core/registry.py`)

Entry-point groups:

| group | contract | built-ins |
|---|---|---|
| `reelmachine.recipes` | `core.recipe.Recipe` | nadeshiko-cut, quranic, doodle |
| `reelmachine.sources` | `sources.base.SourceProvider` | local, hls, vidvault, mock |
| `reelmachine.corpora` | per-recipe corpus protocols | nadeshiko, quran_com, alquran_cloud (+ fakes) |
| `reelmachine.narration` | doodle's `NarrationProvider` (TTS → TimedSpeech) | fake, ElevenLabs, Cartesia |
| `reelmachine.scripts` | doodle's `ScriptProvider` (topic → beats) | template (deterministic), llm (OpenAI-compatible) |
| `reelmachine.renderers` | a scene renderer (`render(scene, ...) -> Path`) | stroke (whiteboard runtime), program (Pillow) |
| `reelmachine.backgrounds` | `quranic.backgrounds.BackgroundProvider` (a clip + its provenance and licence) | gradient, pexels, fake |
| `reelmachine.styles` | `core.style.StylePack` | quranic/*, doodle/* |
| `reelmachine.storage` | `storage.Storage` | local, s3 ([s3]), memory |

Discovery takes an **injectable `entry_points()` provider**, which is how the test suite
proves a third-party package can add a recipe with no core change. Loading is lazy and
failure-isolated: a provider whose module can't import is reported as unavailable
(`doctor`) and only recipes that require it fail (`PROVIDER_UNAVAILABLE` with a hint).
Every provider declares `requires()` / `describe()` so a deployment can gate it
(`REEL_PROVIDERS_DISABLED`, [ADR-0011](adr/0011-provider-scope.md)).

---

## 5. Jobs and the manifest

### 5.1 Job model (`core/job.py`)

```python
class JobRequest(BaseModel):
    recipe: str; inputs: dict; idempotency_key: str | None = None; config: dict = {}

class Job(BaseModel):
    id; recipe; recipe_version; caller; state; created_ms; started_ms; finished_ms
    progress: Progress          # stage, stage_index, stages_total, percent, detail
    request: JobRequest; result: JobResult | None; error: JobError | None

class JobError(BaseModel):
    code; message; hint; stage; details      # actionable, never a stack trace
```

Codes: `INVALID_INPUT`, `PROVIDER_UNAVAILABLE`, `QUOTA_EXCEEDED`, `CONCURRENCY_LIMIT`,
`DISK_LIMIT`, `VOICE_UNAVAILABLE`, `SOURCE_UNRESOLVED`, `ALIGNMENT_FAILED`,
`RENDER_FAILED`, `PREFLIGHT_FAILED`, `VERIFICATION_FAILED`, `INTERRUPTED`, `CANCELLED`,
`INTERNAL`.

A `JobStore` (local filesystem by default, `jobs/<caller>/<job>/`; no database,
[ADR-0004](adr/0004-jobs-and-store.md)) provides create/save/get/list, idempotency-key
lookup, and the job work directory. A duplicate idempotency key returns the existing job.
`JobRunner` executes jobs on a bounded thread pool; cancellation is cooperative between
stages and kills registered subprocesses; per-caller concurrency and disk limits are
enforced at submit and between stages ([ADR-0010](adr/0010-stage-cache-and-quotas.md)).
Heartbeats make an interrupted `running` job detectable as `INTERRUPTED` after a restart.

### 5.2 Manifest (`core/manifest.py`)

```python
class Manifest(BaseModel):
    schema_version; job_id; recipe; recipe_version; created; caller
    inputs: dict                       # validated recipe inputs
    config: dict[str, ConfigValue]     # resolved value + provenance
    render_mode: str                   # declared by the renderer, never inferred
    timeline: dict                     # digest + artifact
    assets: list[AssetRecord]          # sha256, kind, provenance, licence
    stages: list[StageRecord]          # timing, cache hit, output hashes, provider pins
    providers: dict[str, ProviderPin]  # id, version, model/voice ids
    quota: dict[str, QuotaSpend]       # provider -> units, spent_by_caller
    verification: VerificationReport | None
    environment: dict                  # reel-machine, python, ffmpeg versions
```

For a finished job the manifest is sufficient to explain and reproduce the output:
resolved inputs and config (with where each value came from), the exact providers and
models/voices, every asset's provenance and licence, the render mode, and the verification
report.

---

## 6. Configuration

Existing `.env` and `REEL_*` names keep working. Resolution order is
**job config > `REEL_<RECIPE>_<SETTING>` > `REEL_<SETTING>` > default**; every resolved
value that affects output lands in the manifest with its provenance
([ADR-0009](adr/0009-config-resolution.md)). `resolve_config(recipe_id, overrides)`
returns a frozen view with `.provenance`.

`ffmpeg`, `align` and `subtitles` accept an optional `settings`/`cfg` argument (default
`None` → `get_settings()`) so per-job configuration reaches them without changing any
behaviour.

---

## 7. MCP server

One server, two transports (`stdio`, `streamable-http`), SDK v2 (`mcp` optional extra).
Exactly these tools: `list_recipes`, `describe_recipe`, `probe`, `start_job`, `get_job`,
`list_jobs`, `cancel_job`, `get_artifact`. No resources, prompts or sampling.

- `start_job` validates, enforces quotas, enqueues and returns an id in milliseconds;
  callers poll `get_job`. No tool call ever waits for a render.
- Every error is structured (`code`, `message`, `hint`, `details`), never a traceback.
- Tool descriptions are written for a model, each with exactly one example; results carry
  a `schemaVersion`, additive-only within a major version.
- **Multi-tenant from the first commit**: per-caller bearer token in the `Authorization`
  header (raw tokens are never stored — only sha256 in the tenants file), per-caller job
  isolation, quotas, concurrency and disk limits, and job records that name their caller.
- Quota ledgers (Nadeshiko rate limit/monthly quota, cloud TTS characters) are per caller;
  a job records which caller spent what.
- `get_artifact` returns a signed, expiring URL and metadata; **never bytes**. HTTP serves
  `/artifacts/<caller>/<job>/<name>?expires=&sig=`; stdio returns a `file://` URL
  ([ADR-0006](adr/0006-artifact-delivery.md)).
- No secret appears in a tool result, a manifest or a log line; a sentinel test sweeps the
  job store, the logs and every tool result.

---

## 8. Recipes

### 8.1 `nadeshiko-cut`

One recipe parameterised by `corpus` (`anime`, `jdrama`, `anime+jdrama`, `youtube`);
the old CLI spelling is preserved. Stages: `select` → `acquire` → `compose` (alignment
strategy; `mode=plan` stops here with `plan.json`) → `prep` (cut clips) → `render`
(existing one-pass ffmpeg filtergraph + ASS) → `manifest`. A regression test asserts both
corpora execute the identical recipe and stage code with only the filter parameter
differing.

### 8.2 `quranic`

No source media, no Nadeshiko, no alignment. Inputs: Surah/Ayah range, reciter,
translation language, style preset, background choice, audio mastering profile. A
background is `gradient` (procedural, the default, no key), `file` (the caller's own
clip) or `pexels` (stock; needs `PEXELS_API_KEY` — the deployment may simply not have
one, in which case the probe says so and nothing silently substitutes). `backgrounds`
takes a whole list instead: with no scopes the reel is split evenly across the clips;
with `ayah_start`/`ayah_end` on every entry each clip starts and ends exactly where its
ayahs do, so a caller can match a verse's meaning to what is behind it (a clip is
preferred, a still is the fallback and gets a slow push-in). The parts are joined into
one continuous background with a crossfade at each boundary (`transition_ms`, default
600; 0 is a hard cut), each keeping its own provenance, licence and switch window — all
of it in the manifest, Pexels attributions included.

Two gates keep people out of a verse's backdrop, because a reel is not a place to put a
stranger behind the words: candidates whose own description (`alt`, page slug) reads
like a person are skipped, and when a face model is configured (`REEL_FACE_MODEL`) the
downloaded media is sampled and refused if a face is visible — the next candidate is
tried, and the search is only abandoned with the reason named. Both are recorded per
clip (`faceCheck`: passed / blocked / skipped / unavailable), so nothing is claimed that
did not run. `probe` hands the caller the verses (text, words,
translation), which is what makes choosing by meaning possible without a second lookup. Two pluggable corpora: Quran Foundation v4 (text,
translations, audio, word-level millisecond timings) and alquran.cloud text + mp3quran
audio with deterministic proportional timings. The timeline is recitation audio +
word-level highlight captions + translation overlays + background; rendering is
background + karaoke ASS + loudness profile.

### 8.3 `doodle`

Hand-drawn explainer reels, one recipe parameterised by `mode`. A caller's own `script`
is used exactly as given — the path a calling agent takes, and the one to prefer; a bare
`topic` goes through the `scripts` provider group (deterministic `template`, or `llm` when
a deployment would rather a model wrote the beats). Length follows the script: one beat,
one narration line, nothing else caps it.

- `stroke` — whiteboard inking: raster line art is inked stroke by stroke on a continuous
  canvas; semantic regions are revealed in the order the narration reaches them; scene
  structures are declarative (single, multi-act, dual semantic islands). The picture track
  comes from the vendored MIT runtime (`vendor/srt-whiteboard-animation`, OpenCV/Pillow/PyAV
  — no browser, no GPU, no Node); our adapter converts a `SceneSpec` into its annotation
  schema and gates on its validator. Deps in `[stroke]`.
- `program` — knowledge graphics: deterministic typed elements (cards, arrows, labels,
  figures, precise text) rendered to frames with Pillow; titles, numbers and relationships
  are generated in code, never by a generative step ([ADR-0007](adr/0007-renderers-and-extras.md)).
  Deps in `[program]`; a Node/browser renderer can be added later through the renderer
  entry point, never required.

Quality rules encoded as tests: the manifest declares the render mode; the reveal schedule
is driven by narration timings, not even division (a cloud voice that reports only
line-level timings — Cartesia's HTTP route — gets each beat's real audio duration, with
words spread proportionally inside it and `timingsSource` recorded); a missing/unlicensed voice fails
loudly or degrades only via an explicit `allow_visible_fallback` (recorded in the
manifest); preflight renders one representative scene before the full job; the finished
job is verified by inspecting the real file (streams, caption sync, scene boundaries,
final frame).

---

## 9. Extension points

- **Recipe**: implement the protocol, declare a `RecipeSpec`, register a
  `reelmachine.recipes` entry point. See `docs/RECIPES.md`.
- **Provider**: sources, corpora, narration, renderers, backgrounds, styles, storage — same pattern.
- **Style pack**: a JSON pack with licence and provenance, shipped as data or via the
  styles entry point.
- **Publishing/upload**: deliberately not built. A future publishing stage is an extension
  point that consumes finished artifacts from the job store.

## 10. Non-goals

No web UI or desktop app; no publishing/upload stage; no cloud services required by
default; no generated-image service in core; no browser or Node requirement; no rewrite of
`align.py`, the ffmpeg filtergraph or the subtitle machinery; no new video source
providers; no distributed queue, worker fleet or database; no MCP resources, prompts or
sampling.

## 11. Migration summary

The refactor was executed in stages, each leaving the tree green: (0) branch and baseline;
(1) design doc, ADRs and core models; (2) Timeline/Job/Asset extraction and the
`nadeshiko-cut` recipe with the CLI re-pointed; (3) provider registries and entry points;
(4) engine API, job store and runner; (5) MCP server; (6) `quranic`; (7) `doodle`;
(8) docs and CI. `anime`/`jdrama` output behaviour is preserved and verified by the
existing test suite plus `reel doctor --selftest`, which now also covers a non-media
recipe.
