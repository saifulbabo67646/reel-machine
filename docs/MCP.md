# MCP reference

reel-machine exposes its engine to agents through one MCP server with eleven tools, over
two transports: `stdio` (a local agent) and `streamable-http` (a hosted deployment).

```
pip install 'reel-machine[mcp]'          # the MCP dependency is an optional extra
reel mcp serve --transport stdio         # local agent
reel mcp serve --transport streamable-http --host 0.0.0.0 --port 8765
```

Nothing here blocks on a render: `start_job` returns a job id in milliseconds and the
caller polls `get_job`. Every failure is a structured payload —
`{"code", "message", "hint", "stage", "details"}` — never a traceback.

## Transports and callers

- **stdio** is a local, trusted transport. The caller is `REEL_MCP_CALLER` (default
  `local`) unless a token is configured.
- **streamable-http** requires a bearer token on every request. Configure either a single
  caller:

  ```bash
  export REEL_MCP_TOKEN=...            # the raw token never leaves the deployment's env
  export REEL_MCP_CALLER=alice
  ```

  or a tenants file for many callers:

  ```json
  {
    "callers": [
      {
        "id": "alice",
        "tokenSha256": "<sha256 of alice's token>",
        "allowedRecipes": ["nadeshiko-cut"],
        "maxConcurrentJobs": 2,
        "maxDiskMb": 2048,
        "quotas": { "nadeshiko": 500 }
      }
    ]
  }
  ```

  ```bash
  reel mcp token-hash - <<< 'the-raw-token'    # prints the sha256 to paste above
  REEL_MCP_TENANTS=tenants.json reel mcp serve --transport streamable-http
  ```

  Servers store only the hash. Each caller sees only its own jobs and artifacts; caller A
  gets a clean `NOT_FOUND` for caller B's job.

## Artifacts

`get_artifact` returns metadata plus a short-lived **signed URL** — never bytes:

- over HTTP, `GET /artifacts/<caller>/<job>/<name>?expires=&sig=` on the same app,
  signed with `REEL_MCP_SIGNING_KEY` (set it to keep URLs valid across restarts), 15
  minutes by default;
- over stdio there is no listener, so the URL is a `file://` URL.

The result carries `bytes`, `sha256`, `kind`, `provenance` and `licence` so a caller can
verify what it fetched and know where it came from.

## Schema versioning

Every tool result carries `schemaVersion` (currently `1`). Within a major version,
changes are additive: new fields appear, existing ones keep their meaning. Recipe input
schemas come from the recipe's declared model and are returned by `describe_recipe`.

## The tools, with one example each

### `list_recipes`

List what this deployment can make (id, title, render modes, cost).

```json
{}
```

### `browser_status`

The backends available for fetching pages behind Cloudflare Turnstile: which is selected
(`patchright` preferred — it can click the widget inside its cross-origin frame — with
`agent-browser` as the fallback), whether it is installed, the persistent profile, and the
hosts that profile has already cleared. Read-only and instant: it never launches a browser,
because a solve takes seconds and no tool call here may wait. Call it when a source or page
is gated; the fetch itself happens inside the engine, not in a tool call.

```json
{}
```

The result names the backends and their state:

```json
{
  "schemaVersion": 1,
  "selected": "patchright",
  "configured": "auto",
  "backends": [
    {"name": "patchright", "status": "ok", "detail": ""},
    {"name": "agent-browser", "status": "missing", "detail": "agent-browser is not on PATH (npm i -g agent-browser && agent-browser install)"}
  ],
  "profile": ".work/browser/profile",
  "headless": false,
  "clearedHosts": {"getyarn.io": 1791690876}
}
```

### `describe_recipe`

Input JSON Schema, required providers and their state, cost/quota implications,
artifacts, and one example request.

```json
{"recipeId": "nadeshiko-cut"}
```

### `probe`

Validate inputs and source availability without spending quota or rendering.

```json
{"recipeId": "nadeshiko-cut", "inputs": {"word": "彼女", "only": ["<mediaPublicId>:3"]}}
```

For `quranic` the result's `details.verses` carries the selected ayahs — Arabic text,
words and translation — so a caller can choose things that depend on meaning (which
background behind 1:6, which art for a beat) before starting the job:

```json
{"recipeId": "quranic", "inputs": {"surah": 1, "ayah_start": 1, "ayah_end": 7}}
```

For `storyreel` a bare `title` resolves through TMDB and reports the ranked candidates
in `details.candidates` (with a 12-month trending list in `details.trending` when no
title is given), then checks vidvault availability without downloading anything. The
job's `mode` decides where it stops — `transcript` after the script is readable,
`plan` after the top-5 concepts, `build` through the render — and the concept the
viewer picked from `concepts.json` goes into the build job:

