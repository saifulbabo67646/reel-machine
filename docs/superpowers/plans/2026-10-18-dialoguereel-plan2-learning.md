# dialoguereel Plan 2 — Learning and Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a dialoguereel teach and hold attention. Add key words (underlined, with meaning chips), emotions, slow replays, a listen-first pass, a word recap and a "your turn" quiz. Add the full lint with the JLPT level check, eight more built-in characters, and a full agent guide.

**Architecture:** The schedule grows from "hook + lines + tail" into sections (hook, listen pass, bridge, dialogue with slow replays, recap, quiz, tail) and a list of audio cues. The narration stage assembles the voice track from those cues. The reel document carries the new sections; the DOM runtime draws them from pure, Node-tested state functions. Lint rules and the key/level helpers are plain Python modules.

**Tech Stack:** As Plan 1: Python 3.11, pydantic v2, ffmpeg (`atempo`), HyperFrames 0.8.146, `node --test`, pytest (`-m slow` for ffmpeg/engine).

**Spec:** `docs/superpowers/specs/2026-10-18-dialoguereel-design.md`. Read §3 (keys, emotion, slow, flow, quiz), §5 (timeline items 2–6), §7 (guide), §8 (rules 4–11) and §11 item 2. Plan 1 (`docs/superpowers/plans/2026-10-18-dialoguereel-plan1-foundation.md`) is built; this plan extends its modules.

## Global Constraints

- Everything Plan 1's Global Constraints say still holds: SVG contract and safety; the page only receives SVG serialized from the checked tree; all agent text reaches the DOM via `textContent`; the data script escapes every `<`; frames are a pure function of time.
- **New input fields:**
  - `Line.keys: list[KeyWord]`, max 4.
  - `KeyWord(text: 1..30, reading: str|None ≤60, meaning: 1..60)`.
  - `Line.emotion: Literal["neutral","happy","surprised","sad","thinking"] = "neutral"`.
  - `Line.slow: bool = False`.
  - `DialogueReelInputs.flow: Literal["standard","listen_first"] = "standard"`.
  - `DialogueReelInputs.quiz: bool | int = True`. An int is a 0-based line index.
- **Timing constants:**
  - `BRIDGE_MS = 700`
  - `SLOW_RATE = 0.75`; a slow replay window is `round(speech_ms / 0.75)` and starts `GAP_MS` after the line ends.
  - `RECAP_MS = 3000`, present only when the reel has at least one key word.
  - `QUIZ_COUNT_MS = 3000`
  - `QUIZ_HOLD_MS = 800`
  - Hook 1500, gap 350 and tail 900 are unchanged.
- **Order of sections:** hook → [listen pass: every line spoken, gaps of `GAP_MS`] → [bridge `BRIDGE_MS`] → dialogue (each line, then its slow replay if `slow`) → [recap] → [quiz: prompt + countdown `QUIZ_COUNT_MS`, then the answer line spoken, then `QUIZ_HOLD_MS`] → tail.
- **Quiz line:**
  - `quiz: true` picks the line with the most keys; ties go to the earliest; with no keys anywhere, the longest `meaning`.
  - `quiz: false` means no quiz.
  - An int outside the lines is lint error `quiz-line`.
- **Emotion → parts and emoji:** happy → `eyes-happy` + `mouth-smile` when not talking, emoji ❤️; surprised → `eyes-surprised`, emoji ❗; sad → `sweat`, emoji 💦; thinking → head tilt 8°, emoji ❓. A missing optional part falls back to the neutral part.
- **Lint warnings (spec §8, numbering kept):**
  - 4 `no-reading`: a non-Latin script line has no `reading`.
  - 5 `long-line`: CJK > 22 characters, others > 42.
  - 6 `length`: estimated reel outside 15–45 s, listen pass excluded.
  - 7 `key-missing`: key not found in its line.
  - 8 `level`: JLPT key harder than the reel's N-level.
  - 9 `key-count`: fewer than 2 or more than 6 keys in the reel.
  - 10 `ruby`: exists.
  - 11 `same-speaker`: same speaker on more than 3 consecutive lines.
  - The new error: `quiz-line`.
