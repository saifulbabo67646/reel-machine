# techreel Plan 1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working `techreel` recipe that turns a beat script (`say`/`text` lines + visuals) into a narrated 9:16 motion-graphics reel: background, heading, karaoke captions, progress bar, transitions, a recurring hero (`orb`, `laptop`, `monitor`), and the first eight components, rendered by HyperFrames, with a storyboard mode.

**Architecture:** Mirrors `reelmachine/recipes/dialoguereel`: pydantic inputs → `script` (validate + lint) → `narration` (one TTS call per line via `explainer3d.stages.speak_line`, `say` spans mapped onto caption units, a schedule) → `compose` (a JSON document + a DOM runtime copied into a HyperFrames project) → `preview` (storyboard, halts) → `render` (silent MP4 cached by project key, SFX + voice mixed, muxed). The runtime is a pure function of time: `pure.mjs` decides, `main.mjs`/`components.mjs`/`hero.mjs` apply.

**Tech Stack:** Python 3.11+, pydantic v2, pytest; Node ≥ 22 ES modules with `node --test`; HyperFrames renderer; ffmpeg.

**Spec:** `docs/superpowers/specs/2026-10-19-techreel-design.md` (§13 Plan 1). Read it first; section numbers below refer to it.

## Global Constraints

- Frame 1080×1920, 30 fps (`WIDTH, HEIGHT, FPS = 1080, 1920, 30`).
- No LLM, no web fetch inside the engine. No new Python dependencies. No bundled fonts (§6 Fonts: CSS font stacks only).
- Bands (§5.2 Layout): progress bar top 6 px; heading band y 120–420; stage y 440–1380; caption band y 1420–1640; side margins 64 px; nothing below y 1660.
- Line gap 120 ms; transition gaps `cut` 150, `slide` 250, `flash` 200, `zoom` 300 ms; outro 1200 ms after the last word; the hook speaks at 0 ms (§7).
- Caption chunks ≤ 6 words for spaced scripts, ≤ 14 characters for Japanese (§5.3).
- `say` must contain no digit and no `*` (lint errors, §9 rules 2–3). `text` defaults to `say`.
- Every runtime frame is decided from `t` alone (no `requestAnimationFrame`, no `Date.now`, no unseeded `Math.random`).
- Match `dialoguereel`'s code style: module docstrings, short comments that say why, `from __future__ import annotations`, `ConfigDict(extra="forbid")`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A cue word that appears twice or with punctuation/accent markup** ("*176*," vs "176") — must match the first occurrence after normalising (case-fold, strip `*` and punctuation) and not crash on no match. Test in Task 3.
2. **`say` and `text` with different word counts** (Hinglish caption longer than Devanagari speech) — every caption word must still get monotonic, in-range times. Test in Task 2.
3. **A provider returning no word spans** (fake voice on Japanese, a server hiccup) — timings spread over the clip by characters, no empty captions. Test in Task 2.
4. **Many SFX cues** (a 100 s reel with 60+ visuals) — the ffmpeg mix gets one input per cue; cap at 80 cues, dropping `pop`s farthest from any other cue first, keeping every `whoosh`/`thud`. Test in Task 8.
5. **A beat whose visuals overflow the stage** (4 tall blocks) — the stack scales down to ≥ 0.6 and never draws into the caption band. Test in Task 6.

---

## File Structure

```
reelmachine/recipes/techreel/
  __init__.py        docstring only
  models.py          inputs (TechReelInputs, Beat, Line, Cue, visuals), stage outputs
  markup.py          *accent* parsing, caption units, chunking
  words.py           say spans → caption-unit times
  schedule.py        beat/line windows, cue → ms, estimate
  lint.py            Finding, check() — Plan 1 errors 1–6 (§9)
  compose.py         LOOKS, reel_document(), write_project(), project_key()
  stages.py          ScriptStage, NarrationStage, ComposeStage, PreviewStage, RenderStage, sfx_cues()
  verify.py          verify_techreel(): format, duration, audio
  recipe.py          TechReelRecipe, EXAMPLE
  runtime/
    pure.mjs         time → state decisions (no DOM)
    hero.mjs         hero builders (orb, laptop, monitor) + update
    components.mjs   COMPONENTS registry: title, counter, stamp, emoji, chips, window, terminal, repo
    main.mjs         builds DOM from #reel-data, applies per frame
    reel.css         tokens from the look, bands, component styles
    tests/pure.test.mjs
tests/test_techreel_models.py, _markup.py, _words.py, _schedule.py, _lint.py, _compose.py, _stages.py, _engine.py, _render.py
```

Modify: `pyproject.toml` (entry point + package data), `reelmachine/recipes/explainer3d/audio.py` (`tick` effect), `.github/workflows/ci.yml` (node test line).

---

### Task 1: Models and markup

