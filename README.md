# reel-machine

A modular reel production engine. A reel is a **Timeline plus Assets**; rendering is a
separate step that turns `(timeline, assets, style)` into a video file. Four recipes ship
with it:

| recipe | what it makes | needs |
|---|---|---|
| `nadeshiko-cut` | anime / J-Drama dialogue reels cut from **your own** copies of episodes, aligned by audio | a Nadeshiko key + a source (your library, your streaming server, or the download route) |
| `quranic` | Surah/Ayah reels: recitation audio, Arabic text with word-level highlight sync, translation overlays, gradient or supplied background | nothing but ffmpeg (two free Quran corpora providers) |
| `doodle` | hand-drawn explainer reels, **stroke** (whiteboard inking) or **program** (knowledge graphics), narrated by TTS with timings | ffmpeg; stroke mode adds `[stroke]`, program mode `[program]`, cloud voices need a key |
| `storyreel` | viral movie storytelling: the whole film is downloaded, its subtitle script read for the moments that hold an audience, one of five story concepts chosen, a voiceover written with a micro-clip plan and SEO, spoken and cut into many short shots | ffmpeg + `REEL_TMDB_API_KEY` (vidvault); SubDL/OpenSubtitles keys widen the subtitle chain; cloud voices need a key |

The engine is a library. The CLI (`reel`) and the MCP server are both thin clients of it;
an agent over MCP is a first-class caller.

```
pip install reel-machine              # local-first; no browser, no Node, no GPU
pip install 'reel-machine[mcp]'       # the MCP server
uv sync                               # from a checkout; installs `reel`
cp .env.example .env                  # then edit
uv run reel doctor --selftest -v      # proves the whole chain with synthetic media
```

Requires **ffmpeg** on `PATH` (built and tested against ffmpeg 8.0). `ffprobe` is used
when present; if your build does not ship one, the tool falls back to `ffmpeg -i` and says
so in `reel doctor`.

---

## Quick start

```bash
uv run reel doctor --selftest -v          # alignment + a non-media recipe, end to end
uv run reel recipes                       # what this installation can make

# anime / J-Drama (spends Nadeshiko quota; see the cost column in `reel recipes`)
uv run reel search 彼女 -n 15
uv run reel plan 彼女 -c 5
uv run reel build 彼女 -c 5 --aspect vertical

# quranic — no source media, no Nadeshiko key
uv run reel build --help                  # (recipe inputs travel as JSON; see docs/)
uv run python -c "..."                    # or use the engine API / MCP server below

# doodle
uv run reel mcp serve --transport stdio   # then start_job {"recipe": "doodle", ...}

# storyreel — a film becomes a vertical storytelling reel
uv run reel discover --search "Inception"                        # pick a title (TMDB)
uv run reel run storyreel -i '{"title": "Inception", "mode": "transcript"}'
uv run reel run storyreel -i '{"title": "Inception", "mode": "plan"}'          # top 5 concepts
uv run reel run storyreel -i '{"title": "Inception", "mode": "build", "concept": "c1"}'
```

Every recipe is driven by the same engine API, so the CLI, a script and an agent use one
code path. The recipes' exact input schemas are in `reel recipes --json` /
`describe_recipe` over MCP. The agent playbook for the storytelling workflow — the
questions to ask, the modes, the exact JSON — is `docs/STORYREEL_AGENT.md`.

## The engine

```python
from reelmachine.engine import Engine
from reelmachine.core.job import JobRequest

engine = Engine()                          # local filesystem job store under .work/jobs
job = engine.submit(                       # returns in milliseconds
    JobRequest(recipe="quranic", inputs={"surah": 1, "ayah_start": 1, "ayah_end": 3}),
    caller="local",
)
finished = engine.wait(job.id, caller="local")
print(finished.state, finished.result.render_mode)
```

A job is durable (`jobs/<caller>/<job>/job.json`, written atomically), addressable,
cancellable, and carries progress and structured errors. Stage outputs are content-hash
cached, so a re-run resumes instead of repeating work — and quota is spent once. When it
finishes, the manifest records every input that affected the output: the resolved
configuration (with where each value came from), the providers and voices, every asset's
provenance and licence, stage timings, the render mode, and the verification result.