- **Localised section strings** (en, ja, ko, es, zh; any other `meaning_language` falls back to en): `listen`, `bridge`, `recap`, `quiz`, `cta`, `follow`. The en values are:
  - `listen`: "👂 Listen first — how much did you catch?"
  - `bridge`: "Now with subtitles"
  - `recap`: "Words from this video"
  - `quiz`: "How do you say:"
  - `cta`: "Comment your answer 👇"
  - `follow`: "Follow for daily {level}"; when `level` is empty: "Follow for more"
- **Built-in library:** onigiri, dango, boba, cat, shiba, penguin, bunny, kimchi-jar. Each meets the contract with every optional part and is recolourable through `--primary`/`--secondary`/`--accent`.
- **Caption text** collapses only CR/LF runs to one space and keeps every other character, including U+3000.

## Review Focus

1. **A reel with no key words at all.** It should have no recap section, a quiz that falls back to the longest-meaning line, and only the `key-count` warning. Tests: Task 1 (schedule) and Task 3 (lint).
2. **A key that appears twice in its line, or overlaps another key.** It should underline the first occurrence only, and the second key should take the next non-overlapping match. Test: Task 2.
3. **Listen-first combined with slow replays and a quiz.** Audio cues should never overlap, and the voice track length should equal `duration_ms`. Tests: Task 1 (cue invariant over many combinations) and Task 4 (engine run).
4. **`quiz: 0` and `quiz: true` on a 3-line reel.** Both should be valid. `quiz: 3` is a `quiz-line` error with path `quiz`. Tests: Task 3.
5. **A built-in recoloured with all three colours.** It should still pass `check_svg` and inline cleanly next to every other built-in, with no id collisions. Test: Task 7.

---

## File Structure

```
reelmachine/recipes/dialoguereel/
  models.py      + KeyWord, Line.keys/emotion/slow, Inputs.flow/quiz
  strings.py     NEW  localised section strings
  schedule.py    sections + audio cues (replaces the flat schedule)
  keys.py        NEW  key-word matching in a line
  levels.py      NEW  JLPT lookup from reelmachine/data/jlpt_vocab.json
  lint.py        + rules 4–9, 11, quiz-line
  stages.py      narration from cues (slow via atempo), SFX, storyboard frames, caption fix, resolved json
  compose.py     document: keys, emotion, slow, sections, strings
  runtime/pure.mjs, main.mjs, reel.css, tests/pure.test.mjs
  characters/*.svg + index.json (8 new)
  guide.py, recipe.py (EXAMPLE)
docs/RECIPES.md  one dialoguereel entry
```

---

### Task 1: Model fields, section strings and the sectioned schedule

**Files:**
- Modify: `reelmachine/recipes/dialoguereel/models.py`, `schedule.py`
- Create: `reelmachine/recipes/dialoguereel/strings.py`
- Test: `tests/test_dialoguereel_schedule.py` (extend), `tests/test_dialoguereel_models.py` (extend)

**Interfaces:**
- Produces (`models.py`): `KeyWord`, the `Line` and `DialogueReelInputs` fields in Global Constraints, and `Emotion` (the Literal).
- Produces (`strings.py`): `STRINGS: dict[str, dict[str, str]]` keyed by language, then by key; `strings_for(meaning_language: str, level: str) -> dict[str, str]`. It resolves the primary subtag like `compose.prompt_for`, falls back to en, and formats `follow` with the level.
- Produces (`schedule.py`). Keep `HOOK_MS`, `GAP_MS`, `TAIL_MS`, `MEANING_AT` and `LineWindow`; add the new constants.
  - `LineWindow` gains `slow_start_ms: int | None = None` and `slow_ms: int = 0`.
  - `Cue(at_ms: int, line: int, rate: float)`, frozen; `rate` is 1.0 or `SLOW_RATE`.
  - `QuizWindow(line: int, start_ms: int, reveal_ms: int, end_ms: int)`.
  - `Schedule(hook_ms, lines, duration_ms, listen: tuple[LineWindow, ...] = (), bridge_ms: int | None = None, recap_ms: int | None = None, quiz: QuizWindow | None = None, cues: tuple[Cue, ...] = ())`. `lines` stay the dialogue windows; `*_ms` fields are section start times.
  - `schedule(speech_ms: Sequence[int], *, slow: Sequence[bool] = (), listen_first: bool = False, recap: bool = False, quiz_line: int | None = None) -> Schedule`.
  - `quiz_line_for(inputs: DialogueReelInputs) -> int | None` applies the quiz rule from Global Constraints.
  - `estimate(inputs) -> Schedule` passes every option from `inputs`, with `recap = any(line.keys)`.
