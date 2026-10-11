# explainer3d — narrated 3D process & fact reels, rendered from code

Status: approved 2026-10-11 (blueprints, in-shot captions and the conversation blueprint added on review)
Branch: `feat/explainer3d`

## 1. Intent

**Outcome.** A new recipe, `explainer3d`, turns a structured scene script into a vertical
(1080×1920) reel that looks like a stylised 3D animation and explains something step by
step — how a thing is made, how a process works, a fact or a piece of history — with a
voiceover and burned-in captions.

**Use cases (user, 2026-10-11).** "Any kind of 3D explainer reel" in the reference's
grammar — a hook, step-by-step explanation, deliberate camera, and a look that keeps the
viewer to the end — plus educational reels, first among them a **Japanese conversation
scenario**: 3–5 lines of N4-level dialogue acted out in a 3D setting so a learner
remembers the scenario and its sentences.

**Who drives it.** An agent over MCP is the brain: it researches the topic, writes the
shots, picks the 3D models and the moves, reviews a storyboard and starts the build. The
CLI and a topic-only LLM writer use the same code path.

**Success.** An agent goes from "explain how X is made" to a finished MP4 without writing
3D code, the same script always renders the same frames, and every model in the reel has
a recorded source and licence.

**The reference** (`~/Downloads/🎣 The secret to attracting big carp!…mp4`): 720×1280,
30 fps, 101 s, ~45 shots, no hard cuts (transitions are camera moves), a continuous
music/SFX bed, no narration, one fixed title banner. Structure: hook (0–10 s, catch) →
setup (10–24 s) → **process** (24–80 s: each ingredient flies in and drops into one stone
bowl on a slab while the camera orbits; the bowl's contents accumulate) → payoff
(80–100 s) → end card. Look: real-time-engine render with rigged realistic people.

**Deliberate differences from the reference.** We add narration and captions (user
decision). The look is stylised Three.js, not photoreal; people are limited to generic
rigged motions (idle/walk/wave) in v1.

## 2. Decisions taken

| question | decision |
|---|---|
| Rendering approach | Three.js scenes rendered by **HyperFrames** (headless Chrome, frame-seeked, deterministic, FFmpeg encode). Not AI video, not Blender. A shot-renderer seam is left for an AI-video renderer later. |
| Explanation | Voiceover (existing narration providers) + burned-in word-highlight captions + optional music bed + automatic SFX. |
| Agent input | A validated **scene script** built from a fixed vocabulary of moves. Raw Three.js per shot is **opt-in** (off by default; enabled per caller). |
| 3D models | Starter pack (CC0) + parametric shapes + Poly Pizza library search + text-to-3D (Meshy default, Tripo alternative), all cached with provenance. |
| Camera across cuts | Supported in v1: `camera.continue` + persistent scene state between shots. |
| Structure & retention | **Blueprints** (`process`, `facts`, `history`, `conversation`) set the shot structure and camera grammar; `probe` runs a **retention lint**. |
| Captions | Rendered **inside each shot as HTML** (one path for all blueprints; `<ruby>` gives furigana). SRT still exported. |
| Japanese learning | `conversation` blueprint: per-speaker voices, furigana + romaji + English captions, speaker framing, recap shot, JLPT target-word check. |

Lessons adopted from `yihui-dev/awesome-opus5-5-videos` (513 code-rendered video
prompts): animation as a pure function of `t` with no timers or carried state; a
contact sheet before the full render; a style bible ("look") shared by every shot;
procedural props from code; SFX tied to on-screen events with music ducked under voice;
plan → build → judge once; real numbers with honest uncertainty.

## 3. Architecture

```
reelmachine/recipes/explainer3d/
  recipe.py        RecipeSpec, probe, verify
  models.py        inputs, script schema, stage payloads (pydantic, extra="forbid")
  stages.py        script → assets → narration → shots → prep → compose → render
  script.py        topic-only LLM writer (same pattern as doodle's LlmScriptProvider)
  timing.py        narration → shot windows; move fractions → ms
  state.py         scene-state replay and camera continuation
  compile.py       one shot → one HyperFrames project directory
  verify.py        verifier checks
  moves/           catalogue: names, parameter models, examples, SFX cues (Python side)
  looks/           look packs (JSON)
  runtime/         the JS shipped into every shot (moves, camera, shapes, looks, loader)
  models3d/        providers: pack, shapes, polypizza, meshy, tripo, fake
  data/pack/       starter-pack GLBs + manifest.json (hash, source, licence)
reelmachine/render/hyperframes.py   the `hyperframes` renderer provider
```

Registration (pyproject): `reelmachine.recipes: explainer3d`,
`reelmachine.renderers: hyperframes`, new group `reelmachine.models3d: pack, shape,
polypizza, meshy, tripo, fake`. Narration reuses `reelmachine.narration`. New extra
`[explainer3d]` (Python-side deps only; HyperFrames itself is an npm package, pinned to an
exact version — the latest release when implementation starts, recorded once as
`HYPERFRAMES_VERSION` in `render/hyperframes.py` — and invoked with
`npx --yes hyperframes@$HYPERFRAMES_VERSION`). Default install stays
Node- and browser-free (ADR-0007); `reel doctor` reports Node ≥ 22, the pinned
HyperFrames, its Chrome and FFmpeg.

### 3.1 Stages

| stage | input → output | notes |
|---|---|---|
| `script` | inputs → `ScriptOutput` | Validates the supplied script, or writes one from `topic` via the `scripts` provider. Normalises ids. |
| `assets` | script → `AssetsOutput` | Resolves every cast member to a normalised local `.glb` (or a shape spec). Downloads/generations are cached in the shared asset store, keyed by `(provider, query/prompt, options)`. Records provenance + licence. |
| `narration` | assets → `NarrationOutput` | One line per shot, in the voice of the shot's `speaker` (cast member with a `voice`) or the reel's `voice`; word (or character) timings. Shot duration = narration span + `pad_ms` (default 250 ms lead, 350 ms tail), minimum `min_shot_ms` (1500). |
| `shots` | narration → `ShotsOutput` | Replays scene state, resolves camera continuation, compiles each shot to a HyperFrames project directory. Pure; no rendering. |
| `prep` | shots → `PrepOutput` | `storyboard` mode: one still per shot via `hyperframes snapshot`, tiled into `storyboard.jpg`. `build` mode: `hyperframes render` per shot, parallel (`REEL_EXPLAINER3D_WORKERS`, default 2), content-hash cached per shot. |
| `compose` | prep → timeline | Shots joined by a 300 ms crossfade, or a hard cut where the next shot continues the camera. Narration, music (ducked −12 dB under voice), SFX cues. |
| `render` | timeline → `reel.mp4` | Existing ffmpeg composer for joins and audio. Captions and the title banner are already in the shot frames (§5.1), so no ASS burn-in; `captions.srt` is written from the narration timings. |

`storyboard` stops after `prep`; `build` runs to `render`. A build after a storyboard
reuses every cached stage output.

## 4. The scene script

```json
{
  "mode": "build",
  "title": "The Secret Bait That Attracts Big Carp",
  "language": "en",
  "voice": "elevenlabs:<voice-id>",
  "look": "diorama-day",
  "music": "calm-1",
  "set": {"environment": "meadow-day", "stage": "stone-slab"},
  "cast": {
    "bowl":   {"asset": "pack:stone-bowl", "at": "stage.center"},
    "potato": {"asset": "auto:potato"},
    "salt":   {"asset": {"shape": "pile", "material": "salt"}},
    "baijiu": {"asset": "generate:white ceramic liquor bottle with a red cap"}
  },
  "shots": [
    {
      "narration": "First, steam one potato until it is completely soft.",
      "label": "Step 1",
      "camera": {"move": "orbit", "target": "bowl", "from": 20, "to": 45},
      "actions": [
        {"move": "fly_in",    "actor": "potato", "from": "sky",  "at": [0.0, 0.4]},
        {"move": "drop_into", "actor": "potato", "into": "bowl", "at": [0.4, 0.7]},
        {"move": "mash",      "actor": "bowl",   "fill": "#d9d27a", "at": [0.7, 1.0]}
      ]
    },
    {
      "narration": "Then add a pinch of salt.",
      "camera": {"move": "push_in", "target": "bowl", "continue": true},
      "actions": "auto",
      "auto": [{"move": "sprinkle", "actor": "salt", "into": "bowl"}]
    }
  ],
  "allow_fallback": false,
  "allow_generate": true,
  "aspect": "vertical"
}
```

Rules:

- `at` is `[start, end]` as fractions of the shot window, 0 ≤ start < end ≤ 1.
  `"actions": "auto"` lays the moves in `auto` end to end over [0.1, 0.95].
- Every `actor`, `into`, `target` must name a cast member or a set anchor
  (`stage.center`, `stage.left`, `stage.right`, `sky`, `ground`).
- A shot without `camera` holds the previous camera (static).
- `custom` (opt-in): `{"custom": {"js": "<module source>"}}` on a shot. Rejected with
  `CUSTOM_CODE_DISABLED` unless `REEL_EXPLAINER3D_ALLOW_CUSTOM=1` (stdio default: off) or
  the caller's tenant entry has `"allowCustomCode": true`. The module default-exports
  `({THREE, scene, camera, actors, t, duration}) => void` and must be a pure function of
  `t`. It is lint-checked for `Date`, `Math.random`, `performance.now`, `fetch`,
  `XMLHttpRequest`, `setTimeout`, `setInterval`, `requestAnimationFrame` and refused if
  any appear.
- `allow_generate` (default true) lets `auto:` reach a paid generator; `generate:` named
  explicitly always requires a key and is costed by `probe`.
- `music` names a loop from the pack, a local file path, or `null` for none.
- Top-level `topic` without `shots` → the `scripts` provider writes the shots.

Script limits: 1–40 shots, narration 1–240 characters per shot, 1–30 cast members, total
estimated length ≤ 180 s.

## 5. The moves runtime

All motion lives in `runtime/` as ES modules shipped into each shot directory (no CDN at
render time; Three.js is vendored at a pinned version with its licence notice).

**Contract.** Every move is `pose(params, actorState, p) → actorState'` where `p ∈ [0,1]`
is local progress, computed from HyperFrames time on the `hf-seek` event. No timers, no
randomness except a seeded PRNG keyed by `(shot id, actor, move index)`, no state between
frames. Easing uses closed-form springs (critically damped, ≤ 5 % overshoot) and cubic
eases. Particle effects (`pour`, `sprinkle`, `steam`, `smoke`, `fire`) are closed-form in
`t` per particle (seeded birth time, ballistic path), never integrated frame to frame.

**v1 vocabulary.**

| category | moves |
|---|---|
| object | `appear`, `fly_in`, `drop_into`, `place_on`, `spin_showcase`, `scale_pop`, `exit` |
| process | `pour`, `sprinkle`, `mash`, `stir`, `steam`, `smoke`, `fire`, `transform`, `assemble` |
| explainer | `label`, `count_up`, `arrow`, `highlight`, `timeline`, `map_pin`, `compare` |
| camera | `orbit`, `dolly`, `push_in`, `crane`, `follow`, `static`, `two_shot`, `over_shoulder`, `close_up` |
| character | `play` (a named clip on a rigged pack character: `idle`, `walk`, `wave`, `talk`) |
| learning | `vocab_card` |

Each move declares (Python side, `moves/`): a pydantic parameter model, one example, a
one-line description written for a model, and its SFX cue (`whoosh`, `thud`, `pour`,
`sprinkle`, `sizzle`, `pop`, `ding`, `tick`, or none) with the fraction at which it fires.
`list_moves` serves exactly this catalogue; the JS runtime and the Python catalogue are
checked against each other by a test.

**Containers.** `drop_into`, `pour`, `sprinkle`, `mash`, `stir` act on a container actor
that has a `fill` state (`level 0–1`, `colour`, `surface: liquid|paste|grain|dough`).
Shapes of kind `bowl`, `cup`, `pot`, `wok`, `jar`, `bottle` are containers; a pack or
library model is a container when its pack entry or the cast entry says
`"container": {"rim": <height fraction>, "radius": <fraction>}`.

**Looks.** JSON packs in `looks/`: sky (procedural gradient + sun), ground, lighting
(key/fill/ambient, shadow softness), tone mapping, palette, material presets, font,
caption style, title banner style, default music. v1: `diorama-day` (close to the
reference) and `studio-clean` (neutral backdrop for facts and history). One look per reel.

### 5.1 Captions and on-screen text

The runtime draws text as an HTML layer over the WebGL canvas in each shot, styled by the
look. The caption for a shot is its narration, split into display lines, with the
current word highlighted from the narration timings (character timings for Japanese,
via the existing `words_from_characters`). `label` (e.g. "Step 3") and the title banner
are drawn on the same layer. Caption text supports a furigana markup, `漢字[かんじ]`,
rendered as `<ruby>`; a reading applies to the run of kanji immediately before it.

### 5.2 Blueprints and the retention lint

A blueprint is data (`blueprints/<id>.json`) that the script may name with
`"blueprint": "<id>"` (default `process`). It supplies defaults only — anything the
script states wins:

| blueprint | structure | camera grammar |
|---|---|---|
| `process` | hook (the finished result, ≤ 3 s) → setup → one step per shot on one stage → payoff | orbit / push_in on the container, `continue` within the step run |
| `facts` | hook (the most surprising number) → 3–7 facts, each with an explainer move → recap | alternate wide and close shot sizes; `count_up` / `compare` per fact |
| `history` | hook (the turning point) → `timeline` beats in order → "why it matters" | `crane` / `dolly` between eras; `timeline` move as spine |
| `conversation` | establishing shot of the setting → one shot per line → recap | `two_shot` to establish, then `over_shoulder` / `close_up` on the speaker |

`probe` returns `details.retention`: warnings (never errors) for a first shot longer
than 3.5 s; a shot with no action and a static camera; the same camera move three shots
running; any shot longer than 7 s; total length over the blueprint's target; a
`process` reel whose last shot is not a payoff (no action on the hook's subject).

### 5.3 The `conversation` blueprint (Japanese learning)

```json
{
  "blueprint": "conversation",
  "title": "At the station — asking the way",
  "language": "ja",
  "look": "studio-clean",
  "set": {"environment": "station"},
  "cast": {
    "ken":  {"asset": "pack:char-man",   "name": "ケン", "voice": "elevenlabs:<ja-voice-1>", "at": "stage.left"},
    "yuki": {"asset": "pack:char-woman", "name": "ゆき", "voice": "elevenlabs:<ja-voice-2>", "at": "stage.right"}
  },
  "level": "N4",
  "target_words": ["駅", "まっすぐ", "曲がる"],
  "shots": [
    {"speaker": "ken",  "line": {"ja": "すみません、駅[えき]はどこですか。", "en": "Excuse me, where is the station?"}},
    {"speaker": "yuki", "line": {"ja": "この道[みち]をまっすぐ行[い]って、右[みぎ]に曲[ま]がってください。", "en": "Go straight down this road and turn right."}},
    {"speaker": "ken",  "line": {"ja": "ありがとうございます。", "en": "Thank you very much."}}
  ],
  "recap": true
}
```

- A shot with `line` uses `line.ja` (markup stripped) as its spoken text and needs no
  `narration`; `speaker` must be a cast member with a `voice`.
- Caption layout per line: speaker name, Japanese with furigana (current character
  highlighted), romaji (from the readings via `vocab.romaji_display`), English.
- Validation: every kanji run in `line.ja` has a reading (else `INVALID_INPUT` with the
  run); 3–8 lines when `blueprint` is `conversation`; each `target_words` entry must be
  in `jlpt_vocab.json` at the reel's `level` or easier (N5/N4 for `N4`) and appear in
  some line — otherwise a `probe` warning, not an error, because the bundled list is not
  exhaustive. There is no sentence tokenizer: readings come from the script, the check
  is that they are present.
- The speaking character plays `talk` (a procedural head-and-arm idle on the rigged pack
  characters); the listener plays `idle`. Default camera per line comes from the
  blueprint.
- `recap: true` appends a shot listing every line (Japanese with furigana + English) over
  a slow `orbit`, with no new narration — it is timed at 1.2 s per line plus 1 s.
- Optional `vocab_card` explainer move: `{"move": "vocab_card", "word": "曲がる",
  "reading": "まがる", "meaning": "to turn"}`; reading and level filled from
  `jlpt_vocab.json` when omitted.

Camera moves added for this blueprint: `two_shot`, `over_shoulder`, `close_up`
(targets: a cast member). Pack additions: environments `station`, `street`, `cafe`,
`convenience-store`, `classroom` (Kenney CC0 city / furniture kits), and the rigged
characters gain the procedural `talk`.

## 6. Scene state and camera continuation

`state.py` replays shots in order. Each shot starts with the world state left by the
previous shot's moves at `p = 1`: actor transforms, visibility, container fill,
transformed meshes. The resolved start state is written into the shot's `shot.json`, so
a shot renders without knowing the others.

`camera.continue: true` sets the shot's camera start pose to the previous shot's end pose
(position, target, fov). A run of continuing shots is a **chain**: easing-in applies only
at the chain's first shot and easing-out only at its last; joins move at constant
velocity. A chain breaks (with a crossfade) when the set or look changes; asking to
continue across such a break is an `INVALID_INPUT`. Joins inside a chain are hard cuts in
`compose`.

Cache keys include the resolved start state and camera start pose, so editing shot N
re-renders N and every later shot whose inherited state changed — and nothing else.

## 7. 3D models (`reelmachine.models3d`)

Interface: `resolve(spec, ctx) → ModelAsset {path, bbox, clips, provenance, licence,
attribution, cost}`, `search(query, limit, licences) → [Candidate]`, `missing() →
[str]`, `estimate(spec) → cost`.

| prefix | provider | needs | behaviour |
|---|---|---|---|
| `pack:` | `pack` | nothing | ~40 CC0 GLBs (Kenney / Quaternius / KayKit; kitchen and process props, nature, a cottage, 2–3 rigged characters with idle/walk/wave). Hashes and sources in `data/pack/manifest.json` and THIRD_PARTY_NOTICES. |
| `{"shape": …}` | `shape` | nothing | Built in the runtime from parameters; never fails. Kinds: bowl, cup, pot, wok, jar, bottle, ball, box, sack, pile, liquid, plinth. Materials: stone, wood, ceramic, metal, glass, dough, salt, grain, water. |
| `lib:<id>` | `polypizza` | `REEL_POLYPIZZA_API_KEY` | Search + GLB download. Accepts CC0 and CC-BY by default; `REEL_EXPLAINER3D_LICENCES` widens it. NC/SA/ND refused with `LICENCE_REFUSED`. CC-BY attribution is written to `credits.txt`. |
| `generate:<prompt>` | `meshy` (default) / `tripo` | `REEL_MESHY_API_KEY` / `REEL_TRIPO_API_KEY` | Async create + poll inside the job, never in a tool call. Cached by `(provider, prompt, options)`. Metered per caller (`quotas.meshy`, `quotas.tripo`). `probe` reports the estimated cost. |
| `auto:<noun>` | — | — | pack → library → generate (if a key is set and `allow_generate`, default true) → shape (if `allow_fallback`). |

**Normalising** (on download, cached): recentre, scale to the requested or default height
(default 0.3 m props, 1.7 m characters), drop to ground, record bbox and clip names,
refuse > `REEL_EXPLAINER3D_MAX_MODEL_MB` (default 8) or > 300k triangles. Done in Python
with `pygltflib` + numpy (in the `[explainer3d]` extra), not in the browser.

To verify during build: Poly Pizza's licence field name and rate limits, and Meshy's and
Tripo's current pricing, against the live APIs.

## 8. The renderer (`hyperframes`)

`HyperframesRenderer` (entry point `reelmachine.renderers: hyperframes`):

- `missing()`: Node ≥ 22 on PATH; `npx hyperframes@$HYPERFRAMES_VERSION doctor` passes; FFmpeg present.
- `snapshot(shot_dir, at_fraction=0.6) → png`: one frame for the storyboard.
- `render(shot_dir, out_mp4, fps=30, size=1080×1920) → mp4`: `hyperframes lint` first;
  lint failure → `SHOT_RENDER_FAILED` with the lint output in `details`.
- Runs with a timeout of `max(120 s, 20 × shot seconds)` and in its own process group,
  so `cancel_job` kills Chrome.
- Writes `render.log` per shot next to the output.

## 9. MCP surface

Existing tools carry the recipe (`describe_recipe`, `probe`, `start_job`, `get_job`,
`cancel_job`, `get_artifact`). Two additions, both read-only and with no render:

**`list_moves`** `{"category"?: "object"|"process"|"explainer"|"camera"|"character"}` →
moves (name, description, parameter schema, example, SFX cue), looks, shapes, set
anchors.

**`find_models`** `{"query": str, "licences"?: [str], "limit"?: int (≤ 30)}` → candidates
from the pack and, when a key is set, Poly Pizza: `asset` value to paste, title, licence,
author, triangle count, preview URL. It reports whether `generate:` is available and its
estimated cost, but never generates. Library calls time out at 5 s; on timeout the
result holds the pack candidates and a `warnings` entry.

**`probe`** for `explainer3d`: full script validation with paths
(`shots[3].actions[1].into`), the provider each cast member would resolve to (no download,
no generation), estimated per-shot and total length, paid-cost estimate (generations +
TTS characters), renderer readiness.

**`start_job`** modes: `storyboard` → `storyboard.jpg`, `script.resolved.json`,
`manifest.json`; `build` → `reel.mp4`, `captions.srt`, `timeline.json`,
`credits.txt`, `manifest.json`.

Docs: `docs/MCP.md` gains both tools with one example each; a new
`docs/adr/0014-recipe-tools.md` records that recipe-specific read-only tools are allowed
(as `suggest_words` and `find_examples` already are) and that ADR-0005's count is
superseded; `docs/EXPLAINER3D_AGENT.md` is the playbook.

## 10. Agent playbook (summary of `docs/EXPLAINER3D_AGENT.md`)

1. Ask, one at a time: topic, blueprint, language, voice(s), look, target length
   (30–90 s). For `conversation`: scenario, level, 2 speakers, target words.
2. Research. Use real numbers; state uncertainty in the narration where it exists.
3. Write shots as **hook → steps → payoff**: one idea per shot, 6–14 spoken words.
   Process topics keep one stage and one container so the state accumulates; use
   `camera.continue` inside a step sequence.
4. `list_moves`; `find_models` for each cast member.
5. `probe`; fix every error; read `details.retention` and fix what you agree with;
   confirm any paid cost with the user.
6. `start_job` `storyboard`; read `storyboard.jpg`; judge once (legibility, framing,
   does each still show its step); fix only clear failures.
7. `start_job` `build`; poll `get_job`; `get_artifact reel.mp4`.

## 11. Errors

Structured `{code, message, hint, stage, details}` as everywhere else:

| code | when |
|---|---|
| `INVALID_INPUT` | schema or reference errors, with a field path |
| `CUSTOM_CODE_DISABLED` | a `custom` shot without permission |
| `RENDERER_UNAVAILABLE` | Node / HyperFrames / Chrome / FFmpeg missing; hint carries the install command |
| `MODEL_UNAVAILABLE` | a cast member cannot be resolved and `allow_fallback` is false |
| `LICENCE_REFUSED` | a library model's licence is outside the allowed set |
| `QUOTA_EXCEEDED` | existing; now also for `meshy` / `tripo` |
| `SHOT_RENDER_FAILED` | lint or render failure; `details.shot`, `details.lint`, `details.log` |
| `VERIFICATION_FAILED` | existing; see §12 |

With `allow_fallback: true` an unresolved model becomes a shape and the manifest records
`degraded: true` with the reason per cast member.

## 12. Verification (`verify.py`)

A reel is refused when:

- any shot's sampled frames (5 per shot) show no change between first and last
  (mean absolute difference below threshold) — a frozen shot;
- `reel.mp4` duration differs from the narration timeline by more than 100 ms;
- any model in the manifest lacks a licence;
- any shot track was not produced by the renderer the manifest names;
- the output is not 1080×1920, SAR 1:1, H.264 yuv420p;
- for `conversation`, any line shot was voiced by a voice other than its speaker's.

## 13. Testing

- **Unit (fast, no Node):** script validation and error paths; timing maths; `auto`
  layout; state replay; camera chains (easing only at chain ends; break on set change);
  cache-key inheritance; model resolver with the `fake` provider; licence filter;
  compile snapshot of one shot's `shot.json`/`index.html`; MCP tools `list_moves`,
  `find_models` (pack-only, and library timeout path with a stub), `probe`; blueprint
  defaults and override precedence; each retention-lint warning; furigana parsing and
  the missing-reading error; romaji from readings; target-word level check;
  conversation shots voiced by their speaker; recap timing.
- **Runtime (Node, `node --test`):** every move is deterministic (same `p` → same pose),
  hits its declared start/end poses at `p=0/1`, and particle positions are pure in `t`.
  A test asserts the Python catalogue and the JS runtime name the same moves with the same
  parameters.
- **Integration (`slow`, skipped without the renderer):** a 3-shot reel with the fake
  voice and shapes only (no network): duration, motion check, captions present; render
  frame N of one shot twice → identical pixels; a 2-shot continuing camera chain → the
  last frame of shot 1 and the first frame of shot 2 differ by less than a threshold;
  a 3-line `conversation` reel with the fake voice renders ruby captions (a pixel probe
  finds text in the caption band of every shot).
- **Bare install:** with `node` removed from PATH, the recipe imports, `reel recipes`
  lists it, and `probe` reports `RENDERER_UNAVAILABLE` rather than crashing.
- CI: the existing workflow gains a job with Node 22 and the pinned HyperFrames for the
  runtime and integration tests.

## 14. Out of scope for v1

AI-video shot renderer (the renderer seam allows it later); PINOC / Mixamo-style custom
character motions beyond the pack's clips; camera continuation across a set change;
custom code over HTTP unless a tenant enables it; landscape output; music generation
(v1 ships 3–5 CC0 music loops in the pack, credited in `credits.txt`; a local file
path is also accepted).
