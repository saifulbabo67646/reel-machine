# dialoguereel Plan 1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working `dialoguereel` recipe that turns a two-character conversation into a reference-style vertical reel (hook card, two code-drawn SVG characters that hop, blink and flap their mouths, a speech bubble with ruby text, karaoke, reading, delayed meaning, a two-line history, a progress bar), plus `make_cast` on the MCP server for built-in and agent-written SVG characters.

**Architecture:** A new recipe package `reelmachine/recipes/dialoguereel/` following mapreel's shape: pydantic inputs, stages `script → narration → compose → render`, and one HyperFrames HTML project for the whole reel. Characters are inline SVGs in that page; a small DOM runtime (`runtime/main.mjs`) applies state computed by pure, Node-tested functions (`runtime/pure.mjs`) at every `hf-seek`. Casts are content-hashed and stored per caller under `jobs/<caller>/casts/`.

**Tech Stack:** Python 3.11, pydantic v2, stdlib `xml.etree.ElementTree`, ffmpeg, HyperFrames 0.8.146 (Node ≥ 22), `node --test` for the runtime, pytest (`-m slow` for anything touching ffmpeg).

**Spec:** `docs/superpowers/specs/2026-10-18-dialoguereel-design.md` (read §1, §3, §4.1–4.2, §4.4, §5, §6, §7, §8 rules 1–3). Plans 2 (learning and retention) and 3 (polish, image characters) are written after this one ships.

## Global Constraints

- Output video: 1080×1920, 30 fps, H.264 + AAC (reuse `explainer3d.video.mux`).
- Exactly two characters per cast; their `side`s differ (`left`, `right`).
- SVG contract: root `viewBox="0 0 400 400"`; required ids `body`, `eyes-open`, `eyes-closed`, `mouth-closed`, `mouth-open`; optional ids `mouth-smile`, `eyes-happy`, `eyes-surprised`, `blush`, `arm-left`, `arm-right`, `sweat`, `sparkle`; ≤ 60 000 bytes.
- SVG safety: refuse `<!DOCTYPE`/`<!ENTITY`, `script`, `foreignObject`, `iframe`, `object`, `embed`, `audio`, `video`, `animate`, `animateTransform`, `animateMotion`, `set`; any `on*` attribute; `href`/`xlink:href` not starting with `#` (except `<image>` with `data:image/png;base64,` or `data:image/jpeg;base64,`); `@import`, `javascript:` or `url(` not followed by `#` in a `style` attribute or `<style>`. The error names the element. *(Deviation from spec §4.1: stdlib ElementTree after refusing DOCTYPE/ENTITY instead of adding `defusedxml`; same protection, no new dependency.)*
- Ruby markup: `漢字{かんじ}` attaches to the kanji run (`[㐀-䶿一-鿿々〆ヶ]+`) just before `{`.
- Timeline constants: hook 1500 ms; gap between lines 350 ms; tail 900 ms; meaning fades in at 60 % of a line's speech over 250 ms.
- Lint errors (plan 1): speaker not in cast; fewer than 3 or more than 12 lines (`mode != "cast"`); empty `title`. Errors fail `full` mode unless `lint: "warn"`; in `storyboard` mode they are warnings.
- No network in the default test suite; tests that run ffmpeg/the engine are `pytestmark = pytest.mark.slow`.
- Casts are private to their caller; `cast_id` matches `^cast_[0-9a-f]{12}$` and is never used as a path before matching it.
- Voices: every character's `voice` must name the narration provider the deployment serves (same check as mapreel's `voice` probe check).

## Review Focus

1. **Agent SVG that is hostile or sloppy** (event handlers, external hrefs, an `<image>` pointing at `file://`, duplicate ids, missing required groups) — expect a clear `InvalidInput` naming the element, never a render. Tests in Task 2.
2. **Two characters whose SVGs use the same ids or gradient names** (both built-ins define `#body`, `#shade`) — expect each character's groups and `url(#…)` refs to stay its own once inlined in one page. Test in Task 2 (`scope_ids`) and Task 6 (document has distinct ids).
3. **A `cast_id` that doesn't exist or looks like a path** (`../x`, `cast_zzz`) — expect `InvalidInput` with hint "call make_cast first", nothing read outside `casts/`. Test in Task 4.
4. **A line with no spaces** (Japanese) gets one word span from the voice — karaoke must still advance character by character and the mouth must still flap. Test in Task 6 (`pure.test.mjs`).
5. **Malformed ruby** (`{よみ}` with no kanji before it, `漢字{`, `{}`) — expect the braces shown as literal text plus a warning, never a crash. Test in Task 3.

---

## File Structure

```
reelmachine/recipes/dialoguereel/
  __init__.py          exports DialogueReelInputs, DialogueReelRecipe
  models.py            inputs (script, cast, characters) and stage outputs
  svg.py               SVG contract + safety check, id scoping, recolouring
  library.py           built-in characters: names, descriptions, load
  characters/index.json, cupcake.svg, mug.svg
  ruby.py              ruby markup → runs
  cast.py              cast id, resolve, save/load per caller
  schedule.py          line windows from speech lengths
  lint.py              Finding + plan-1 rules
  guide.py             GUIDE dict for describe_recipe (workflow + SVG contract)
  compose.py           reel/cast documents, HyperFrames project writer
  runtime/main.mjs     DOM runtime (builds once, applies state on hf-seek)
  runtime/pure.mjs     pure state functions
  runtime/reel.css     layout and look
  runtime/tests/pure.test.mjs
  stages.py            ScriptStage, NarrationStage, ComposeStage, RenderStage
  recipe.py            DialogueReelRecipe (spec, probe, verify=None)
reelmachine/jobs/local.py, memory.py   + caller_dir(caller)
reelmachine/mcp/tools.py, server.py    + make_cast
pyproject.toml                          + entry point
.github/workflows/ci.yml                + node --test for the runtime
tests/test_dialoguereel_*.py
```