- Existing callers (`stages.plan_of`, compose) keep working: the new `Schedule` fields have defaults.

- [ ] **Step 1: Write the failing tests**

```python
def test_plain_schedule_is_unchanged():
    s = schedule([1000, 2000])
    assert [(w.start_ms, w.end_ms, w.meaning_ms) for w in s.lines] == [(1500, 2500, 2100), (2850, 4850, 4050)]
    assert s.duration_ms == 5750 and s.cues == (Cue(1500, 0, 1.0), Cue(2850, 1, 1.0))

def test_a_slow_line_replays_after_a_gap():
    s = schedule([1500, 1000], slow=[True, False])
    w = s.lines[0]
    assert (w.slow_start_ms, w.slow_ms) == (1500 + 1500 + 350, 2000)
    assert s.lines[1].start_ms == 3350 + 2000 + 350
    assert Cue(3350, 0, 0.75) in s.cues

def test_listen_first_plays_every_line_then_a_bridge():
    s = schedule([1000, 1000], listen_first=True)
    assert [w.start_ms for w in s.listen] == [1500, 2850]
    assert s.bridge_ms == 3850 + 350 and s.lines[0].start_ms == s.bridge_ms + 700

def test_recap_and_quiz_follow_the_dialogue():
    s = schedule([1000, 1200], recap=True, quiz_line=1)
    end = s.lines[-1].end_ms
    assert s.recap_ms == end + 350
    assert (s.quiz.start_ms, s.quiz.reveal_ms) == (s.recap_ms + 3000, s.recap_ms + 3000 + 3000)
    assert s.quiz.end_ms == s.quiz.reveal_ms + 1200 + 800 and s.duration_ms == s.quiz.end_ms + 900
    assert s.cues[-1] == Cue(s.quiz.reveal_ms, 1, 1.0)

@pytest.mark.parametrize("listen", [False, True])
@pytest.mark.parametrize("slow", [[False] * 3, [True, False, True]])
@pytest.mark.parametrize("quiz", [None, 0])
def test_cues_never_overlap_and_fit(listen, slow, quiz):
    speech = [900, 1300, 700]
    s = schedule(speech, slow=slow, listen_first=listen, recap=True, quiz_line=quiz)
    ends = [c.at_ms + round(speech[c.line] / c.rate) for c in s.cues]
    assert all(e <= n.at_ms for e, n in zip(ends, s.cues[1:])) and ends[-1] <= s.duration_ms

def test_quiz_line_rule():  # most keys, earliest tie; no keys → longest meaning; False → None; int → as given
    ...

def test_strings_fall_back_to_english_and_format_the_level():
    assert strings_for("fr", "N5")["follow"] == "Follow for daily N5"
    assert strings_for("en", "")["follow"] == "Follow for more" and strings_for("ja-JP", "N5")["recap"] != STRINGS["en"]["recap"]
```

