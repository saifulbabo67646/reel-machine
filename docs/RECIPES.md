# Writing your own recipe, provider or style pack

Adding a category is **a new package plus a recipe declaration, never a change to the
core**. Built-in recipes register through the same entry points as yours; there is no
first-party registry table.

## A recipe in 60 lines

```python
# mypackage/my_recipe.py
from pydantic import BaseModel, ConfigDict
from reelmachine.core.recipe import CostNote, ProbeReport, ProviderReq, RecipeSpec, StageSpec
from reelmachine.core.stage import StageContext, StageOutput


class MyInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    count: int = 3


class MyOutput(StageOutput):
    headline: str = ""


class HeadlineStage:
    id = "headline"          # stable id: it is the stage cache key
    revision = 1             # bump to invalidate cached outputs
    cacheable = True         # cache by content hash (payload + config + providers)
    cache_fields = ("title", "count")   # which inputs the cache key covers
    output_model = MyOutput

    def run(self, ctx: StageContext, payload: dict) -> MyOutput:
        inputs = MyInputs.model_validate(payload)      # payload is the job request
        ctx.cancel.raise_if_cancelled()                # cooperative cancellation
        ctx.progress.detail(f"  · working on {inputs.title}")
        text = " ".join([inputs.title.upper()] * inputs.count)
        (ctx.workdir / "headline.txt").write_text(text)         # stage scratch space
        asset = ctx.assets.put(                                  # content-addressed, with provenance
            ctx.workdir / "headline.txt",
            kind="report",                                       # any AssetKind member
            provenance=Provenance(provider="mypackage"),
        )
        return MyOutput(headline=text)


class MyRecipe:
    spec = RecipeSpec(
        id="headline",
        title="Headline reel",
        summary="Repeats a title, for demonstration.",
        input_model=MyInputs,                 # JSON Schema comes from here
        stages=(StageSpec(stage=HeadlineStage()),),
        providers={},                          # role -> ProviderReq(group=..., default=...)
        cost=CostNote(unit="none", estimate="0"),
        artifacts=("manifest.json",),
        render_modes=(),
        example={"title": "hello", "count": 2},
    )

    def probe(self, inputs: MyInputs, ctx) -> ProbeReport:      # no quota, no render
        return ProbeReport(ok=bool(inputs.title), details={"title": inputs.title})

    def verify(self, ctx: StageContext, result: dict):          # optional; inspects the real output
        return None
```

```toml
# mypackage/pyproject.toml
[project.entry-points."reelmachine.recipes"]
headline = "mypackage.my_recipe:MyRecipe"
```

```bash
pip install -e mypackage
reel recipes            # your recipe is listed
```

## Stages

- A stage declares `id`, `revision`, `cacheable`, optional `cache_fields`, and an
  `output_model` (a pydantic model — stage outputs are persisted as JSON under the job's
  work directory, which is what makes a job resumable).
- `payload` is the previous stage's output, or the validated job inputs for the first
  stage (`StageSpec(feeds=None)`). Return a `StageOutput` subclass; set
  `halt_pipeline=True` to stop the run (that is how a plan-only mode stops before media
  prep).
- `ctx` gives you the job, resolved config, providers, the asset store, a work
  directory, progress, a cancel token, a redacting logger and the caller's quota
  account. Everything a stage writes is either an asset (content-addressed) or an
  artifact under its work directory.

## Providers

Every provider declares `name`, `missing()` (unmet requirements, for `reel doctor` and
deployment gating) and `describe()`. Register by group:

| group | used by | notes |
|---|---|---|
| `reelmachine.sources` | `nadeshiko-cut`, `storyreel` | `resolve(media, episode) -> EpisodeAsset`, `probe`, `reference_clips` |
| `reelmachine.corpora` | `nadeshiko-cut` (`nadeshiko`), `quranic` (`quran_com`, `alquran_cloud`, `quran-fake`) | per-recipe protocols |
| `reelmachine.narration` | `doodle`, `storyreel` | `synthesize(text, voice=...) -> TimedSpeech` with word timings |
| `reelmachine.subtitles` | `storyreel` | `fetch(request) -> SubtitleFetch` — the film's script: embedded, SubDL, OpenSubtitles, or a chain |
| `reelmachine.story` | `storyreel` | the storytelling brain: `concepts(...)`, `script(...)`; caller-supplied output always wins |
| `reelmachine.renderers` | `doodle` | `render(scene, ...)` for `stroke` and `program` |
| `reelmachine.storage` | engine | `local`, `memory`, S3-compatible `s3` |
| `reelmachine.styles` | all recipes | a `StylePack`, a mapping of them, or a callable returning either |

An interface used by exactly one recipe belongs in that recipe's package; when a second
recipe needs it, it graduates to core (ADR-0011). Providers must not import heavy or
optional dependencies at module scope — a missing dependency is reported as an
unavailable provider, not an import error.

## Style packs

A style pack is a JSON file (or an entry point) with a palette, fonts and parameters,
plus **mandatory** licence and provenance metadata:

```json
{
  "id": "mystyle",
  "title": "My style",
  "render_modes": ["doodle"],
  "palette": {"ink": "#101010", "paper": "#ffffff"},
  "fonts": {"caption": "Helvetica"},
  "params": {},
  "licence": {"name": "CC-BY-4.0", "url": "…", "attribution": "…"},
  "provenance": {"provider": "me", "source": "…"}
}
```

Third-party visual IP may arrive this way and only this way; it is never hard-coded in
core. Anything you ship is your licence obligation — see `THIRD_PARTY_NOTICES.md`.

## Test your recipe without a network

Inject providers through the engine; every interface has a deterministic fake:

```python
engine = Engine(
    settings,
    registry=registry_with_your_entry_point,
    provider_overrides={"corpus": "my-fake", "narration": "fake"},
)
job = engine.submit(JobRequest(recipe="headline", inputs={"title": "hi"}), caller="local")
finished = engine.wait(job.id, caller="local")
assert finished.state.value == "succeeded"
assert finished.result.render_mode == "…"
```

A recipe that renders must carry its render mode into the timeline
(`Timeline.render_mode`); the manifest and the verifier take it from there, so a static
pan can never be reported as stroke animation.