**Files:**
- Create: `reelmachine/recipes/techreel/__init__.py`, `models.py`, `markup.py`
- Test: `tests/test_techreel_models.py`, `tests/test_techreel_markup.py`

**Interfaces:**
- Produces (`models.py`):
  - `Look = Literal["mint", "ember", "violet", "mono"]`, `BeatKind = Literal["hook","pain","reveal","explain","feature","honest","cta","other"]`, `Transition = Literal["cut","slide","flash","zoom"]`, `HeroKind = Literal["orb","laptop","monitor"]`, `HeroState = Literal["center","small-top","corner","hidden","pulse","shake","rings","screen","sleep"]`.
  - `Cue(line: int ≥0, word: str|None, frac: float 0..1|None)` — validator: not both `word` and `frac`.
  - `Line(say: str 1..400, text: str|None ≤400)`; property `caption -> str` (= `text` or `say`).
  - `VisualBase(at: Cue|None=None, exit: Cue|Literal["beat"]="beat", place: Literal["stack","left","right"]="stack")`.
  - Visuals (discriminated on `kind`): `HeroVisual(state: HeroState, text: str|None ≤24)`, `TitleVisual(text ≤80, size: Literal["m","l","xl"]="l")`, `CounterVisual(from_: float = Field(0, alias="from"), to: float, decimals: int 0..2 = 0, prefix ≤8 = "", suffix ≤12 = "", label ≤40 = "")` (`populate_by_name=True`), `StampVisual(text ≤32, tone: Literal["accent","red","green"]="red", rotate: float -25..25 = -8)`, `EmojiVisual(emoji ≤16, place: Literal["auto","left","right"]="auto")` (overrides base `place`), `ChipsVisual(items: list[str ≤24] 1..6, style: Literal["outline","filled"]="outline")`, `WindowVisual(title ≤40, tabs: list[str ≤16] ≤4 = [], active: int=0, live: bool=False, cursor: list[CursorMove] ≤4 = [], button: str|None ≤24)` with `CursorMove(to: tuple[float,float] each 0..1, at: Cue)`, `TerminalVisual(lines: list[TermLine] 1..8)` with `TermLine(cmd: str|None ≤80, out: str|None ≤80, tone: Literal["ok","err","dim"]="dim")` exactly one of cmd/out, `RepoVisual(name ≤60, description ≤140 = "", stars: int ≥0 = 0, license ≤24 = "", language ≤24 = "")`. `Visual = Annotated[Union[...], Field(discriminator="kind")]`.
  - `HeroSpec(kind: HeroKind, text: str|None ≤24)`, `Creator(name ≤40, handle ≤40, avatar: str|None)`.
  - `Beat(id: str|None pattern ^[a-z][a-z0-9_-]{0,23}$, kind: BeatKind="other", heading ≤60|None, kicker ≤40|None, lines: list[Line] 1..8, visuals: list[Visual] ≤12 = [], transition: Transition="cut", keyword ≤24|None, dm ≤40|None, next ≤60|None)`.
  - `TechReelInputs(title 1..80, language: str="en" (BCP-47 pattern as dialoguereel), voice: str="fake:default", look: Look="mint", accent: str|None (#rrggbb), hero: HeroSpec|None, mode: Literal["storyboard","full"]="storyboard", lint: Literal["strict","warn"]="strict", music: str|None, sfx: bool=True, creator: Creator|None, sources: list[str ≤500] ≤20 = [], beats: list[Beat] ≤40 = [], name (same pattern as dialoguereel), outdir: str|None)`.
- Produces (`markup.py`):
  - `@dataclass(frozen=True) class Unit: text: str; accent: bool` — one caption unit (a word, or a character for Japanese).
  - `is_unspaced(language: str) -> bool` — primary subtag in `{"ja","zh","th"}`.
  - `units(caption: str, language: str) -> list[Unit]` — `*…*` toggles accent (an unclosed `*` is literal); spaced: split on whitespace; unspaced: one unit per non-space character, punctuation attached to the preceding unit.
  - `plain(text: str) -> str` — `*` removed.
  - `norm(token: str) -> str` — casefold, strip `*` and Unicode punctuation (`unicodedata.category` starting with `P`).
  - `chunks(us: list[Unit], language: str) -> list[list[int]]` — indices; spaced: ≤ 6 units, break after a unit ending in `.,!?;:।…` when the chunk has ≥ 2 units; unspaced: ≤ 14 characters, break after `。、！？`.

- [ ] **Step 1: Write failing tests**

```python
def test_text_defaults_to_say():
    assert Line(say="hello there").caption == "hello there"

def test_counter_accepts_from_alias():
    v = Beat.model_validate({"lines": [{"say": "x"}], "visuals": [{"kind": "counter", "from": 5, "to": 176}]}).visuals[0]
    assert (v.from_, v.to) == (5, 176)

def test_cue_refuses_word_and_frac(): pytest.raises(ValidationError, Cue, line=0, word="a", frac=0.5)
def test_unknown_visual_kind_refused(): ...  # {"kind": "hologram"} → ValidationError
def test_terminal_line_needs_exactly_one_of_cmd_out(): ...
```