- [ ] **Step 2: Run** `uv run pytest tests/test_dialoguereel_schedule.py tests/test_dialoguereel_models.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Translations for ja, ko, es and zh should be natural, short and in a friendly tone. In ja, `follow` is "毎日の{level}はフォローしてね".
- [ ] **Step 4: Run.** Expected: PASS, and `uv run pytest -q -m "not slow"` passes.
- [ ] **Step 5: Commit:** `git commit -m "dialoguereel: key words, emotions, slow lines, listen-first and quiz in the script and schedule"`

---

### Task 2: Key-word matching and JLPT levels

**Files:**
- Create: `reelmachine/recipes/dialoguereel/keys.py`, `levels.py`
- Test: `tests/test_dialoguereel_keys.py`

**Interfaces:**
- Consumes: `parse_ruby`, `plain_text` (ruby.py); `KeyWord` (Task 1).
- Produces (`keys.py`):
  - `KeySpan(index: int, start: int, end: int)`, frozen, holding character offsets in the line's plain text.
  - `match_keys(text: str, keys: Sequence[KeyWord]) -> list[KeySpan | None]`, one entry per key in order.
  - Matching rule:
    - Search for the key's text in the ruby-stripped plain text.
    - If that fails, search for its stem. In Japanese (the key contains kana or kanji), the stem is the text minus its final kana when it is longer than one character. In Latin script, it is the lower-cased text minus 2 characters when it is 6 or more long, minus 1 when it is 4–5 long, and the whole text otherwise. The Latin search is case-insensitive and anchored to the start of a word.
    - A match never overlaps an earlier key's span; take the next occurrence.
    - Return `None` when nothing matches.
- Produces (`levels.py`): `jlpt_level(text: str, reading: str | None = None) -> int | None`, which returns 5…1 (5 = N5). It looks up `text` in the vocab's first or second column, then `reading`. When a word has several levels, it takes the easiest (the highest number). The JSON is loaded once with `functools.cache`. Also `n_level(label: str) -> int | None`: "N5"/"n3" → 5/3, anything else → None.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_dictionary_form_matches_its_conjugation():
    assert match_keys("手伝{てつだ}いましょうか。", [kw("手伝う")]) == [KeySpan(0, 0, 2)]

def test_ruby_is_stripped_before_matching(): ...
def test_latin_prefix_match(): assert match_keys("¿Puedo ayudarte?", [kw("ayudar")])[0] == KeySpan(0, 7, 11)
def test_a_repeated_word_and_overlapping_keys(): ...   # second key takes the next free occurrence
def test_no_match_is_none(): ...

def test_jlpt_levels():
    assert jlpt_level("あさって") == 5 and jlpt_level("zzzz") is None
    assert n_level("N4") == 4 and n_level("TOPIK 2") is None
```

- [ ] **Step 2: Run.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "dialoguereel: key words found in their lines, and JLPT levels"`

---

### Task 3: The full lint

**Files:**
- Modify: `reelmachine/recipes/dialoguereel/lint.py`, `stages.py` (`finding_path` for the new rules)
- Test: `tests/test_dialoguereel_lint.py` (extend)

**Interfaces:**
- Consumes: `match_keys`, `jlpt_level`, `n_level` (Task 2); `estimate`, `quiz_line_for` (Task 1); `plain_text`, `parse_ruby`.
- Produces: `lint.check(inputs, speakers)` gains the rules in Global Constraints. `no-title` is skipped when `mode == "cast"`.
  - Non-Latin script means any `ch.isalpha()` with `ord(ch) > 0x024F`.
  - CJK means a Han, kana or Hangul character.
  - `finding_path`:
    - `quiz-line` → `quiz`
    - `key-missing` / `level` → `lines[i].keys[j]`; add `key: int | None = None` to `Finding`
    - `no-reading` → `lines[i].reading`
    - `long-line` → `lines[i].text`
    - `same-speaker` → `lines[i].speaker`
    - `length` / `key-count` → `lines`
- The JLPT rule runs only when `language` starts with `ja` and `n_level(inputs.level)` is not None.

- [ ] **Step 1: Write the failing tests.** Write one failing case and one passing case per rule:

```python
def test_no_reading_on_japanese_but_not_spanish(): ...
def test_long_line_limits_by_script(): ...      # 23 kana warns, 22 does not; 43 Latin warns
def test_length_window(): ...                    # 3 short lines → under 15 s warns
def test_key_missing_names_its_path(): ...       # path "lines[1].keys[0]"
def test_level_flags_a_harder_word():            # an N2 word in an N5 reel warns; an unrated word does not
def test_key_count(): ...                         # 1 key warns, 2 does not, 7 warns
def test_same_speaker_run(): ...                  # 4 in a row warns, 3 does not
def test_quiz_line_out_of_range_is_an_error(): ...  # quiz=3 on 3 lines → error, path "quiz"; quiz=0 ok
def test_reference_dialogue_regression():        # spec §8 fixture: no title → no-title error; no keys → key-count warning
```

- [ ] **Step 2: Run.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "dialoguereel: the full script lint with the JLPT level check"`

---

### Task 4: The voice track, sound effects, storyboard and captions