---

### Task 1: Inputs model and recipe skeleton

**Files:**
- Create: `reelmachine/recipes/dialoguereel/__init__.py`, `models.py`, `recipe.py` (skeleton)
- Modify: `pyproject.toml` (entry point under the recipes group, next to `mapreel = …`)
- Test: `tests/test_dialoguereel_models.py`

**Interfaces:**
- Produces (in `models.py`):
  - `Look = Literal["pop"]`, `Mode = Literal["cast", "storyboard", "full"]`
  - `BuiltinSource(kind: Literal["builtin"], name: str, colors: dict[Literal["primary","secondary","accent"], str] = {})` — colours `#rrggbb`
  - `SvgSource(kind: Literal["svg"], svg: str (max_length 60_000))`
  - `CharacterSource = Annotated[BuiltinSource | SvgSource, Field(discriminator="kind")]`
  - `Character(id: str  ^[a-z][a-z0-9_-]{0,23}$, name: str 1..40, voice: str = "fake:default", side: Literal["left","right"], source: CharacterSource)`
  - `CastSpec(characters: list[Character])` — validator: exactly 2, distinct ids, distinct sides
  - `Line(speaker: str 1..24, text: str 1..80, reading: str|None ≤120, meaning: str 1..120)`
  - `DialogueReelInputs(title: str = "" ≤60, language: str = "ja", meaning_language: str = "en", level: str = "" ≤16, scene: str|None ≤8, cast: str | CastSpec, look: Look = "pop", mode: Mode = "storyboard", lint: Literal["strict","warn"] = "strict", music: str|None = None, name: str|None, outdir: str|None, lines: list[Line] max 30, default [])`
  - All models `ConfigDict(extra="forbid")`.
- Produces (in `recipe.py`): `DialogueReelRecipe` with `spec.id == "dialoguereel"`, `input_model=DialogueReelInputs`, stages empty for now, `probe` returning `ProbeReport(ok=True)`; `verify` returns `None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_two_character_script_validates():
    inputs = DialogueReelInputs.model_validate(SCRIPT)  # module-level SCRIPT: cupcake left, mug right, 3 lines
    assert inputs.cast.characters[0].source.kind == "builtin" and inputs.mode == "storyboard"

@pytest.mark.parametrize("cast", [ONE_CHARACTER, SAME_SIDE, SAME_ID])
def test_a_cast_needs_two_distinct_characters_on_both_sides(cast):
    with pytest.raises(ValidationError):
        DialogueReelInputs.model_validate({**SCRIPT, "cast": cast})

def test_a_cast_id_string_is_accepted_as_is():
    assert DialogueReelInputs.model_validate({**SCRIPT, "cast": "cast_0123456789ab"}).cast == "cast_0123456789ab"

def test_colours_must_be_hex():
    with pytest.raises(ValidationError):
        BuiltinSource(kind="builtin", name="mug", colors={"primary": "blue"})

def test_the_recipe_is_registered():
    from reelmachine.core.registry import Registry
    assert Registry().find_recipe("dialoguereel") is not None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_dialoguereel_models.py -v` — Expected: FAIL (module not found).