```python
def test_units_accent_and_split():
    assert units("Aur laptop *zinda* hai.", "hi") == [Unit("Aur", False), Unit("laptop", False), Unit("zinda", True), Unit("hai.", False)]
def test_units_multiword_accent(): units("*176 billion* params", "en")[:2] == [Unit("176", True), Unit("billion", True)]
def test_units_unclosed_star_is_literal(): units("5 * 3", "en")[1].text == "*"
def test_units_japanese_by_character(): [u.text for u in units("百二十五億。", "ja")] == ["百","二","十","五","億。"]
def test_norm(): norm("*Zinda*,") == "zinda"; norm("हैं।") == "हैं"
def test_chunks_six_and_punctuation():
    us = units("one two, three four five six seven eight", "en")
    assert chunks(us, "en") == [[0, 1], [2, 3, 4, 5, 6, 7]]
def test_chunks_japanese_fourteen(): all(sum(len(us[i].text) for i in c) <= 14 for c in chunks(us, "ja"))
```

- [ ] **Step 2: Run** `pytest tests/test_techreel_models.py tests/test_techreel_markup.py -q` → FAIL (module not found).
- [ ] **Step 3: Implement** the models and markup above.
- [ ] **Step 4: Run** the same → PASS.
- [ ] **Step 5: Commit** `techreel: models, accent markup and caption units`.

### Task 2: Say spans → caption-unit times

**Files:** Create `reelmachine/recipes/techreel/words.py`; Test `tests/test_techreel_words.py`

**Interfaces:**
- Consumes: `WordSpan(text, start_ms, end_ms)` from `reelmachine.core.timeline`; `Unit` from Task 1.
- Produces: `unit_times(spans: list[WordSpan], us: list[Unit], speech_ms: int) -> list[tuple[int, int]]` — line-relative (start, end) per unit, monotonic non-decreasing, all within `[0, speech_ms]`.

Algorithm: if `len(spans) == len(us)`, copy span times. Otherwise build a piecewise-linear char→time map from the spans (cumulative non-space characters of each span text at that span's start; the total at the last span's end; with no spans, `0 → 0` and `total → speech_ms`). Unit *i* starts at `map(fraction of caption characters before i × total span chars)`; ends at the next unit's start (the last ends at the last span's end, or `speech_ms`).

- [ ] **Step 1: Failing tests**

```python
S = [WordSpan(text="भाई,", start_ms=500, end_ms=1000), WordSpan(text="मैंने", start_ms=1000, end_ms=1400)]
def test_equal_counts_copy(): assert unit_times(S, units("Bhai, maine", "hi"), 1600) == [(500, 1000), (1000, 1400)]
def test_unequal_counts_monotonic_in_range():
    t = unit_times(S, units("Bhai, maine apne", "hi"), 1600)
    assert len(t) == 3 and all(0 <= a <= b <= 1600 for a, b in t) and [a for a, _ in t] == sorted(a for a, _ in t)
    assert t[0][0] == 500
def test_no_spans_spread_by_characters():
    t = unit_times([], units("ab abcd", "en"), 600)
    assert t == [(0, 200), (200, 600)]
def test_japanese_characters_from_char_spans(): ...  # 5 one-char spans, 5 units → copied
```

- [ ] **Step 2:** Run `pytest tests/test_techreel_words.py -q` → FAIL.
- [ ] **Step 3:** Implement `unit_times`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `techreel: map spoken word timings onto caption words`.

### Task 3: Schedule and cues

**Files:** Create `reelmachine/recipes/techreel/schedule.py`; Test `tests/test_techreel_schedule.py`