```json
{"recipeId": "storyreel", "inputs": {"title": "Inception", "mode": "plan"}}
```

The full agent playbook (the questions, the JSON shapes, the storytelling rules) is
`docs/STORYREEL_AGENT.md`.

For `explainer3d`, probe validates the whole scene script without rendering — a refusal
names the field (`details.error.path`, e.g. `shots[2].actions[0].into`) — and returns the
estimated length per shot and the voice characters it will spend:

```json
{"recipeId": "explainer3d", "inputs": {"mode": "storyboard", "cast": {"bowl": {"asset": {"shape": "bowl"}, "at": "stage.center"}}, "shots": [{"narration": "A bowl.", "camera": {"move": "orbit", "target": "bowl"}}]}}
```

It also returns retention `warnings` — no hook in the first half second, more than 2.8 s
without a beat, no re-hook on reels over 40 s, no call to action, a length far from the
`length` target, a camera looking through the stage — each saying what to change.
`describe_recipe("explainer3d")` carries a `guide`: the workflow, every move and camera
with its parameters, the sets and anchors, the narration markup (`*hot*`,
`[shown|spoken]`) and how many beats a 25 s or 55 s reel needs. Complete cinematic
scripts: `examples/explainer3d/monster-fish-bait-25s.json` and `-55s.json`.

### `start_job`

Start a render; returns a job id immediately. An `idempotencyKey` makes retries safe: the
same key always maps to the same job.

```json
{"recipe": "nadeshiko-cut", "inputs": {"word": "彼女", "count": 5}, "idempotencyKey": "reel-2026-10-01"}
```

### `get_job`

State, progress (stage, percent, detail), artifacts, and a structured error when it
failed.

```json
{"jobId": "9f2c1ab34d5e"}
```

### `list_jobs`

This caller's jobs, newest first, optionally filtered by state.

```json
{"state": "running", "limit": 20}
```

### `cancel_job`

Cancel a queued or running job. A job that already finished is returned unchanged.

```json
{"jobId": "9f2c1ab34d5e"}
```

### `get_artifact`

A signed, expiring URL for one artifact, plus its size, hash, provenance and licence.

```json
{"jobId": "9f2c1ab34d5e", "name": "reel.mp4"}
```

### `suggest_words`

Words the corpus can actually teach, best first — for choosing what a reel should be
about (`wordreel`). Reads the subtitles on disk, so it is fast and read-only: no job, no
render. Words come back because they recur in real dialogue, not from a word list, so
each one is known to have examples behind it.

```json
{"recipe": "wordreel", "limit": 12}
```

### `find_models`

CC0 3D models for an explainer3d cast (Poly Haven, and the Khronos photoreal fish),
searched by name and tags. Read-only and free; the first call fetches the catalogue. Put
a result in a cast member as `{"asset": {"model": "polyhaven:wooden_bowl_01", "size": 0.3,
"container": true}}`.

```json
{"query": "wooden bowl"}
```

### `find_examples`

The lines of dialogue that use a word or phrase, each with its file, timestamp and the
form that matched. Call it before `start_job` to see exactly what a reel would be built
from. Inflected forms match too (`gave up` for `"give up"`) unless `allowInflected` is
false. Read-only: no job, no render.

```json
{"word": "give up", "inputs": {"library": ["/media/films"]}}
```

## Errors

| code | what to do |
|---|---|
| `INVALID_INPUT` | fix the inputs; `describe_recipe` has the schema and an example |
| `UNAUTHORIZED` | fix the bearer token |
| `NOT_FOUND` | the job or artifact does not exist for this caller |
| `QUOTA_EXCEEDED` | wait for the quota to reset or ask for a higher limit |
| `CONCURRENCY_LIMIT` | wait for a running job to finish |
| `DISK_LIMIT` | delete old jobs or ask for more disk |
| `PROVIDER_UNAVAILABLE` | the deployment is missing a key or an extra; `describe_recipe` says which provider |
| `SOURCE_UNRESOLVED` | the source provider cannot find that episode; check `reel probe` |
| `ALIGNMENT_FAILED` | the reference clips did not match the caller's copy |
| `VOICE_UNAVAILABLE` | the requested voice is not licensed/configured on this deployment |
| `RENDER_FAILED` / `PREFLIGHT_FAILED` / `VERIFICATION_FAILED` | the render itself refused; the detail says where |
| `INTERRUPTED` / `CANCELLED` | re-submit with the same idempotency key to resume from cached stages |

## What the server deliberately does not expose

No MCP resources, prompts or sampling — tools only, and only the eight above. No tool
returns file bytes. No tool call waits for a render. No secret appears in a result.