Recipes, sources, corpora, narration, renderers, storage and styles are discovered
through **entry points**. Built-in providers register through exactly the same mechanism
as third-party ones, so adding a category is a new package plus a recipe declaration —
never a change to the core. See [docs/RECIPES.md](docs/RECIPES.md).

## MCP

One server, two transports (`stdio`, `streamable-http`) and exactly eight tools:
`list_recipes`, `describe_recipe`, `probe`, `start_job`, `get_job`, `list_jobs`,
`cancel_job`, `get_artifact`. No tool call waits for a render; `start_job` returns a job
id and the caller polls. Per-caller bearer tokens, per-caller jobs, quotas, concurrency
and disk limits; `get_artifact` returns a signed, expiring URL — never bytes. Full
reference, with one example per tool: [docs/MCP.md](docs/MCP.md).

## What `reel doctor --selftest` proves

It builds a synthetic episode, fabricates segments whose timestamps are shifted by a
**known** +2500 ms, and asserts the whole chain — so a green run means the alignment
genuinely measured the offset rather than echoing its input — then renders the `quranic`
recipe with the fake corpus, proving a non-media recipe end to end. The alignment
selftest also checks container-vs-cue drift (<200 ms) and audio/video parity (<250 ms).

## Nadeshiko's timestamps are not relative to your file

`Segment.startTimeMs` is an offset into the *external source video* the episode was
indexed from; your copy is a different release. So `ffmpeg -ss startTimeMs` lands near the
scene, or nowhere at all. This tool **measures** the mapping:

1. Every segment ships with `urls.audioUrl` — a ground-truth clip of exactly that line.
2. Decode your episode and that clip to RMS envelopes (codec/bitrate/volume independent)
   and slide the clip across the episode with FFT normalised cross-correlation.
3. One anchor gives a constant offset; several anchors spread across the episode give an
   **affine** map `local = a·src + b`, so a frame-rate difference shows up as a slope
   rather than silently desyncing the end of the reel.
4. Anchors that disagree (a repeated catchphrase matching twice) are dropped by a
   median-residual filter, and the whole episode is **refused** if the survivors still
   disagree by more than 250 ms — better a failed run than a wrong cut.

This is not a pipeline stage: it is the strategy `nadeshiko-cut` uses while building its
video elements, and recipes that never touch a source file never see it. The maths lives
in `reelmachine/align.py` and is unchanged from the original implementation.

**Cost:** aligning one episode decodes its whole audio track (for HLS, that means fetching
the whole episode once — a 24-minute 1080p episode is roughly 150 MB, cached in
`.work/cache/`). Cutting afterwards re-reads only the seconds it needs.

## Sources (`nadeshiko-cut`)

| provider | what it needs |
|---|---|
| `local` | `REEL_LOCAL_ROOT` + templates (or fuzzy matching) |
| `hls` | `REEL_HLS_TEMPLATE`, opaque ids in `REEL_HLS_MAP`, and a fresh token from `REEL_HLS_RESOLVER_CMD` / `REEL_HLS_TOKEN_CMD` |
| `vidvault` | `REEL_TMDB_API_KEY`; downloads once, then re-cuts for free |
| `mock` | nothing — synthetic media for tests and dry runs |

Deployments that bring their own files or their own streaming server need nothing else.
Every provider declares its unmet requirements and `reel doctor` prints them, so a
deployment can gate one (`REEL_PROVIDERS_DISABLED=hls,vidvault`).

The full provider notes — HLS hosts that disguise segments as `.jpg`, the signed-token
seam, the vidvault payload shapes, the per-connection CDN throttling and the
range-parallel downloader — were the hard-won part of the original README and are
preserved in [docs/SOURCES.md](docs/SOURCES.md).

## CLI

| command | purpose |
|---|---|
| `reel doctor [--selftest]` | check ffmpeg, config, providers, quota; run the end-to-end selftest |
| `reel recipes [--json]` | what this installation can make |
| `reel search <word>` / `words` / `titles` | browse the corpus |
| `reel plan <word>` | search + select + align → `plan.json` (quota only) |
| `reel build <word>` | plan + cut + render |
| `reel mine <id>:<ep>` | every JLPT word inside episodes you already have |
| `reel align` / `reel probe` | debug one episode's mapping / resolve one stream |
| `reel quota` / `reel cache [--clear]` | usage and cache |
| `reel mcp serve` / `reel mcp token-hash` | run the MCP server / hash a caller token |