**Files:**
- Modify: `reelmachine/recipes/dialoguereel/stages.py`, `models.py` (`LineWindowModel` + section fields on `NarrationOutput`)
- Test: `tests/test_dialoguereel_engine.py` (slow, extend), `tests/test_dialoguereel_probe.py` (extend)

**Interfaces:**
- Consumes: `schedule(...)`, `Cue`, `QuizWindow`, `quiz_line_for` (Task 1); `pad_speech`, `silence`, `synth_sfx`, `mix`, `ffmpeg.concat_audio`.
- Produces:
  - **`NarrationOutput`** gains `listen: list[LineWindowModel] = []`, `bridge_ms: int | None = None`, `recap_ms: int | None = None` and `quiz: dict | None = None` (the QuizWindow fields). `LineWindowModel` gains `slow_start_ms`/`slow_ms`. `plan_of(narration)` rebuilds the full `Schedule`.
  - **Voice track:**
    - Each line is spoken once, as in Plan 1. A slow cue uses an `atempo=0.75` copy of that line's wav, made once per line.
    - The track is built by walking `plan.cues`: silence up to `at_ms`, then the clip. Tail silence runs to `duration_ms`.
    - The joined track's length is `duration_ms` within 30 ms.
  - **SFX cues in `RenderStage`:**
    - `pop` at the drop-ins (Plan 1)
    - `whoosh` at `bridge_ms` and `recap_ms`
    - `pop` at each countdown second (`quiz.start_ms + 0/1000/2000`)
    - `ding` at `quiz.reveal_ms`
  - **Storyboard frames:** the hook; the middle of the listen pass when present; each line at `meaning_ms + 300`; `recap_ms + 1500`; `quiz.start_ms + 1500`; `quiz.reveal_ms + 300`.
  - **`script.resolved.json`** gains `sections: {"listen": [...], "bridgeMs", "recapMs", "quiz"}` and, per line, `keys: [{index, start, end} | null]` (from `match_keys`).
  - **`caption_text`** collapses only `\r`/`\n` runs and keeps U+3000. Test: `caption_text("はい　そうです", "Yes  that")` gives `"はい　そうです\nYes  that"`.
  - **Captions** cover the dialogue lines and the quiz answer, which is the line's caption at the reveal.

- [ ] **Step 1: Write the failing tests.** Fast: `test_caption_text_keeps_full_width_spaces`, and `test_plan_of_round_trips_every_section` (build a `NarrationOutput` by hand). Slow (fake voice, fake renderer):

```python
def test_full_with_every_section_has_a_matching_voice_track(tmp_path):
    job = run(tmp_path, EXAMPLE_P2 | {"mode": "full", "flow": "listen_first"})   # keys, a slow line, quiz true
    r = resolved(job); assert r["sections"]["quiz"] and r["sections"]["recapMs"] and r["sections"]["listen"]
    assert abs(ffprobe(artifact(job, "reel.mp4")).duration_ms - r["durationMs"]) < 100

def test_storyboard_has_the_section_frames(tmp_path): ...   # sheet tiles = 1 + 1 + lines + 3
```

