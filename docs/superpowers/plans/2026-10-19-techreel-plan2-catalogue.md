# techreel Plan 2 — Catalogue and Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The full visual vocabulary (the remaining 16 components, 5 more hero kinds, the `locked`/`split` states, automatic `small-top`), the CTA ending built from `creator`/`keyword`, agent SVG and caller images, the full lint (§9 rules 5 and 7–15), and the complete agent guide with three worked example scripts.

**Architecture:** Same pipeline as Plan 1. Models gain visuals; `compose._visual` passes them through camelCased (overlays: `stamp`, `emoji`, `error`, `flash`); new runtime modules `runtime/cards.mjs` (data/UI cards) and `runtime/social.mjs` (comment, follow, people, nodes, heartbeat, flash, error) export component records merged into `COMPONENTS` in `main.mjs`. A new `assets` stage fetches images into the project.

**Tech Stack:** as Plan 1. Pillow is already a dependency (used by `dialoguereel/images.py`).

**Spec:** `docs/superpowers/specs/2026-10-19-techreel-design.md` (§5, §9, §10, §13 Plan 2).

## Global Constraints

- Everything in Plan 1's Global Constraints still binds (bands, determinism, no bundled fonts, no new deps, `say` rules).
- Each new component: `{build(v, doc) -> HTMLElement, update(node, v, t, doc)}`, deterministic from `t`, enter spring ~250 ms, styled only from look tokens (CSS variables), natural size measured once at build.
- ≤ 4 visuals live at once is a lint *warning*, not an error.
- Agent SVG: the dialoguereel safety rules exactly (≤ 60 KB, `defusedxml`, no script/foreignObject/`on*`/external refs, `data:` PNG/JPEG only).
- Images: `https` or (local runs only, `ctx.config.local_files`) a path; public-address guarded download; PNG ≤ 1600 px longest side.

## Review Focus

1. **An `image` URL that redirects to a private address or isn't an image** — refused with a path-bearing `InvalidInput`, never a crash or a hang. Test in Task 3.
2. **A CTA beat with no `creator`** — the follow card is skipped, comment box still shows; no `None` in the DOM. Test in Task 5.
3. **Agent SVG ids colliding with page ids** (two `svg` visuals both using `id="a"`) — ids scoped per visual (reuse `dialoguereel.svg.inline_svg` with the visual id as scope). Test in Task 3.
4. **`people` with `total` 500** — capped (model `le=200`), grid fits the stage width at any total. Test in Task 1 (model) and Task 4 (node test of grid geometry).
5. **Tiny caption chunks** (a 120 ms one-word chunk) — merged into the neighbour so no caption flashes for < 350 ms. Test in Task 6.

---

### Task 0: Pacing and render fidelity (from the Plan 1 Supertonic acceptance reel)

The acceptance reel (`scratchpad/t9/pocket-llm-hi.mp4`, report `.superpowers/sdd/2026-10-19-techreel-plan1-foundation/task-9-report.md`) showed:

**Files:** Modify `stages.py` (narration), `runtime/main.mjs`, `runtime/components.mjs`, `runtime/hero.mjs`, `runtime/reel.css`; Test `tests/test_techreel_stages.py`, node tests, a slow render check.