Useful flags on `build`: `--count`, `--per-media`, `--per-category`, `--category`,
`--aspect vertical|square|original`, `--pre`, `--post`, `--watermark`, `--name`.

## Configuration

`.env` and `REEL_*` names are unchanged. Resolution order is **job config >
`REEL_<RECIPE>_<SETTING>` > `REEL_<SETTING>` > default**, and every resolved value that
affects output lands in the manifest with its provenance. New settings worth knowing:
`REEL_TTS` (`fake` | `elevenlabs` | `cartesia`), `REEL_QURAN_API_BASE`,
`REEL_MCP_TOKEN` / `REEL_MCP_TENANTS`, `REEL_MCP_SIGNING_KEY`, `REEL_LOG_FORMAT=json`.

## Layout

```
reelmachine/
  core/        Timeline + Assets, jobs, recipes, stages, manifests, registry
  engine/      the Engine, the stage runner, the job runner
  jobs/        durable local job store (no database, by design)
  storage/     local / memory / S3-compatible storage behind one interface
  sources/     local · hls · vidvault · mock          (nadeshiko-cut)
  corpora/     nadeshiko                                (nadeshiko-cut)
  recipes/
    nadeshiko_cut/  select → acquire → compose (alignment) → prep → render
    quranic/        corpora (Quran Foundation · alquran.cloud · fake) → timeline
    doodle/         script → narration → scenes → prep → compose → render
    storyreel/      acquire (vidvault) → subtitles → transcript → concepts → script
                    → voiceover → prep → compose → render
  render/      the plain/karaoke ASS writer and the audio profiles
  mcp/         the MCP server (optional extra)
  align.py ffmpeg.py subtitles.py vocab.py …   the media machinery, moved not rewritten
  vendor/      the MIT whiteboard runtime (notices in THIRD_PARTY_NOTICES.md)
```

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — the design of record
- [docs/MCP.md](docs/MCP.md) — the agent interface, one example per tool
- [docs/RECIPES.md](docs/RECIPES.md) — write your own recipe, provider or style pack
- [docs/adr/](docs/adr/) — the load-bearing decisions
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — vendored code and licences

## Gotchas already handled

These cost real debugging time; they are encoded in the code and guarded by tests so they
cannot silently come back.

| symptom | cause | where |
|---|---|---|
| Subtitles drift later through the reel | concat binds an audio pad to the wrong stream when inputs are referenced by a bare label | `ffmpeg.build_reel_video` routes every audio input through a labelled `aformat`/`asetpts` filter |
| Reel is ~13% shorter than the clips | a bare label list between `;` separators is parsed as an empty filter name | labels are attached to the `concat` filter that consumes them |
| Every clip ~80 ms short *only on a real host* | HLS inputs start at a non-zero PTS | `cut_clip` rebases with `setpts`/`asetpts=PTS-STARTPTS` |
| Every clip one frame too long | `-t` keeps the frame that *starts* before the limit | `cut_clip` also bounds video with `-frames:v N` |
| `is not in allowed_segment_extensions` | CDNs disguise fMP4 as `.jpg` | `ffmpeg.hls_input_args` widens the whitelist and disables `extension_picky` |
| Audio a few tens of ms short per clip | AAC flush adds frames past the output `-t` | `cut_clip` pads and cuts both streams to one duration |
| A repeated line pulls a cut into the wrong scene | score alone cannot distinguish two identical matches | `align.locate` sidelobe margin + windowed re-search |
| A renderer's own tail overran the narration | the stroke runtime holds the finished picture for half a second | the scene window is the contract: the normaliser trims to it |
| A "hand-drawn" reel was really a static pan | nothing checked that ink accumulates | preflight samples the canvas and refuses zero growth; a test asserts it |

## Legal

Nadeshiko indexes third-party media; the AGPL-3.0 licence on the API covers the API, not
the anime. This tool downloads and re-encodes video from a server you control and
produces derivative clips — make sure you have the rights to whatever you publish. Quran
text is public domain; translations remain their publishers'. Cloud voices are licensed
per vendor. Check the source site's terms before automating against it.