- [ ] **Step 3: Implement** `models.py`, the skeleton `recipe.py`, `__init__.py`, and the entry point `dialoguereel = "reelmachine.recipes.dialoguereel.recipe:DialogueReelRecipe"`; run `uv sync` so the entry point is installed.
- [ ] **Step 4: Run** the tests — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "dialoguereel: inputs model and recipe skeleton"`

---

### Task 2: SVG contract, safety and the two first built-ins

**Files:**
- Create: `reelmachine/recipes/dialoguereel/svg.py`, `library.py`, `characters/index.json`, `characters/cupcake.svg`, `characters/mug.svg`
- Test: `tests/test_dialoguereel_svg.py`

**Interfaces:**
- Produces (`svg.py`):
  - `REQUIRED: tuple[str, ...]`, `OPTIONAL: tuple[str, ...]`, `MAX_BYTES = 60_000` (values in Global Constraints)
  - `check_svg(svg: str, *, path: str) -> list[str]` — returns the optional ids present; raises `InvalidInput(message, hint, details={"path": path, "element": <tag or attr>})`
  - `scope_ids(svg: str, prefix: str) -> str` — every `id="x"` → `id="{prefix}-x"`, every `url(#x)` → `url(#{prefix}-x)`, every `href="#x"`/`xlink:href="#x"` → `#{prefix}-x`
  - `recolour(svg: str, colors: Mapping[str, str]) -> str` — sets `--primary`/`--secondary`/`--accent` in the root element's `style`
- Produces (`library.py`): `builtin_names() -> list[dict]` (`name`, `description`, `role` from `index.json`), `load_builtin(name: str, colors: Mapping[str, str]) -> str` (raises `InvalidInput` listing the names on an unknown one).

**The built-ins** are hand-drawn to the house style (spec §4.1): kawaii sticker, ~14 px white outer stroke, 4 px dark (`#3a2a3a`) inner line, flat pastel fills via `var(--primary, …)` etc., one soft shade, dot eyes with a white glint, pink blush ovals. Cupcake: pink swirl frosting (`--primary`), orange-tan cup top, mint pleated wrapper (`--secondary`). Mug: pale blue body (`--primary`), handle on the right, darker rim. Both include every optional group; `arm-left`/`arm-right` carry `data-pivot`. Check them by eye: render each in a browser (`open` an HTML file in the scratchpad that inlines both) before committing.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("name", ["cupcake", "mug"])
def test_each_builtin_meets_the_contract_and_has_every_optional_group(name):
    assert set(check_svg(load_builtin(name, {}), path="x")) == set(OPTIONAL)

@pytest.mark.parametrize("bad, element", [
    ('<script>alert(1)</script>', "script"), ('<g onload="x()"/>', "onload"),
    ('<use href="https://e.com/a.svg#x"/>', "href"), ('<image href="file:///etc/passwd"/>', "image"),
    ('<foreignObject/>', "foreignObject"), ('<style>@import url(https://e.com/x.css);</style>', "style"),
    ('<animate attributeName="href"/>', "animate"),
])
def test_hostile_svg_is_refused_naming_the_element(bad, element):
    with pytest.raises(InvalidInput) as err:
        check_svg(minimal_svg(extra=bad), path="cast.characters[0].source.svg")
    assert err.value.details["element"] == element and err.value.details["path"].endswith("svg")

def test_doctype_and_entities_are_refused(): ...          # '<!DOCTYPE svg [<!ENTITY a "b">]>' + minimal_svg()
def test_missing_required_group_is_named(): ...           # drop mouth-open → message contains "mouth-open"
def test_wrong_viewbox_and_oversize_are_refused(): ...    # viewBox="0 0 100 100"; "<!--" + "x"*60_001 + "-->"
def test_duplicate_ids_are_refused(): ...
def test_a_data_png_image_is_allowed(): ...

def test_scope_ids_keeps_two_characters_apart():
    a, b = scope_ids(load_builtin("cupcake", {}), "kid"), scope_ids(load_builtin("mug", {}), "mom")
    ids = re.findall(r'id="([^"]+)"', a + b)
    assert len(ids) == len(set(ids)) and 'id="kid-mouth-open"' in a
    assert all(ref.startswith("kid-") for ref in re.findall(r"url\(#([^)]+)\)", a))

def test_recolour_sets_the_css_variables():
    assert "--primary:#123456" in recolour(load_builtin("mug", {}), {"primary": "#123456"})
```

`minimal_svg(extra="")` is a test helper producing a contract-valid 400×400 SVG with the five required groups plus `extra`.

- [ ] **Step 2: Run** `uv run pytest tests/test_dialoguereel_svg.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `svg.py` (textual DOCTYPE/ENTITY and size check first, then `ElementTree.fromstring`, walk every element comparing local names, case-sensitively for tags as listed) and `library.py`; draw the two SVGs and `index.json`.
- [ ] **Step 4: Run** the tests — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "dialoguereel: SVG character contract, safety check, cupcake and mug"`

---

### Task 3: Ruby markup

**Files:**
- Create: `reelmachine/recipes/dialoguereel/ruby.py`
- Test: `tests/test_dialoguereel_ruby.py`

**Interfaces:**
- Produces: `Run(text: str, ruby: str | None)` (frozen dataclass); `parse_ruby(text: str) -> tuple[list[Run], list[str]]` (runs, problems); `plain_text(runs: Sequence[Run]) -> str` (what the voice reads: the runs' text joined, no readings).

- [ ] **Step 1: Write the failing tests**

```python
def test_a_reading_attaches_to_the_kanji_run_before_it():
    runs, problems = parse_ruby("手伝{てつだ}いましょうか。")
    assert runs == [Run("手伝", "てつだ"), Run("いましょうか。", None)] and problems == []

def test_two_readings_in_one_line():
    runs, _ = parse_ruby("今日{きょう}は雨{あめ}です")
    assert [r.ruby for r in runs] == ["きょう", None, "あめ", None]

def test_text_without_braces_is_one_plain_run():
    assert parse_ruby("¿Puedo ayudar?") == ([Run("¿Puedo ayudar?", None)], [])

@pytest.mark.parametrize("text", ["{よみ}です", "漢字{", "漢字{}です", "かな{かな}"])
def test_malformed_braces_stay_literal_with_a_problem(text):
    runs, problems = parse_ruby(text)
    assert "".join(r.text for r in runs) == text and problems

def test_plain_text_drops_readings():
    assert plain_text(parse_ruby("手伝{てつだ}う")[0]) == "手伝う"
```

- [ ] **Step 2: Run** — Expected: FAIL. **Step 3: Implement** with one regex scan; anything that is not `KANJI_RUN{non-empty, no braces}` is literal text. **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "dialoguereel: ruby markup for furigana"`

---

### Task 4: Casts — id, resolve, save and load per caller

**Files:**
- Create: `reelmachine/recipes/dialoguereel/cast.py`
- Modify: `reelmachine/jobs/local.py` (`caller_dir(self, caller: str) -> Path`, public twin of `_caller_dir`), `reelmachine/jobs/memory.py` (`caller_dir` → `self._root / caller`)
- Test: `tests/test_dialoguereel_cast.py`

**Interfaces:**
- Consumes: `CastSpec`, `Character` (Task 1); `check_svg`, `load_builtin`, `recolour` (Task 2).
- Produces:
  - `ResolvedCharacter(id, name, voice, side, svg: str, parts: list[str])` (pydantic, `extra="forbid"`) — `svg` is recoloured but not yet scoped
  - `ResolvedCast(id: str, characters: list[ResolvedCharacter])`
  - `CAST_ID = re.compile(r"^cast_[0-9a-f]{12}$")`
  - `cast_id(spec: CastSpec) -> str` — `"cast_" + sha256(json.dumps([ {id,name,side,source} … ], sort_keys=True))[:12]`; voices are **not** hashed
  - `resolve(spec: CastSpec, *, path: str = "cast") -> ResolvedCast` — errors carry `path` like `cast.characters[1].source.svg`
  - `save(root: Path, spec: CastSpec) -> str` — writes `root/"casts"/f"{id}.json"` (the full spec incl. voices) atomically; returns the id
  - `load(root: Path, ident: str) -> CastSpec` — `InvalidInput(details={"path": "cast"}, hint="call make_cast first, or pass the cast inline")` when the id doesn't match `CAST_ID` or the file is missing
  - `caller_root(ctx: StageContext) -> Path` — `settings.workdir.parent` when `settings.workdir.name == ctx.job.id` (as `explainer3d.stages._caller_cache`), else `settings.cache_dir`

- [ ] **Step 1: Write the failing tests**

```python
def test_the_id_ignores_voices_but_not_looks():
    a = cast_id(SPEC); b = cast_id(with_voice(SPEC, "elevenlabs:x")); c = cast_id(with_colour(SPEC, "#000000"))
    assert a == b != c and CAST_ID.match(a)

def test_save_then_load_round_trips_with_voices(tmp_path):
    ident = save(tmp_path, with_voice(SPEC, "elevenlabs:x"))
    assert load(tmp_path, ident).characters[0].voice == "elevenlabs:x"

@pytest.mark.parametrize("ident", ["cast_000000000000", "../casts/x", "cast_ZZZ", ""])
def test_unknown_or_pathlike_ids_are_refused(tmp_path, ident):
    with pytest.raises(InvalidInput) as err:
        load(tmp_path, ident)
    assert "make_cast" in err.value.hint

def test_resolve_names_the_character_whose_svg_fails():
    with pytest.raises(InvalidInput) as err:
        resolve(with_svg(SPEC, 1, "<svg/>"))
    assert err.value.details["path"] == "cast.characters[1].source.svg"

def test_local_store_exposes_the_caller_dir(tmp_path):
    from reelmachine.jobs.local import LocalJobStore
    assert LocalJobStore(tmp_path).caller_dir("alice") == tmp_path / "alice"
```

- [ ] **Step 2: Run** — Expected: FAIL. **Step 3: Implement.** **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "dialoguereel: casts are hashed, resolved and kept per caller"`

---

### Task 5: Schedule and the plan-1 lint

**Files:**
- Create: `reelmachine/recipes/dialoguereel/schedule.py`, `lint.py`
- Test: `tests/test_dialoguereel_schedule.py`, `tests/test_dialoguereel_lint.py`

**Interfaces:**
- Consumes: `estimate_speech_ms(text, language)` from `reelmachine/recipes/explainer3d/timing.py`; `plain_text`, `parse_ruby` (Task 3).
- Produces (`schedule.py`): `HOOK_MS=1500`, `GAP_MS=350`, `TAIL_MS=900`, `MEANING_AT=0.6`; frozen dataclasses `LineWindow(start_ms, speech_ms, end_ms, meaning_ms)`, `Schedule(hook_ms, lines: tuple[LineWindow, ...], duration_ms)`; `schedule(speech_ms: Sequence[int]) -> Schedule`; `estimate(inputs: DialogueReelInputs) -> Schedule`.
- Produces (`lint.py`): `Finding(rule, severity: Literal["error","warning"], message, hint, line: int | None = None)` (dataclass); `check(inputs: DialogueReelInputs, speakers: set[str]) -> list[Finding]` with rules `unknown-speaker`, `line-count`, `no-title` (errors) and `ruby` (warning, one per problem from `parse_ruby`). `line-count` is skipped when `inputs.mode == "cast"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_lines_follow_the_hook_with_gaps():
    s = schedule([1000, 2000])
    assert [(w.start_ms, w.end_ms, w.meaning_ms) for w in s.lines] == [(1500, 2500, 2100), (2850, 4850, 4050)]
    assert s.duration_ms == 5750

def test_no_lines_is_just_hook_and_tail():
    assert schedule([]).duration_ms == 1500 + 900

def test_unknown_speaker_is_an_error_naming_the_line():
    found = check(script(lines=[line("dad")] * 3), {"kid", "mom"})
    assert [(f.rule, f.severity, f.line) for f in found][:1] == [("unknown-speaker", "error", 0)]

@pytest.mark.parametrize("count, ok", [(2, False), (3, True), (12, True), (13, False)])
def test_line_count(count, ok): ...

def test_an_empty_title_is_an_error(): ...
def test_cast_mode_needs_no_lines(): ...
def test_bad_ruby_is_a_warning(): ...
```

- [ ] **Step 2: Run** — Expected: FAIL. **Step 3: Implement.** **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "dialoguereel: line schedule and the first lint rules"`

---

### Task 6: The runtime and the HyperFrames project

This is the look of the reel. Build it in a browser first: write the document for the fixture script, open `index.html` directly (it renders the frame at `?t=<seconds>` when not inside HyperFrames), and tune spacing, sizes and motion by eye at a dozen timestamps before writing the snapshot test.

**Files:**
- Create: `reelmachine/recipes/dialoguereel/compose.py`, `runtime/main.mjs`, `runtime/pure.mjs`, `runtime/reel.css`, `runtime/tests/pure.test.mjs`
- Modify: `.github/workflows/ci.yml` (add `node --test "reelmachine/recipes/dialoguereel/runtime/tests/*.test.mjs"` next to mapreel's line)
- Test: `tests/test_dialoguereel_compose.py`

**Interfaces:**
- Consumes: `ResolvedCast` (Task 4), `Schedule` (Task 5), `parse_ruby` (Task 3), `scope_ids` (Task 2), `WordSpan` (`reelmachine/core/timeline.py`).
- Produces (`compose.py`):
  - `WIDTH, HEIGHT, FPS = 1080, 1920, 30`; `LOOKS = {"pop": {"bg": "#4b2bff", "text": "#ffffff", "reading": "#7fd3ff", "meaning": "#9fe0ff", "accent": "#ffe066", "bubble": "rgba(255,255,255,0.10)"}}`
  - `reel_document(inputs: DialogueReelInputs, cast: ResolvedCast, plan: Schedule, words: list[list[WordSpan]]) -> dict` — keys: `mode: "reel"`, `width`, `height`, `duration` (s), `look` (the colour dict), `title`, `level`, `scene`, `language`, `hookMs`, `characters: [{id, side, parts}]`, `lines: [{speaker, runs: [{t, r}], reading, meaning, startMs, endMs, meaningMs, words: [{text, startMs, endMs}]}]` (word times in reel ms)
  - `cast_document(cast: ResolvedCast) -> dict` — `mode: "cast"`, duration 2 s, the characters only
  - `write_project(doc: dict, cast: ResolvedCast, dest: Path) -> Path` — writes `index.html` (template below) with each character's `scope_ids(svg, char.id)` inlined in `<div class="char {side}" id="char-{id}">`, copies `runtime/*.mjs` and `reel.css`
  - `project_key(doc: dict, cast: ResolvedCast) -> str` — sha256 of doc JSON, the SVGs, the runtime files and the template
- Produces (`pure.mjs`, all pure, times in ms):
  - `activeLine(lines, ms) -> number` — index of the last line whose `startMs <= ms`, else `-1`
  - `dropIn(ms, delayMs) -> {y, scale}` — characters rise 500 px → 0 over 450 ms with `backOut`; `delayMs` 150 left, 300 right
  - `hop(ms, startMs) -> {y, sx, sy}` — 280 ms squash-and-stretch hop (−36 px peak) at a line's start
  - `mouthOpen(ms, words) -> boolean` — inside a word span, open on even 90 ms ticks
  - `blinking(ms, seed) -> boolean` — closed for 120 ms at deterministic intervals of 2000–5000 ms from `seed`
  - `litChars(ms, total, words) -> number` — characters lit, spread over the spans in proportion to each span's `text.length`; one span → linear
  - `fade(ms, atMs, durMs = 250) -> number` (0..1)
  - `history(lines, index) -> number[]` — up to two previous line indices, oldest first
  - `progress(ms, durationMs) -> number`
- `main.mjs`: builds the DOM once from `#reel-data` (hook card, level badge, bubble with `<ruby>` per run, reading, meaning, history rows, progress bar), then on `hf-seek` sets transforms/opacity/classes from the pure functions: the active speaker's `char` element hops; its `#{id}-mouth-open` shows while `mouthOpen`, else `#{id}-mouth-closed`; `#{id}-eyes-closed` replaces `eyes-open` while `blinking`; both characters idle-bob (±6 px, 1.6 s, opposite phase); the listener tilts 4° toward the speaker; the bubble's tail sits on the speaker's side. Errors set `window.__hfStartupError` as mapreel's `main.mjs` does.
- Layout: level badge top-left (64, 96); hook title centred at y≈760, 92 px bold; bubble spans x 72–1008 from y 300, history rows above it at 70 % size and 45 % opacity; characters 440 px square, bottoms at y 1680, centres x 290 / 790; progress bar 8 px at y 0. Fonts: `"Noto Sans JP","Hiragino Sans","Yu Gothic","Noto Sans KR","Apple SD Gothic Neo",system-ui,sans-serif` (bundling fonts is Plan 3).
- Template: copy mapreel's `_TEMPLATE` conventions (`data-composition-id`, `data-duration`, `window.__hf.buildReady`, `<script id="reel-data" type="application/json">` with `</` escaped).

- [ ] **Step 1: Write the failing Node tests** in `runtime/tests/pure.test.mjs`:

```js
test("a line is active from its start until the next starts", () => {
  const lines = [{ startMs: 1500 }, { startMs: 2850 }];
  assert.deepEqual([1000, 1500, 2849, 2850, 9000].map((t) => activeLine(lines, t)), [-1, 0, 0, 1, 1]);
});
test("one long span still lights characters one by one", () => {
  const words = [{ text: "てつだいましょうか。", startMs: 0, endMs: 1000 }];
  assert.equal(litChars(0, 10, words), 0); assert.equal(litChars(500, 10, words), 5); assert.equal(litChars(1000, 10, words), 10);
});
test("the mouth flaps only while a word is spoken", () => {
  const words = [{ text: "a", startMs: 100, endMs: 400 }];
  assert.equal(mouthOpen(50, words), false); assert.equal(mouthOpen(100, words), true); assert.equal(mouthOpen(190, words), false);
});
test("blinks are deterministic, brief and spaced", () => { /* same seed → same frames; closed runs ≤ 120 ms; gaps ≥ 2000 ms over 20 s */ });
test("a hop returns to rest", () => { assert.deepEqual(hop(280, 0), { y: 0, sx: 1, sy: 1 }); });
test("history keeps the two lines before the active one", () => { assert.deepEqual(history([{}, {}, {}, {}], 3), [1, 2]); });
```

- [ ] **Step 2: Run** `node --test "reelmachine/recipes/dialoguereel/runtime/tests/*.test.mjs"` — Expected: FAIL.
- [ ] **Step 3: Implement `pure.mjs`.** **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Write the failing Python tests** (`tests/test_dialoguereel_compose.py`):

```python
def test_the_document_carries_reel_times_and_ruby_runs():
    doc = reel_document(INPUTS, CAST, schedule([1000, 1200, 900]), WORDS)
    assert doc["lines"][1]["startMs"] == 2850 and doc["lines"][0]["runs"][0] == {"t": "手伝", "r": "てつだ"}
    assert doc["lines"][0]["words"][0]["startMs"] >= 1500

def test_the_project_inlines_both_characters_with_their_own_ids(tmp_path):
    html = (write_project(reel_document(...), CAST, tmp_path / "p") / "index.html").read_text()
    assert 'id="kid-mouth-open"' in html and 'id="mom-mouth-open"' in html and "<\\/" not in html.split("reel-data")[0]

def test_project_key_changes_with_the_cast_and_the_runtime(): ...
```

- [ ] **Step 6: Run** — Expected: FAIL. **Step 7: Implement** `compose.py`, `main.mjs`, `reel.css`; tune in the browser. **Step 8: Run** both suites — Expected: PASS.
- [ ] **Step 9: Commit** — `git commit -m "dialoguereel: the reel runtime and its HyperFrames project"`

---

### Task 7: Stages, probe and the engine run

**Files:**
- Create: `reelmachine/recipes/dialoguereel/stages.py`
- Modify: `reelmachine/recipes/dialoguereel/models.py` (stage outputs), `recipe.py` (stages, providers, probe, example)
- Test: `tests/test_dialoguereel_probe.py` (fast), `tests/test_dialoguereel_engine.py` (`pytestmark = pytest.mark.slow`)

**Interfaces:**
- Consumes: everything above; `speak_line` (`explainer3d/stages.py`), `pad_speech`, `silence`, `mix`, `synth_sfx` (`explainer3d/audio.py`), `mux` (`explainer3d/video.py`), `ffmpeg.concat_audio`, `write_srt` (`render/captions.py`), `provider_for` (`doodle/narration`).
- Produces (stage outputs in `models.py`, each a `StageOutput`):
  - `ScriptOutput(inputs, cast: ResolvedCast, lint: list[dict], warnings: list[str])`
  - `NarrationOutput(script: ScriptOutput, audio_asset: str = "", duration_ms: int, lines: list[LineWindowModel], words: list[list[WordSpan]], provider: str)` — `LineWindowModel` mirrors `LineWindow`
  - `ComposeOutput(narration: NarrationOutput, project: str, key: str)`
  - `RenderOutput(video: str, srt: str, manifest: str, duration_ms: int, render_mode: str = "hyperframes")` — same field names as mapreel so the executor collects `reel.mp4`, `captions.srt`, `reel.json`
- Stages:
  1. `ScriptStage` (`id="script"`): validate; `cast.load(caller_root(ctx), …)` for a string, else `cast.save(caller_root(ctx), spec)` so an inline cast becomes reusable; `cast.resolve`; lint — errors raise `InvalidInput(stage="script", details={"path": f"lines[{f.line}].speaker" | "lines" | "title", "rule": f.rule})` only when `mode == "full"` and `lint == "strict"`.
  2. `NarrationStage` (`uses=("narration",)`): skipped (`lines=[]`, silent asset not needed) in `cast` mode. Otherwise, for each line, the speaker's voice must satisfy `provider_for(voice) == provider.name` (else `VoiceUnavailable`, wording as mapreel's); `speak_line(ctx, provider, plain_text(runs), voice, inputs.language)`; `schedule(speech_ms)`; track = `silence(HOOK_MS)` + each line `pad_speech(lead_ms=0, total_ms=speech + GAP_MS)` (the last: `speech + TAIL_MS`), joined with `ffmpeg.concat_audio`, stored as a NARRATION asset; word spans shifted to reel time.
  3. `ComposeStage`: `cast_document` in cast mode, else `reel_document`; `write_project` into `ctx.workdir/"project"`; writes `artifacts/script.resolved.json` (`{"cast": id, "lines": [{speaker, startMs, endMs, meaningMs, words}], "warnings": […]}`).
  4. `RenderStage` (`uses=("renderer",)`): renderer `missing()` → `RendererUnavailable` as mapreel's `PrepStage`. **cast** mode: `renderer.snapshot(project, 1.0, artifacts/"cast.png")`, return with `halt_pipeline=True` and no `RenderOutput` (return a `StageOutput` subclass `SheetOutput(path: str, halt_pipeline=True)`). **storyboard**: snapshots at `hookMs*0.8/1000` and at each line's `meaningMs+300`, tiled into `artifacts/storyboard.jpg` with the same ffmpeg `tile` recipe as mapreel's `PrepStage._storyboard`; halt. **full**: `renderer.render(project, work/"video.mp4", fps=FPS, seconds=duration)`; SFX cues `pop` at `150` and `300` ms (the drop-ins) via `synth_sfx(..., designed=True)`; `mix(..., profile="studio")`; `mux` to `outdir/f"{name}.mp4"`; `write_srt` with one `Caption` per line (`text = plain_text + "\n" + meaning`); `reel.json` manifest `{reel, recipe: "dialoguereel", mode, created, durationMs, cast, look, renderer, credits: [voices…]}`.
- `DialogueReelRecipe.spec`: `providers={"narration": ProviderReq(group="narration", default="fake", …), "renderer": ProviderReq(group="renderers", default="hyperframes", …)}`, `cost=CostNote(unit="tts_characters", …, providers=("elevenlabs","cartesia","macos","fake"))`, `artifacts=("reel.mp4","captions.srt","reel.json","storyboard.jpg","cast.png","script.resolved.json")`, `guide=GUIDE` (Task 8), `example=` the reference dialogue (spec §8 fixture) with built-in `cupcake` (kid, left) and `mug` (mom, right), `mode: "storyboard"`, `title: "Helping Mom at home"`, `level: "N5"`, `scene: "🧺"`.
- `probe`: checks `script` (validation + cast resolve; on `InvalidInput` report its path like mapreel), `lint` (errors fail the check in full/strict), `renderer` (`missing()`), `voice` (every character's provider equals the bound one); `details` = `{"estimatedMs", "lines": [{index, estimatedMs}], "lint": [...], "cast": cast_id, "cost": {"ttsCharacters"}}`. `probe` must not write a cast to disk.

- [ ] **Step 1: Write the failing fast tests** (`test_dialoguereel_probe.py`, using `StaticProviders` with `FakeNarration` and `FakeShotRenderer`):

```python
def test_the_example_probes_clean():
    report = probe(EXAMPLE)
    assert report.ok and report.details["cast"].startswith("cast_") and report.details["estimatedMs"] > 1500

def test_an_unknown_speaker_fails_with_its_path():
    report = probe(with_line_speaker(EXAMPLE, 2, "dad") | {"mode": "full"})
    assert not report.ok and "lines[2].speaker" in report.details["lint"][0]["message"] + json.dumps(report.details)

def test_a_voice_from_another_provider_fails_the_voice_check(): ...
def test_hostile_svg_fails_the_script_check_with_its_path(): ...
def test_probe_writes_nothing(tmp_path): ...  # no casts/ dir appears
```

- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Write the failing slow tests** (`test_dialoguereel_engine.py`, mirroring `test_mapreel_engine.py`: `Engine(settings, provider_overrides={"renderer": "explainer3d-fake"})`, `tts="fake"`):

```python
def test_storyboard_makes_a_sheet(tmp_path):
    job = run(tmp_path, EXAMPLE)
    assert job.state == JobState.SUCCEEDED and "storyboard.jpg" in names(job) and "script.resolved.json" in names(job)

def test_full_makes_a_reel_with_sound_and_captions(tmp_path):
    job = run(tmp_path, EXAMPLE | {"mode": "full"})
    reel = artifact(job, "reel.mp4")
    probe = ffprobe(reel); assert (probe.width, probe.height) == (1080, 1920) and probe.has_audio
    assert abs(probe.duration_ms - resolved(job)["durationMs"]) < 100
    assert "Shall I help?" in artifact(job, "captions.srt").read_text()

def test_cast_mode_makes_a_sheet_and_an_inline_cast_is_saved(tmp_path):
    job = run(tmp_path, {"mode": "cast", "title": "", "cast": EXAMPLE["cast"], "lines": []})
    assert "cast.png" in names(job) and list((tmp_path / "work" / "jobs" / "local" / "casts").glob("cast_*.json"))

def test_a_strict_full_build_with_no_title_fails_at_script(tmp_path): ...
```

(`resolved(job)` reads `script.resolved.json`; add `durationMs` there.)

- [ ] **Step 4: Run** `uv run pytest tests/test_dialoguereel_probe.py tests/test_dialoguereel_engine.py -v` — Expected: FAIL.
- [ ] **Step 5: Implement** the stage outputs, `stages.py` and the recipe wiring.
- [ ] **Step 6: Run** both files — Expected: PASS. Run `uv run pytest -q` — Expected: the whole suite passes.
- [ ] **Step 7: Commit** — `git commit -m "dialoguereel: stages, probe and engine runs"`

---

### Task 8: `make_cast`, the guide, and a real render

**Files:**
- Create: `reelmachine/recipes/dialoguereel/guide.py`
- Modify: `reelmachine/mcp/tools.py` (`make_cast`), `reelmachine/mcp/server.py` (`DESCRIPTIONS["make_cast"]`, tool registration beside `find_images`), `docs/MCP.md` (one entry, same shape as `find_images`'s)
- Test: `tests/test_dialoguereel_mcp.py` (`pytest.importorskip("mcp")`), `tests/test_dialoguereel_render.py` (slow; skips like `test_mapreel_render.py` when Node/HyperFrames are missing)

**Interfaces:**
- Consumes: `CastSpec`, `cast.resolve/save/cast_id` (Task 4), `builtin_names` (Task 2), `McpTools.start_job` plumbing, `engine.store.caller_dir` (Task 4).
- Produces: `McpTools.make_cast(self, ctx, characters: list[dict], preview: bool = True) -> dict` →
  `{"schemaVersion": 1, "castId": "cast_…", "characters": [{"id", "name", "side", "voice", "parts": [...]}], "previewJobId": "<job id>" | None, "hint": "pass castId as the reel's \"cast\"; get_job(previewJobId) then get_artifact(…, \"cast.png\") to see the characters"}`.
  Validates with `CastSpec`/`resolve` (errors through `_guard` as structured payloads with the path), saves under `engine.store.caller_dir(caller)`, and when `preview` submits `dialoguereel` with `{"mode": "cast", "cast": castId, "lines": []}` for the caller. It spends nothing.
- `GUIDE` keys (plan 1): `workflow` (pick level and situation → `make_cast` (built-in or your own SVG) → look at `cast.png` → write lines → `probe` → storyboard → full), `builtins` (from `builtin_names()`), `characters` (the full contract from Global Constraints, the house style, and one complete minimal worked example SVG that passes `check_svg` — assert that in a test), `lines` (ruby markup with an example, `reading` for non-Latin scripts, keep lines short), `voices` (one provider per deployment; give each character a different voice id of that provider).
- `DESCRIPTIONS["make_cast"]`: "Make the two characters for a dialoguereel: built-ins by name (cupcake, mug, …) or your own SVG following describe_recipe('dialoguereel').guide.characters. Free and instant; returns a castId to reuse across reels and starts a preview job whose cast.png shows each character. Example: {\"characters\": [{\"id\": \"kid\", \"name\": \"Cupcake\", \"side\": \"left\", \"source\": {\"kind\": \"builtin\", \"name\": \"cupcake\"}}, {\"id\": \"mom\", \"name\": \"Mug\", \"side\": \"right\", \"source\": {\"kind\": \"builtin\", \"name\": \"mug\"}}]}"

- [ ] **Step 1: Write the failing tests**

```python
def test_make_cast_returns_a_reusable_id_and_a_preview_job(tmp_path):
    with _tools(tmp_path) as tools:
        out = tools.make_cast(None, CHARACTERS)
        again = tools.make_cast(None, CHARACTERS, preview=False)
    assert out["castId"] == again["castId"] and out["previewJobId"] and again["previewJobId"] is None
    assert {c["id"] for c in out["characters"]} == {"kid", "mom"}

def test_make_cast_reports_bad_svg_with_its_path(tmp_path):
    with _tools(tmp_path) as tools, pytest.raises(ToolError) as err:
        tools.make_cast(None, with_svg(CHARACTERS, 0, '<svg viewBox="0 0 400 400"><script/></svg>'))
    payload = json.loads(str(err.value)); assert payload["code"] == "INVALID_INPUT" and "characters[0]" in payload["details"]["path"]

def test_casts_are_private_to_their_caller(tmp_path): ...   # caller B gets InvalidInput loading A's castId via start_job/probe
def test_the_guide_example_svg_passes_the_contract(): ...    # check_svg(GUIDE["characters"]["example"], path="x")
def test_the_recipe_is_described_with_its_guide(tmp_path): ...  # describe_recipe(None, "dialoguereel")["guide"]["builtins"]
```

(`_guard` emits `"INVALID_INPUT"` for `InvalidInput`. The spec's `look`/`variant` parameters belong to image casts and arrive with them in Plan 3; `preview` is this plan's addition.)

- [ ] **Step 2: Run** — Expected: FAIL. **Step 3: Implement.** **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Real render test** (`test_dialoguereel_render.py`, slow): the example in `full` mode with the real `hyperframes` renderer and fake voice; assert the MP4 is 1080×1920, its duration matches `script.resolved.json`, and two frames sampled at line 1's and line 2's `meaningMs + 300` differ in the top half (the text changed). Run it: `uv run pytest -m slow tests/test_dialoguereel_render.py -v` — Expected: PASS.
- [ ] **Step 6: Look at the reel.** Render the example with `REEL_TTS=macos` (Kyoko for `ja`) via `uv run reel` or a short script, open the MP4, and compare it with the reference side by side: text legible at phone size, characters crisp, mouth flaps in time, meaning appears after the voice starts. Fix what looks wrong and rerun the slow suite.
- [ ] **Step 7: Commit** — `git commit -m "dialoguereel: make_cast on the MCP server, the guide, and a real render"`