- [ ] **Step 2: Run.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run** the fast suite and `uv run pytest -m slow tests/test_dialoguereel_engine.py -v`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "dialoguereel: the voice track follows the sections, with slow replays, quiz and sound cues"`

---

### Task 5: Key chips, emotions, slow tag and readable history

Build it in the browser first, as Plan 1 Task 6 did: open `index.html?t=<s>` with headless Chrome and look at frames before writing the snapshot tests.

**Files:**
- Modify: `compose.py` (line document fields), `runtime/pure.mjs`, `runtime/main.mjs`, `runtime/reel.css`, `runtime/tests/pure.test.mjs`
- Test: `tests/test_dialoguereel_compose.py` (extend)

**Interfaces:**
- Consumes: `match_keys`, `jlpt_level` (Task 2); `Schedule` with `slow_*` (Task 1).
- Produces (document, per line):
  - `keys: [{text, reading, meaning, level, start, end}]`. Only matched keys are included. `level` is an int or null, and is filled only for `ja`.
  - `emotion`
  - `slowStartMs`, `slowEndMs` (null when not slow)
- Produces (`pure.mjs`):
  - `keyVisible(ms, line, key, total) -> number` returns the chip's 0..1 entry progress. It starts when `litChars(ms, total, line.words) > key.start`, pops in over 220 ms with `backOut`, and stays until the line's bubble exits.
  - `emotionParts(emotion, talking) -> {eyes, mouth, extras: string[], emoji, tiltDeg}`, following the emotion table in Global Constraints. `mouth` is `"open"`, `"closed"` or `"smile"`; talking always wins for the mouth.
  - `emojiPop(ms, startMs) -> {y, scale, alpha}`: 600 ms up and fade, starting at the line start.
  - `slowActive(ms, line) -> boolean`.
  - `rowFit(textLen, meaningLen, widthPx) -> {textPx, meaningPx}`. It shrinks both down to 0.5× before any ellipsis.
- **Look:**
  - Key words get a 6 px accent underline under their character range, drawn in the bubble's text.
  - Chips stack in the band between the bubble and the characters, from y ≈ 1000 down. Each chip shows the word (ruby reading above, as in the bubble), "·", the meaning, and an `N4`-style tag when it has a level. Chips are pill-shaped, with an accent border and 52 px text.
  - During a slow replay: a "🐢 0.75×" tag on the bubble, the karaoke re-runs at the slow timing, and the speaker's mouth flaps at the slow rate.
  - Emotion emoji rise from above the speaker's head.

- [ ] **Step 1: Write the failing Node tests** for the five functions. Key cases:
  - The chip is hidden before its characters are lit and fully visible 220 ms after.
  - Talking overrides the smile mouth.
  - `thinking` gives `tiltDeg: 8`.
  - `rowFit` never goes below 0.5×.
- [ ] **Step 2: Run.** Expected: FAIL. **Step 3: Implement `pure.mjs`.** **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Write the failing Python tests.** The document includes matched keys only, with offsets, and the level only for `ja`. `emotion` and the slow times pass through.
- [ ] **Step 6: Implement** `compose.py`, `main.mjs` and `reel.css`, and tune by eye at the line start, a chip's pop, mid-slow-replay and each emotion. HyperFrames lint must stay at 0 errors. **Step 7: Run** the Node, fast and dialoguereel slow suites. Expected: PASS.
- [ ] **Step 8: Commit:** `git commit -m "dialoguereel: key-word chips, emotions, slow replays and history that fits"`

---

### Task 6: Listen-first, bridge, recap and quiz screens

**Files:**
- Modify: `compose.py` (document sections + strings), `runtime/pure.mjs`, `runtime/main.mjs`, `runtime/reel.css`, `runtime/tests/pure.test.mjs`
- Test: `tests/test_dialoguereel_compose.py` (extend)

**Interfaces:**
- Consumes: `Schedule.listen/bridge_ms/recap_ms/quiz` (Task 1); `strings_for` (Task 1); the per-line `keys` (Task 5).
- Produces:
  - **Document:** `strings` (from `strings_for`), `listen: [{startMs, endMs}]`, `bridgeMs`, `recapMs`, `recap: [{text, runs, reading, meaning, level}]` (at most 6, the matched keys in order), and `quiz: {line, startMs, revealMs, endMs} | null`.
- Produces (`pure.mjs`):
  - `section(ms, doc) -> "hook" | "listen" | "bridge" | "dialogue" | "recap" | "quiz" | "tail"`.
  - `countdown(ms, quiz) -> {n: 3|2|1|0, ring: 0..1}`.
  - `recapCard(ms, recapMs, index) -> {y, alpha}`, staggered by 120 ms.
- **Look:**
  - **Listen pass:** the bubble shows the line blurred (CSS `filter: blur(14px)`) with no reading or meaning. The banner shows `strings.listen` and the speaker still animates.
  - **Bridge:** a full-width card with `strings.bridge` that slides in and out over `BRIDGE_MS`.
  - **Recap:** the title `strings.recap`, then up to 6 cards in a 2-column grid, each with the word (ruby), reading, meaning and level tag. The characters stay below and nod.
  - **Quiz:** `strings.quiz` with the meaning in big text and a 3-2-1 ring. At the reveal, the line appears with ruby and reading, and the speaker of that line talks. Then `strings.cta` and `strings.follow` show.
  - **Tail:** the last frame returns to the hook's background and character positions, so the reel loops.

- [ ] **Step 1: Write the failing Node tests.** `section` must return the right name at each boundary of a document built from `schedule([1000,1000], listen_first=True, recap=True, quiz_line=0)`; Task 6 adds a small JSON fixture for this. `countdown` gives 3/2/1 per second and 0 after the reveal. Recap cards are staggered.
- [ ] **Step 2: Run.** Expected: FAIL. **Step 3: Implement `pure.mjs`.** **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Write the failing Python tests.** A document with `quiz: false` has `quiz: null` and no `ding`. Strings follow `meaning_language`. The recap is capped at 6.
- [ ] **Step 6: Implement** `compose.py`, `main.mjs` and `reel.css`, and tune by eye at every section. **Step 7: Run** all suites. Expected: PASS.
- [ ] **Step 8: Commit:** `git commit -m "dialoguereel: listen-first pass, recap cards and the your-turn quiz"`

---

### Task 7: Eight more built-in characters

**Files:**
- Create: `reelmachine/recipes/dialoguereel/characters/{onigiri,dango,boba,cat,shiba,penguin,bunny,kimchi-jar}.svg`
- Modify: `characters/index.json`
- Test: `tests/test_dialoguereel_svg.py` (the built-in tests already parametrize over `builtin_names()`; extend them if they hard-code names)

**Interfaces:**
- Consumes: the contract and `check_svg`, `inline_svg`, `load_builtin`, `recolour` (Plan 1).
- Produces: each name in `builtin_names()` with a one-line description and a `role` (kid, parent, friend, teacher, shopkeeper or pet).

- [ ] **Step 1: Write the failing tests.**
  - Every built-in passes `check_svg` with every optional part.
  - Every pair of built-ins inlines with distinct ids.
  - Every built-in recoloured with all three colours (`#123456`, `#abcdef`, `#fedcba`) still passes.
  - `builtin_names()` has 10 entries.