1. **Dead air**: Supertonic clips carry ~0.5 s leading and ~0.7 s trailing silence (≈ 10 s of a 37.5 s reel). Add `trim_clip(src, dest, *, settings, keep_ms=60) -> tuple[Path, int, int]` in `stages.py` (ffmpeg `silencedetect` at −45 dB on the clip, cut to [first_sound − keep, last_sound + keep]; returns the new path, the trimmed lead ms and the new duration) used for every line before scheduling; word spans shift by −lead and clamp to the new duration. Test: a synthesised clip (anullsrc 500 ms + sine 800 ms + anullsrc 700 ms) trims to ≈ 920 ms with lead ≈ 440 ms.
2. **Thin headings in the full render**: in `pocket-llm-hi.mp4` the display font falls back to a regular-weight face, while `snapshot` stills were heavy. Find why (font loading timing in render vs snapshot; `document.fonts.load` for the heading/display font before `buildReady` resolves; the font stack must reach a heavy face on macOS and Linux — add `"Arial Black"`/`"Helvetica Neue"` weight 900 via `font-weight` and `font-synthesis: weight`) and fix; verify by rendering 2 s of the example and comparing a heading frame with a snapshot at the same time (mean abs diff in the heading band < 6).
3. **Visual nits**: the stamp spills past the side margins while it scales in (clamp using the 1.8× start scale); the orb overlaps the counter for a moment while gliding (the stack must reserve the hero's slot at its *target* scale during the glide); headings orphan a short last word ("SE") — use `text-wrap: balance`; the partially revealed caption sits off-centre (centre the pill on the *full* chunk, reveal inside it — Task 6 later makes the pill grow); the `window` mock is empty — give it `rows: list[str ≤48] ≤4` text rows (model + runtime) rendered as chat/list lines instead of grey skeletons when present.
4. **Hindi pronunciation note** for the guide (Task 8): Latin "AI" inside Devanagari `say` is read as a long "एएएए" and "LLM"/"एलएलएम" garbles — write "एआई", "एल एल एम" (spaced letters). Record in `GUIDE["language"]`.

- [ ] Steps: failing test for `trim_clip` → implement 1 → PASS; fix 2 with the render comparison; fix 3 with snapshots LOOKed at; Commit `techreel: tight pacing, heavy headings and visual fixes`.

### Task 1: Models for the full catalogue

**Files:** Modify `reelmachine/recipes/techreel/models.py`, `compose.py` (`_OVERLAYS`); Test `tests/test_techreel_models.py`, `tests/test_techreel_compose.py`

**Interfaces (Produces):** new visuals on `VisualBase`, added to the `Visual` union (field limits in brackets):
- `EquationVisual(kind="equation", rows: list[str ≤40] 1..4, result: str ≤24)`
- `ChatVisual(kind="chat", channel: str ≤32 = "", messages: list[ChatMessage] 1..5)`; `ChatMessage(sender ≤24, text ≤120, bot: bool = False)` (`from` is a Python keyword — field `sender`, alias `"from"`, `populate_by_name`)
- `DialogVisual(kind="dialog", title ≤40, body ≤120 = "", approve ≤16 = "Approve", decline ≤16 = "Decline", click: Literal["approve","decline"]|None = None, click_at: Cue|None = None)`
- `ErrorVisual(kind="error", title ≤40, body ≤120, button ≤12 = "OK")`
- `ReceiptsVisual(kind="receipts", items: list[ReceiptItem] 1..5)`; `ReceiptItem(title ≤32, sub ≤32 = "")`
- `ChecklistVisual(kind="checklist", items: list[str ≤40] 1..6, state: Literal["done","progress"] = "done")`
- `PeopleVisual(kind="people", total: int 1..200, lit: int ≥0 (≤ total, validator), label ≤40 = "", badge ≤24 = "")`
- `VersusVisual(kind="versus", left: Score, right: Score)`; `Score(label ≤20, value ≤12, emoji ≤16 = "")`
- `NodesVisual(kind="nodes", items: list[NodeItem] 2..6, layout: Literal["row","triangle","ring","flow"] = "row")`; `NodeItem(label ≤20, icon ≤16 = "")`
- `BarsVisual(kind="bars", items: list[BarItem] 2..6, unit ≤8 = "", highlight: int|None = None)`; `BarItem(label ≤20, value: float ≥0)`
- `FlashVisual(kind="flash", text ≤40)`; `HeartbeatVisual(kind="heartbeat", label ≤40 = "")`
- `CommentVisual(kind="comment", keyword ≤24, reply ≤40 = "")`; `FollowVisual(kind="follow")` (draws `creator`)
- `ImageVisual(kind="image", src: str 1..500, highlight: tuple[float,float,float,float]|None` each 0..1)
- `SvgVisual(kind="svg", svg: str 1..60000, size: Literal["s","m","l"] = "m")`
- `HeroKind` += `"chip","phone","robot","svg","image"`; `HeroSpec` gains `svg: str|None ≤60000`, `src: str|None ≤500` (validator: `svg` iff kind `svg`, `src` iff kind `image`); `HeroState` += `"locked","split"`; `HeroVisual` gains `count: int 2..6 = 3` and `labels: list[str ≤16] ≤6 = []` (for `split`).
- `compose._OVERLAYS = ("stamp", "emoji", "error", "flash")`; the doc gains `"creator": {name, handle, avatar}|null` and per beat `"keyword"`, `"dm"`, `"next"` (empty strings when absent).