**Interfaces:**
- Constants: `LINE_GAP_MS = 120`, `TRANSITION_MS = {"cut": 150, "slide": 250, "flash": 200, "zoom": 300}`, `OUTRO_MS = 1200`.
- `@dataclass(frozen=True) LineSlot(start_ms: int, speech_ms: int, end_ms: int)`; `BeatSlot(start_ms: int, end_ms: int, lines: tuple[LineSlot, ...])`; `Plan(beats: tuple[BeatSlot, ...], duration_ms: int)`.
- `schedule(beats: Sequence[Beat], speech_ms: Sequence[Sequence[int]]) -> Plan` — beat 0 starts at 0; beat *i>0* starts at the previous beat's `end_ms + TRANSITION_MS[beats[i].transition]`; lines in a beat are separated by `LINE_GAP_MS`; `duration_ms = last beat end + OUTRO_MS`.
- `estimate(inputs: TechReelInputs) -> Plan` — `speech_ms` from `explainer3d.timing.estimate_speech_ms(line.say, inputs.language)` (min 400 ms per line).
- `cue_ms(cue: Cue | None, beat: BeatSlot, times: Sequence[Sequence[tuple[int,int]]], us: Sequence[Sequence[Unit]]) -> tuple[int, bool]` — absolute ms and whether it resolved as asked. `None` → beat start. `line` out of range → beat start, `False`. `word` → first unit of that line whose `norm` equals `norm(word)` (multi-word cue: first unit matching its first token); not found → line start, `False`. `frac` → line start + frac × speech.
- `exit_ms(exit: Cue | Literal["beat"], beat: BeatSlot, next_start: int | None, ...) -> int` — `"beat"` → `next_start` (the next beat's start) or the reel's duration; a cue → `cue_ms`.

- [ ] **Step 1: Failing tests** — `schedule` with beats `[cut, slide]`, speech `[[1000, 800], [500]]` → beat0 `(0, 1920)` lines `(0,1000),(1120,1920)`; beat1 starts 2170, ends 2670; duration 3870. `cue_ms` with `word="*176*,"` against units `["mein", "*176", "billion*"]` returns unit 1's start; duplicate word returns the first; missing word → `(line start, False)`; `frac=0.5`; out-of-range line → `(beat start, False)`. `estimate` of a 2-beat input is > 0 and each line ≥ 400 ms.
- [ ] **Step 2:** Run `pytest tests/test_techreel_schedule.py -q` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `techreel: beat schedule and visual cues`.

### Task 4: Lint (Plan 1 errors)

**Files:** Create `reelmachine/recipes/techreel/lint.py`; Test `tests/test_techreel_lint.py`

**Interfaces:**
- `@dataclass(frozen=True) Finding(rule: str, severity: Literal["error","warning"], message: str, hint: str, path: str)`.
- `check(inputs: TechReelInputs, bound_provider: str | None = None) -> list[Finding]` with rules (paths like `beats[2].lines[0].say`):
  - `no-beats` (error, path `beats`), `empty-say` (error).
  - `digit-in-say` (error): any `str.isdigit()` character (covers Devanagari/Arabic-Indic digits). Hint: `write numbers and names with digits as words in the voice's language, e.g. "GPT-4o" → "G P T four o"; keep the digits in "text"`.
  - `markup-in-say` (error): `*` in `say`.
  - `cue-line` (error): any visual `at`/`exit`/`cursor[].at` with `line ≥ len(beat.lines)`.
  - `voice-provider` (error): `bound_provider` given and `provider_for(inputs.voice) != bound_provider` (reuse `reelmachine.recipes.doodle.narration.provider_for`).
  - `cue-word` (warning): a `word` cue not found among the line's caption units (via `norm`).
- `blocks(inputs) -> bool` — `inputs.mode == "full" and inputs.lint == "strict"`.

- [ ] **Step 1: Failing tests** — one failing and one passing case per rule; `"एक सौ"` passes digit check, `"१७६"` fails it; `"GPT-4o"` fails.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `techreel: lint for the errors that break narration`.

### Task 5: Compose document and project

**Files:** Create `reelmachine/recipes/techreel/compose.py`; Test `tests/test_techreel_compose.py`

**Interfaces:**
- `WIDTH, HEIGHT, FPS = 1080, 1920, 30`; `RUNTIME_DIR = HERE / "runtime"`; `RUNTIME_FILES = ("*.mjs", "*.css")`.
- `LOOKS: dict[str, dict[str, str]]` — keys per look: `bg, glow1, glow2, glow3, text, muted, accent, accentInk, card, cardEdge, red, green`. Values: `mint` bg `#06100f` accent `#2ee6b8` glows teal/deep-teal/navy (OpenDots); `ember` bg `#141110` accent `#ff5a2a` glows rust/brown/charcoal (Strata); `violet` bg `#0d0a18` accent `#9b7bff`; `mono` bg `#0b0b0c` accent `#f4f4f5`. `accentInk` is the text colour on an accent fill.
- `look_tokens(inputs) -> dict[str,str]` — `LOOKS[inputs.look]` with `accent` replaced by `inputs.accent` when set.
- `reel_document(inputs: TechReelInputs, plan: Plan, times: list[list[list[tuple[int,int]]]]) -> dict` — `times[b][l]` = unit times for beat b line l (line-relative). Shape:

```jsonc
{"mode": "reel", "width": 1080, "height": 1920, "duration": 12.3, "language": "hi", "look": {...},
 "hero": {"kind": "orb", "text": null} | null,
 "heroStates": [{"atMs": 0, "state": "center", "text": null}],      // from hero visuals, in time order; first at 0 = "center" when a hero exists
 "beats": [{"id": "hook", "kind": "hook", "startMs": 0, "endMs": 1920, "transition": "cut",
            "heading": [{"t": "bhai…", "a": false}], "kicker": "",
            "visuals": [{"id": "b0v1", "kind": "counter", "enterMs": 640, "exitMs": 2170, "place": "stack",
                         "class": "block", ...fields camelCased, cursor[].at → atMs}]}],
 "captions": [{"startMs": 0, "endMs": 900, "words": [{"t": "Bhai,", "a": false, "startMs": 0, "endMs": 400}]}],
 "sfx": true}
```
  Hero visuals become `heroStates`, not entries in `visuals`. `class` is `"overlay"` for `stamp`/`emoji`, else `"block"`. Captions: per line, `chunks()` of its units; a chunk shows from its first unit's start to the next chunk's first start (the line's last chunk until the line's end + 0, or the next line's start if sooner).
- `write_project(doc: dict, dest: Path) -> Path` and `project_key(doc: dict) -> str` — as `dialoguereel/compose.py` (same "<" escaping, `data-composition-id="techreel"`, `window.__hf.buildReady = { techreel: … }`), without characters.

- [ ] **Step 1: Failing tests** — a 2-beat fixture with a hero visual, a counter cued on a word, a stamp: assert `heroStates[0] == {"atMs": 0, "state": "center", "text": None}` and the second state's `atMs` is its cue; the counter's `enterMs` equals its word's absolute start and `exitMs` equals beat 1's start; the stamp has `class == "overlay"`; caption chunks cover each line without overlap; `look_tokens` honours `accent`; `write_project` writes `index.html` + runtime files and the data script contains no raw `<`; `project_key` changes when a runtime file's bytes change (monkeypatch `_runtime_paths`).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement (runtime files may be placeholders until Task 6/7; create empty `runtime/main.mjs` and `runtime/reel.css` here). **Step 4:** PASS.
- [ ] **Step 5:** Commit `techreel: the reel document and its HyperFrames project`.

### Task 6: Runtime decisions (`pure.mjs`)

**Files:** Create `reelmachine/recipes/techreel/runtime/pure.mjs`, `runtime/tests/pure.test.mjs`; Modify `.github/workflows/ci.yml` (add `node --test "reelmachine/recipes/techreel/runtime/tests/*.test.mjs"` beside the dialoguereel line); add a pytest wrapper in `tests/test_techreel_compose.py` that runs it (copy `tests/test_mapreel_compose.py:69`'s pattern, skip without `node`).

**Interfaces (exports):**
- Constants: `ENTER_MS = 260`, `EXIT_MS = 200`, `GLIDE_MS = 300`, `COUNTER_MS = 900`, `HERO_MOVE_MS = 450`, `TYPE_CPS = 28`, `STAGE = {top: 440, bottom: 1380, left: 64, right: 1016}`, `STACK_GAP = 32`, `MIN_SCALE = 0.6`.
- `backOut(p) -> number` (overshoot 1.7), `clamp01(x)`.
- `enterP(v, t) -> 0..1` and `exitP(v, t) -> 0..1`; `alive(v, t) -> bool` (`enterMs ≤ t < exitMs + EXIT_MS`).
- `stackLayout(blocks, sizes, t) -> Map<id, {x, y, scale}>` — `blocks` = block visuals of the current beat (plus `{id:"hero"}` when the hero is in a stacking state), `sizes` = `Map<id,{w,h}>` measured at build. Live blocks stack in `enterMs` order, centred horizontally; `place: left|right` pairs share a row; total height + gaps > stage height ⇒ uniform scale `max(MIN_SCALE, room/needed)`; stack centred vertically in the stage. Positions glide: each block's y is interpolated from its layout at the last membership change to the new one over `GLIDE_MS` (compute both layouts from the membership just before and after the latest enter/exit ≤ t).
- `counterValue(v, t) -> string` — `from → to` over `COUNTER_MS` from `enterMs`, ease-out cubic, `toFixed(decimals)`, with `prefix`/`suffix`; values ≥ 1000 get thin grouping only if `to` ≥ 10000 (e.g. `19.9K` stays literal when the caller writes `to: 19.9, suffix: "K"`).
- `typed(text, startMs, t) -> string` — the first `floor((t-startMs)/1000*TYPE_CPS)` characters (whole text after).
- `captionAt(captions, t) -> index | -1`; `wordPhase(w, t) -> "future"|"now"|"past"`.
- `heroAt(states, t) -> {state, from, p, text}` — the latest state with `atMs ≤ t`, the one before it as `from`, `p` = progress over `HERO_MOVE_MS`.
- `beatAt(beats, t) -> index`; `transitionP(beats, i, t) -> 0..1` over that beat's transition length (`cut`=0 ⇒ always 1).
- `progress(t, duration) -> 0..1`; `blob(i, t, seed) -> {x, y, r}` seeded slow Lissajous drift (period 9–14 s).
- `seedOf(str) -> uint32` (FNV-1a, as dialoguereel).

- [ ] **Step 1: Failing tests** — `stackLayout` with four 400-px-tall blocks returns scale `0.6` and the last block's bottom ≤ 1380 (Review Focus 5); one block alone is centred in the stage; a block that exits makes the others glide (y at `exit+GLIDE_MS/2` strictly between old and new); `counterValue({from:0,to:176,decimals:0,enterMs:0}, 900) === "176"` and is `"0"` at 0; `typed("abc", 0, 1000/28*2) === "ab"`; `heroAt` interpolation `p` hits 1 at 450 ms; `captionAt` picks by time; `blob` is identical for equal arguments.
- [ ] **Step 2:** `node --test reelmachine/recipes/techreel/runtime/tests/pure.test.mjs` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run → PASS; `pytest tests/test_techreel_compose.py -q` → PASS.
- [ ] **Step 5:** Commit `techreel: runtime decisions from time alone`.

### Task 7: Runtime DOM — layers, hero, components

**Files:** Create `runtime/main.mjs`, `runtime/hero.mjs`, `runtime/components.mjs`, `runtime/reel.css`.

**Interfaces:**
- `components.mjs` exports `COMPONENTS: Record<kind, {build(v, doc) -> HTMLElement, update(node, v, t, doc) -> void}>` for `title, counter, stamp, emoji, chips, window, terminal, repo`. Visual behaviour per §5.2: title slams in word by word (60 ms stagger); counter rolls (`counterValue`) with its label under it; stamp scales from 1.8 → 1 with a slight rotate overshoot at `enterMs`, red/green/accent border text; emoji pops with a 2-cycle wobble; chips appear one by one 120 ms apart; window shows title bar dots, tabs (active tab accent), 3 skeleton rows, LIVE badge pulsing, a cursor arrow moving to each `cursor[].to` (fractions of the window box) at its `atMs` over 350 ms, an optional button that gets a press scale when the cursor arrives; terminal types each `cmd` line (`$ ` prefix, `typed`) one after another then shows its `out` lines instantly in tone colours (ok green, err red, dim muted); repo shows a GitHub-like card: owner/name bold, description, a ⭐ counter rolling to `stars`, licence and language pills.
- `hero.mjs` exports `buildHero(kind, doc) -> HTMLElement`, `updateHero(node, heroAt(...), t, doc)`. `orb`: a 360 px sphere — stacked radial gradients (chrome: white specular, grey midtones, accent rim light) under an SVG `feTurbulence`+`feDisplacementMap` whose `baseFrequency`/`seed` attribute is set from `t` so the liquid surface swirls deterministically. `laptop` / `monitor`: 4 px off-white line art (SVG), screen area that shows `text` in the accent display font with a glow when state `screen`. States: `center` (in the stack), `small-top` (scale 0.45 at the stack top), `corner` (scale 0.3 at top-right of the stage, out of the stack), `hidden` (scale 0, opacity 0), `pulse` (scale 1 ± 0.04 at 1.2 Hz), `shake` (±10 px 12 Hz for 400 ms after `atMs`), `rings` (3 expanding rings, 1.6 s period), `sleep` (opacity 0.45 + "z z z" floating). Moves between states interpolate position/scale over `HERO_MOVE_MS` with `backOut`.
- `main.mjs`: reads `#reel-data`; builds background (base colour + 3 blurred 900 px glow circles at `blob()` positions, `filter: blur(120px)`; a 3 % grain via a tiny SVG noise data URI), heading band (heading tokens as spans, accent tokens in `--accent`, display font 96 px max, fitted to 952 px width; kicker as mono 28 px letter-spaced above it, accent colour), stage, caption pill (dark translucent pill, 54 px bold, current word full white, past words 85 % white, future words hidden, accent words in `--accent`), progress bar. Each frame (HyperFrames seek hook, same mechanism as `dialoguereel/runtime/main.mjs`): pick the beat, apply `transitionP` (slide: stage translateX from 100 %→0 for the incoming beat; flash: accent full-bleed overlay peaking at the midpoint; zoom: scale 1.15→1 + blur 12→0 px), heading slam-in (word by word, 70 ms stagger) and blur-out over the last 150 ms before the next beat; block layout from `stackLayout` (sizes measured once after fonts load: `document.fonts.ready`); overlays: stamp centred on the newest live block (rotated `rotate`), emoji at that block's top-right (or `place`); caption from `captionAt`; progress bar width. `?t=3.2` in the URL shows that frame (as dialoguereel).
- `reel.css`: tokens as CSS variables set from `doc.look` on `:root`; font stacks from §6 (display `"Archivo Black", "Montserrat", "Helvetica Neue", "Arial Black", sans-serif`; mono `"JetBrains Mono", "SF Mono", Menlo, monospace`; Devanagari `"Noto Sans Devanagari", "Kohinoor Devanagari", "Devanagari Sangam MN", sans-serif` appended to both; Japanese `"Noto Sans JP", "Hiragino Sans"`); cards: `--card` fill, 1.5 px `--cardEdge` border, 28 px radius, soft shadow.

- [ ] **Step 1:** Write a fixture `runtime/tests/fixture.json`: a reel document with every Plan 1 component, every hero state and all three heroes over ~20 s (view any frame with `index.html?t=<s>` from `compose.write_project(fixture, tmp)`).
- [ ] **Step 2:** Implement `components.mjs`, `hero.mjs`, `main.mjs`, `reel.css`.
- [ ] **Step 3:** Check with the real renderer: in a Python shell, `write_project(fixture, /tmp/…)`, then `HyperframesRenderer(get_settings()).snapshot(project, s, out)` at 1, 4, 8, 12, 16 s. Look at each PNG (Read tool). Expected: no element outside its band, captions legible, Devanagari glyphs shaped (not boxes), the orb visibly liquid, counter mid-roll at its enter + 450 ms.
- [ ] **Step 4:** `node --test reelmachine/recipes/techreel/runtime/tests/*.test.mjs` still PASS.
- [ ] **Step 5:** Commit `techreel: the runtime draws the layers, the hero and the first components`.

### Task 8: Stages, SFX and recipe registration

**Files:**
- Create: `stages.py`, `verify.py`, `recipe.py`
- Modify: `pyproject.toml` (entry `techreel = "reelmachine.recipes.techreel.recipe:TechReelRecipe"` under `reelmachine.recipes`; package data `recipes/techreel/runtime/*.mjs`, `recipes/techreel/runtime/*.css`), `reelmachine/recipes/explainer3d/audio.py` (add `"tick": "sine=f=2400:d=0.025,afade=t=out:st=0:d=0.025,volume=0.6"` to `_SFX`)
- Test: `tests/test_techreel_stages.py`, `tests/test_techreel_engine.py` (slow, fake renderer)

**Interfaces:**
- Stage outputs in `models.py`: `ScriptOutput(inputs, lint: list[dict], warnings: list[str])`; `NarrationOutput(script, audio_asset: str, duration_ms: int, beats: list[BeatSlotModel], times: list[list[list[tuple[int,int]]]], provider: str)` (`BeatSlotModel`/`LineSlotModel` mirror the dataclasses); `ComposeOutput(narration, project: str, key: str)`; `PreviewOutput(compose, sheet: str = "")`; `RenderOutput(video, srt, manifest, duration_ms, render_mode="hyperframes")`.
- `ScriptStage` (`id="script"`): validate, `lint.check(inputs, bound)`, raise `InvalidInput(..., details={"path","rule"})` on the first error when `lint.blocks`.
- `NarrationStage` (`id="narration"`, uses `narration`): for every line, `speak_line(ctx, provider, line.say, inputs.voice, inputs.language)` (cached), `unit_times(spans, units(line.caption, language), ms)`, `schedule(...)`; track = for each line `pad_speech(src, lead_ms=start - cursor, total_ms=lead+ms)` then a `silence` tail to `duration_ms`, joined with `ffmpeg.concat_audio`; store as `AssetKind.NARRATION`. A voice whose provider ≠ bound → `VoiceUnavailable` (copy dialoguereel's message/hint).
- `ComposeStage`: `reel_document` → `write_project(ctx.workdir/"project")`; writes `artifacts/script.resolved.json` (`durationMs`, per beat `startMs/endMs`, per visual `kind/enterMs/exitMs`, `warnings`).
- `PreviewStage`: `storyboard` → stills at each beat's midpoint and at `max(enterMs)+400` of its visuals (deduplicated, ≤ 24), tiled 4 wide at 360×640 into `artifacts/storyboard.jpg` (reuse dialoguereel's tiling code shape), `halt_pipeline=True`; `full` passes through.
- `sfx_cues(doc: dict) -> list[tuple[int, str]]` — `whoosh` at each beat start except the first; `pop` at each block visual's `enterMs`; `thud` at each stamp; `ding` at each counter's `enterMs + COUNTER_MS (900)`; `tick` at each terminal `cmd` start (the runtime's typing order: cmd k starts after the previous cmd finished typing + 250 ms; export the same rule from Python as `terminal_starts(v) -> list[int]` and mirror it in `pure.mjs` as `terminalStarts`). Empty when `doc["sfx"]` is false. Capped at `MAX_SFX = 80`: drop `pop`s first, those closest to another cue first (Review Focus 4).
- `RenderStage`: as `dialoguereel.RenderStage` (cache per caller under `cache/techreel/reels/<renderer>/<key[:2]>/<key>.mp4`, `synth_sfx(..., designed=True)`, `mix(..., profile="studio")`, `mux`), captions → `write_srt` with one cue per caption chunk (plain text), manifest `{reel, recipe: "techreel", mode, created, durationMs, look, voice, renderer, sources}`.
- `verify_techreel(ctx, outputs) -> VerificationReport` — storyboard → ok; full → format 1080×1920 with audio, duration within 100 ms of the plan, `check_audio` (import from `dialoguereel.verify`).
- `TechReelRecipe.spec`: `id="techreel"`, `title="Tech & AI explainer reel"`, summary one sentence per spec §1, stages `script → narration → compose → preview → render`, providers `narration` (default `fake`, description "Text-to-speech with word timings; Supertonic recommended") and `renderer` (`hyperframes`), cost note `tts_characters`, artifacts `("reel.mp4","captions.srt","reel.json","storyboard.jpg","script.resolved.json")`, `example=EXAMPLE`, `guide={"summary": "…", "workflow": [...], "language": "...say/text rules (§6)..."}` (the full guide is Plan 2). `probe` mirrors dialoguereel's: lint findings in `details["lint"]`, `details["estimatedMs"]` from `estimate`, renderer and voice checks.
- `EXAMPLE` — an original 4-beat Hindi reel about a fictional open-source tool "Pocket LLM" (hook with an `orb` hero and a counter, a pain beat with a stamp + emoji, a reveal with `repo` + `chips`, a feature with `terminal` + `window`), `voice: "fake:default"`, `mode: "storyboard"`, Devanagari `say` with numbers in words, Hinglish `text`, `creator: {"name": "Your Name", "handle": "@yourhandle"}`, one `sources` URL `https://example.com/pocket-llm`.

- [ ] **Step 1: Failing tests** (`test_techreel_stages.py`, no render): `sfx_cues` on a doc fixture → whoosh count = beats−1, one thud per stamp, ding at counter enter+900; `sfx: false` → `[]`; a doc with 120 pops → 80 cues and every whoosh kept; `terminal_starts` matches hand-computed values; `ScriptStage` raises `InvalidInput` with `details["rule"] == "digit-in-say"` in strict full mode and only warns in storyboard mode. (`test_techreel_engine.py`, `pytest.mark.slow`, `provider_overrides={"renderer": "explainer3d-fake"}`, `tts="fake"`): `TechReelInputs.model_validate(EXAMPLE)` passes and the lint has no errors for it; the EXAMPLE storyboard succeeds with `storyboard.jpg` + `script.resolved.json` and no `reel.mp4`; `describe_recipe`-level: `TechReelRecipe.spec.id == "techreel"` is discoverable through the registry (as `tests/test_dialoguereel_engine.py` does).
- [ ] **Step 2:** Run `pytest tests/test_techreel_stages.py -q` and `pytest tests/test_techreel_engine.py -q -m slow` → FAIL.
- [ ] **Step 3:** Implement; `pip install -e .` (or `uv pip install -e .`) so the entry point registers.
- [ ] **Step 4:** Both PASS; `pytest -q` (default suite) still PASS; `ruff check reelmachine/recipes/techreel tests/test_techreel_*.py` clean.
- [ ] **Step 5:** Commit `techreel: stages, sound effects and the recipe`.

### Task 9: Real render and the Hindi acceptance reel

**Files:** Create `tests/test_techreel_render.py` (slow, skipped without HyperFrames — copy `tests/test_dialoguereel_render.py`'s header); Test only.

- [ ] **Step 1: Test** `test_the_example_renders_as_a_real_reel`: EXAMPLE with `mode: "full"`, `tts="fake"` → job SUCCEEDED; `reel.mp4` 1080×1920 with audio; duration within 100 ms of `script.resolved.json`'s `durationMs`; the frame at a counter's `enterMs + 1000` differs from the frame at its beat start in the stage band (mean abs diff > 4 over y 440–1380), and the caption band at a line's midpoint is not the background colour (some pixel > 200 luminance).
- [ ] **Step 2:** Run `pytest tests/test_techreel_render.py -q -m slow` → PASS (on a machine with HyperFrames).
- [ ] **Step 3: Manual acceptance with Supertonic** (not CI): `REEL_TTS=supertonic reel run techreel` (or the engine in a Python shell) with EXAMPLE, `voice: "supertonic:M1"`, `mode: "full"`, against `http://127.0.0.1:8787`. Watch the MP4 (sample frames + listen); transcribe with whisper-cli to confirm the Hindi is intelligible and numbers are said as words. Note anything off as Plan 2 input.
- [ ] **Step 4:** Commit `techreel: a real render of the example reel`.

---

## Self-review notes

- Spec §13 Plan 1 items → Tasks: package/models (1), `say`/`text` map (2), schedule (3), lint errors 1–6 (4; rule 5 SVG/image is Plan 2 since those components arrive there), compose (5), runtime layers/hero/8 components (6–7), narration/render/storyboard/SFX (8), Hindi Supertonic reel (9).
- Deferred to Plan 2 by the spec: other components, hero `locked`/`split`, `robot`/`chip`/`phone`/`svg`/`image` heroes, full lint and guide, CTA defaults, `assets` stage. Plan 3: `post.md`, music, full verify, docs.