- [ ] **Step 2: Run.** Expected: FAIL. **Step 3: Draw each character** in the house style of `cupcake.svg`/`mug.svg`. Render all 10 in neutral, talk, blink, happy and surprised states on `#4b2bff` and look at them before committing. **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "dialoguereel: eight more built-in characters"`

---

### Task 8: The guide, the example and the docs entry

**Files:**
- Modify: `reelmachine/recipes/dialoguereel/guide.py`, `recipe.py` (`EXAMPLE`), `docs/RECIPES.md`
- Test: `tests/test_dialoguereel_mcp.py` / `tests/test_dialoguereel_probe.py` (extend)

**Interfaces:**
- Produces: `GUIDE` gains these keys:
  - `situations`: about 30 entries for N5–N3, each `{"level", "situation", "example_title"}`.
  - `writing`: natural lines; polite or casual chosen deliberately; one new grammar point per reel; 2–6 keys; the `slow` flag for the hardest line at N5; when to use `listen_first`.
  - `hooks`: learner hook-title formulas with examples.
  - `endings`: quiz and CTA.
  - `emotions`: the table from Global Constraints.
  - `levels`: the ladders for JLPT, TOPIK and CEFR, and that the JLPT check runs only for Japanese.
  - `workflow`: updated for the new options.
- `EXAMPLE` keeps the reference dialogue. It adds keys on 4–5 lines, two emotions, `slow` on line 2, `quiz: true` and `scene`.
- `docs/RECIPES.md` gets one dialoguereel section, in the same shape as the other recipes' entries, pointing to the guide.

- [ ] **Step 1: Write the failing tests.**
  - The example probes clean, with no `key-missing` or `level` findings.
  - Every `situations` entry has all three fields, and there are at least 25.
  - The guide mentions `listen_first`, `slow`, `keys`, `quiz` and `emotion`.
- [ ] **Step 2: Run.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Look at the real reel.**
  - Render the example in full with `flow: "listen_first"` and macOS Japanese voices, through HyperFrames.
  - Make a contact sheet at 1 fps and look at every section.
  - Fix anything broken with small, tested changes.
  - Run `uv run pytest -m slow tests/test_dialoguereel_*.py -v`.
- [ ] **Step 6: Commit:** `git commit -m "dialoguereel: the full guide, a richer example and the recipes doc"`