- [ ] **Step 1: Failing tests** — each new kind validates from a minimal dict and refuses one out-of-range field; `ChatMessage` accepts `"from"`; `PeopleVisual(total=10, lit=11)` fails; `HeroSpec(kind="svg")` without `svg` fails; compose: an `error` visual has `class == "overlay"`, `doc["creator"]` and a beat's `keyword` round-trip.
- [ ] **Step 2:** `uv run pytest tests/test_techreel_models.py tests/test_techreel_compose.py -q` → FAIL. **Step 3:** Implement. **Step 4:** PASS + fast suite.
- [ ] **Step 5:** Commit `techreel: models for the full catalogue`.

### Task 2: Runtime cards (`runtime/cards.mjs`)

**Files:** Create `runtime/cards.mjs`; Modify `runtime/main.mjs` (merge registries), `runtime/reel.css`, `runtime/pure.mjs` (+ tests), `runtime/tests/make_fixture.py` + `fixture.json` (add every new card).

**Interfaces:** `export const CARDS = { equation, chat, dialog, receipts, checklist, versus, bars }`. Behaviour:
- `equation`: rows appear 180 ms apart, a rule draws left→right (250 ms), the result rolls in big in the accent colour (use `counterValue` when the result parses as number+suffix, else slams).
- `chat`: a channel header; messages appear one per 450 ms from `enterMs` with avatar circles (bot messages get an accent ring and a small "BOT" chip), typing dots for 300 ms before each.
- `dialog`: a glass card with icon, title, body, two buttons; at `click_at` (or 70 % through the visual's life if `click` is set without a cue) the chosen button press-scales and gets a ✓ (approve) or ✕ (decline).
- `error`: (overlay, here for code sharing — export from cards and register as overlay) a light OS-style window (`#f4f4f5` card, dark text, a ⚠ icon, OK button) that shakes in (±14 px, 300 ms).
- `receipts`: paper tickets (off-white, mono font, dashed dividers, slight alternating rotation ±3°) stacking downward 260 ms apart, each overlapping the previous by 40 %.
- `checklist`: items appear 200 ms apart; `done` ticks each 250 ms after it appears (accent check); `progress` ticks all but the last, which shows a spinner (rotation from `t`).
- `versus`: two score cards with emoji, label and big value, a "VS" between them, the winner (higher numeric value, else right) glows.
- `bars`: horizontal bars grow over 600 ms (staggered 120 ms), values roll with `counterValue` logic, `highlight` bar in accent, others muted.
- `pure.mjs` gains `staggerP(i, enterMs, t, gapMs, durMs) -> 0..1` and `winner(left, right) -> "left"|"right"`, with node tests.

- [ ] **Step 1:** node tests for `staggerP` and `winner`; extend the fixture with one of each card. **Step 2:** run → FAIL. **Step 3:** implement cards + CSS. **Step 4:** node tests PASS; render snapshots at each card's enter + 1 s with `HyperframesRenderer.snapshot` and LOOK at them (bands respected, legible, on-style with the reference contact sheets in the scratchpad `ref/` folder).
- [ ] **Step 5:** Commit `techreel: the card components`.

### Task 3: Agent SVG, caller images and the assets stage

**Files:** Create `reelmachine/recipes/techreel/assets.py`; Modify `models.py` (stage output), `stages.py` (new `AssetsStage` between script and narration), `recipe.py` (stage list), `compose.py` (`write_project` copies `assets/`, `project_key` hashes their bytes; svg inlined), `reelmachine/recipes/dialoguereel/images.py` (generalise `read_pose` → `read_image(ref, *, allow_local, path, what="pose image")`, keep `read_pose` as a thin wrapper), `runtime/cards.mjs` (`image`, `svg` components), hero (`svg`, `image` kinds); Test `tests/test_techreel_assets.py`.

**Interfaces:**
- `assets.fetch_images(inputs, folder: Path, *, allow_local: bool) -> dict[str, str]` — maps each distinct `src` (image visuals and an `image` hero) to a file name `img-<sha256(src)[:16]>.png` written in `folder`; uses `read_image(..., what="image")`; normalise to PNG ≤ 1600 px (pass a max side; don't trim alpha for opaque screenshots — add `trim: bool = True` param to `read_image`, techreel passes `False`).
- `assets.check_svgs(inputs) -> None` — runs `dialoguereel.svg.check_svg` on every `svg` visual and an `svg` hero, path `beats[i].visuals[j].svg` / `hero.svg`; raises `InvalidInput` (lint rule 5 reports the same as an error in Task 7).
- `AssetsStage` (`id="assets"`, not cacheable): `fetch_images` into `ctx.workdir/"assets"`, cached per caller by URL hash under `cache/techreel/images/`; output `AssetsOutput(script, images: dict[str,str], folder: str)`; narration consumes it.
- Doc: image visuals carry `"file": "assets/img-….png"`; svg visuals carry `"svg"` already scoped by `inline_svg(svg, vid)`.
- Runtime: `image` — rounded 28 px frame, Ken-Burns (scale 1.0→1.06 over its life, seeded drift), `highlight` box draws as an accent rectangle at enter + 400 ms with a pulse; `svg` — sized `s` 320, `m` 520, `l` 760 px wide, pops in. Hero `svg`/`image` — the asset centred in a 420 px box; all hero states apply.

- [ ] **Step 1: Failing tests** — `fetch_images` with a local PNG (allow_local) writes the hashed file; a local path with `allow_local=False` → `InvalidInput` with `details["path"]`; an `http://127.0.0.1/x.png` URL in guarded mode → refused (monkeypatch DNS as dialoguereel's image tests do); `check_svgs` refuses `<script>` naming the path; two svg visuals with `id="a"` produce distinct scoped ids in the doc; `project_key` changes when an asset's bytes change.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS; snapshot a fixture with an image and an svg; LOOK.
- [ ] **Step 5:** Commit `techreel: agent svg, caller images and the assets stage`.

### Task 4: Social and analogy components (`runtime/social.mjs`)

**Files:** Create `runtime/social.mjs`; Modify `main.mjs`, `reel.css`, `pure.mjs` (+ tests), fixture.

**Interfaces:** `export const SOCIAL = { people, nodes, heartbeat, flash, comment, follow }`.
- `people`: a grid of simple person glyphs (circle head + rounded body, SVG), columns `ceil(sqrt(total × 1.6))` capped so the grid is ≤ 888 px wide; glyphs fade in in reading order over 600 ms; then `lit` glyphs (seeded spread across the grid) turn accent with a small hop; `label` mono under the grid with a live count ("OFFICE MEIN 125 LOG"-style, the agent writes the label); `badge` (e.g. "MEETING 💤") pill floats over the unlit area.
- `nodes`: circular accent nodes with labels/icons in `row`/`triangle`/`ring`/`flow` layouts; connecting lines draw (stroke-dashoffset from `t`) 150 ms after both ends exist; `flow` adds arrowheads.
- `heartbeat`: an ECG polyline drawn left→right over 900 ms, then a bright dot sweeping it every 1.2 s; label mono below.
- `flash` (overlay): full-bleed accent fill (z under captions) with `text` in `accentInk` display font, in 150 ms, holds, out 150 ms before its exit.
- `comment`: an Instagram-like comment row (avatar circle, "Add a comment…" placeholder) types `keyword` with a caret (`typed`), a send-arrow press at the end, then `reply` appears as a small chip ("📩 link DM kar dunga").
- `follow`: a creator card (avatar — `creator.avatar` image if given else an initial in an accent circle — name, handle), a big "Follow" button that flips to "Following ✓" 900 ms after enter with a press and a ding moment.
- `pure.mjs`: `gridOf(total, maxW, cell) -> {cols, rows, size}`, `litSet(total, lit, seed) -> Set<number>`, `nodeLayout(n, layout) -> [{x,y}]` (fractions), with node tests (`gridOf(200, 888, …)` fits; `litSet` deterministic and of size `lit`).
- Python `sfx_cues` additions: `ding` at follow flip (enter + 900), `tick` per typed keyword char batch (one tick at comment enter), `thud` at `error` enter, `whoosh` at `flash` enter.

- [ ] Steps: node tests FAIL → implement → PASS → snapshots LOOK → `uv run pytest tests/test_techreel_stages.py` (sfx additions with tests) → Commit `techreel: social and analogy components`.

### Task 5: Hero kinds, states and the CTA ending

**Files:** Modify `runtime/hero.mjs`, `runtime/main.mjs`, `runtime/pure.mjs` (+ tests), `compose.py`, `reel.css`; Test `tests/test_techreel_compose.py`, node tests.

**Interfaces:**
- New hero kinds drawn in the house line-art style (4 px off-white strokes, accent glow): `chip` (GPU card: outline, two fans rotating from `t`, a "VRAM" label strip), `phone` (rounded rectangle, notch, screen shows `text`), `robot` (friendly bot head, antenna, two eye dots that blink every 3–5 s seeded). `HERO_SIZE` gains them.
- States: `locked` — the hero sits inside a frosted glass box (backdrop blur, `--cardEdge` border) with a padlock that drops in from above; `split` (orb and robot only; others treat it as `center`) — the hero shrinks to 0 while `count` accent dots fly out to `nodeLayout(count, "triangle"|"row")` positions with `labels` under them.
- Auto `small-top` (spec §5.2): while the hero is in `center` and any other block is live in the stack, it renders as `small-top` (scale 0.45, top of the stack), gliding with `HERO_MOVE_MS`; pinned states (`pulse`, `shake`, `rings`, `screen`, `sleep`, `locked`) keep their scale. Add `effectiveHeroState(state, othersLive) -> state` to `pure.mjs` with tests.
- CTA defaults in `compose.reel_document`: a beat with `kind == "cta"` and no visuals gets generated visuals: `comment` (keyword = `beat.keyword` or the reel title's first word upper-cased; reply = `beat.dm` or "") at the first line; `follow` at the second line (or the first line's `frac 0.6`) when `creator` is set; a `title` with `beat.next` at the last line when set. Generated ids continue the `b{beat}v{n}` scheme.

- [ ] **Step 1: Failing tests** — compose: a CTA beat without visuals gets `comment` + `follow` + `title`; without creator, no `follow` (Review Focus 2); node: `effectiveHeroState("center", true) === "small-top"`, `("pulse", true) === "pulse"`, split dot positions.
- [ ] **Step 2–4:** FAIL → implement → PASS; snapshots of each hero kind × `center`/`locked`/`split`; LOOK.
- [ ] **Step 5:** Commit `techreel: more heroes, locked and split, and the call-to-action ending`.

### Task 6: Caption polish

**Files:** Modify `compose.py` (caption chunks), `runtime/main.mjs`/`reel.css` (pill); Test `tests/test_techreel_compose.py`.

**Interfaces:** `compose.merge_short(chunks: list[list[int]], times: list[tuple[int,int]], min_ms: int = 350, max_units: int = 8) -> list[list[int]]` — a chunk shorter than `min_ms` merges into the next chunk of the same line (or the previous when it is last), unless the merge exceeds `max_units` (spaced) / 18 chars (unspaced). The runtime pill's width follows the revealed words (measure each word's right edge at build; the pill's width = revealed extent + padding, eased over 120 ms).

- [ ] Steps: failing test (a 120 ms one-word chunk merges; never exceeds the cap) → implement → PASS → snapshot check of a pill mid-line → Commit `techreel: captions never flash and the pill grows with the words`.

### Task 7: The full lint

**Files:** Modify `lint.py`, `recipe.py` (probe surfaces warnings), `stages.py` (finding paths); Test `tests/test_techreel_lint.py`.

**Interfaces:** `check(inputs, bound_provider=None)` adds (paths as before; severities per spec §9):
- `svg` (error, rule 5): `assets.check_svgs` failures as findings; `image-local` (error): a non-URL `src` when `allow_local` is False — `check(..., allow_local: bool = True)` new keyword.
- `hook-slow` (warning, 7): first line's estimate > 3500 ms, or beat 0 has no visual.
- `beat-long` (warning, 8): a beat with > 4 lines or estimated > 9000 ms; `static` (warning, 8): within a beat, a stretch > 3500 ms (estimated) with no visual entering (use `schedule.estimate` + `cue_ms` on estimated unit times from `words.unit_times([], …)`).
- `structure` (warning, 9): first beat not `hook`; last beat not `cta`; no `honest` beat.
- `crowded` (warning, 11): more than 4 non-hero visuals live at once (by estimated enter/exit).
- `length` (warning, 12): estimated total outside 30 000–150 000 ms.
- `text-mismatch` (warning, 13): `len(text)/len(say)` outside [0.4, 2.5] (only when `text` is set).
- `hindi-latin` (warning, 14): `language` primary `hi` and fewer than 50 % of `say`'s letters are Devanagari (U+0900–U+097F).
- `no-sources` (warning, 15): `sources` empty.
- `active-tab` (warning): `WindowVisual.active` outside `tabs` (from Plan 1 review).

- [ ] Steps: one failing + one passing test per new rule → implement → PASS → EXAMPLE stays error-free (it may warn on `structure`/`length` — then adjust EXAMPLE to be clean: hook first, cta last, an honest beat, ≥ 30 s) → Commit `techreel: the full lint`.

### Task 8: The guide and the worked examples

**Files:** Create `reelmachine/recipes/techreel/guide.py`, `reelmachine/recipes/techreel/examples/hi-pocket-llm.json`, `en-what-is-rag.json`, `ja-tiny-models.json`; Modify `recipe.py` (`guide=GUIDE`, `EXAMPLE` loaded from the Hindi example file), `pyproject.toml` (package data `recipes/techreel/examples/*.json`); Test `tests/test_techreel_examples.py`.

**Interfaces:** `GUIDE: dict[str, Any]` with keys (all plain strings or lists of strings, written for an agent, following spec §10 verbatim in substance):
- `workflow` (research → outline → lines → visuals → probe → storyboard → full),
- `research` (rules: primary sources, verify numbers the same day, never invent, `sources`),
- `formula` (the 7 parts with target seconds: hook ≤ 3 s, pain 6–12 s, reveal 5–8 s, explain 15–30 s, features 15–30 s, honest 6–10 s, cta 5–8 s),
- `humour` (rules + 12 example jokes across hi/en/ja, safe-humour rule),
- `visuals` (rules + a table "analogy → component" e.g. "most people idle" → `people`, "pipeline" → `nodes flow`, "permission" → `dialog`, "crash/overload" → `error`, "bills" → `receipts`, "benchmark" → `bars`, "A vs B" → `versus`),
- `language` (say/text, digits table per language: en/hi/ja for 0–10, hundreds, thousands, "B"/"K" units, model-name spellings like GPT-4o, H100, Llama 3.1; Supertonic voices F1–F5/M1–M5; Japanese chunking),
- `components` (generated from the visual models' JSON schemas: kind → required/optional fields with one example each — build it at import from `models.Visual` so it never drifts),
- `heroes` (kinds and states),
- `cta` (patterns per language),
- `examples` (the three example file names and what each demonstrates).
- Examples: all three lint-clean (no errors; warnings allowed only `no-sources` none — include sources), use original fictional or generic-concept content ("What is RAG?" may reference the concept with no brand claims), 45–90 s estimated, exercising together every component kind and at least four hero kinds.

- [ ] **Step 1: Failing tests** — every example validates, has zero lint errors and zero warnings; together they use every `Visual` kind; `GUIDE["components"]` lists every kind in `Visual`; `describe_recipe("techreel")` (through the MCP tools module as `tests/test_dialoguereel_mcp.py` does) returns the guide.
- [ ] **Step 2–4:** FAIL → write guide + examples → PASS.
- [ ] **Step 5:** storyboard each example with the real renderer (fake voice) and LOOK; fix anything ugly in the examples (not the runtime — runtime issues get noted for Plan 3).
- [ ] **Step 6:** Commit `techreel: the agent guide and three worked examples`.
