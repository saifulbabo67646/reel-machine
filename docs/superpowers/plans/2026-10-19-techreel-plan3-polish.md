# techreel Plan 3 — Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship-ready techreel: a chrome-liquid orb and denser stage that match the references, a music bed, `post.md`, the full `verify`, documentation, and three rendered example reels (Hindi, English, Japanese) with Supertonic.

**Architecture:** No new stages. `post.py` (new) writes `post.md` in compose; `verify.py` grows checks reusing `dialoguereel.verify` helpers; music reuses `mapreel`'s `check_music` + `explainer3d.music.synth_music`.

**Tech Stack:** as Plans 1–2.

**Spec:** `docs/superpowers/specs/2026-10-19-techreel-design.md` (§5.3, §6 Fonts, §8 artifacts/verify, §11, §13 Plan 3).

## Global Constraints

- Plans 1–2 Global Constraints still bind.
- No bundled fonts: docs list the packages (`fonts-noto-core` on Debian/Ubuntu; macOS ships Kohinoor Devanagari and Hiragino).
- `post.md` never contains the `say` text (the Devanagari TTS spelling); it uses captions (`text`).
- The example reels are original; no real creator names/handles.

## Review Focus

1. **`music` pointing at a missing file** — `InvalidInput` with path `music` at the script stage, not an ffmpeg crash at render. Test in Task 2.
2. **`post.md` for a reel without `creator`/`sources`** — still well-formed, no "None". Test in Task 3.
3. **`verify` on a storyboard run** — ok without checks (no reel), never raises. Test in Task 4.
4. **An `accent` override with poor contrast on `mono`** (e.g. `#111111`) — the accent ink falls back to readable text (luminance check) so accent words stay legible. Test in Task 1.
5. **Japanese reel captions** — chunking ≤ 14 chars survives `merge_short`. Test in Task 5 (examples render).

---

### Task 1: Look polish — the liquid chrome orb and a fuller stage

**Files:** Modify `runtime/hero.mjs`, `runtime/reel.css`, `runtime/main.mjs`, `compose.py` (`look_tokens`); Test node tests, `tests/test_techreel_compose.py`.

- Orb: closer to the reference (`ref/a/sheet_01.jpg`): a bright mirror sphere — environment reflection (a blurred horizon gradient band warped by `feTurbulence` + `feDisplacementMap`, `baseFrequency` and `seed` driven from `t`), a hard white specular highlight top-left, a soft accent rim light, a subtle inner caustic swirl; no dark "marble" bands. Compare snapshots side by side with the reference frame and iterate.
- Stage density: block components scale up by default so the stage looks full (natural sizes ×1.15 for `counter`, `repo`, `window`, `terminal`, `chat`, `dialog`, `receipts`, `people`) while the stack's fit logic still applies; heading max size 104 px.
- `look_tokens`: compute `accentInk` from the accent's relative luminance (black ink when luminance > 0.45, else white); if the accent's contrast against `bg` is < 3:1, lighten it toward white until it reaches 3:1 (Review Focus 4).
- [ ] Steps: failing compose tests for `accentInk`/contrast → implement → PASS; snapshots LOOKed at against the reference sheets for each look (`mint`, `ember`, `violet`, `mono`) → Commit `techreel: a liquid chrome orb, a fuller stage and readable accents`.

### Task 2: Music bed

**Files:** Modify `stages.py` (`ScriptStage` checks music, `RenderStage` mixes it), Test `tests/test_techreel_stages.py`.

- `music`: `"auto"` → `explainer3d.music.synth_music` bed of the reel's length (as mapreel); a local file path → used (local runs only, like mapreel's `check_music` — reuse it, generalised if needed); missing file → `InvalidInput(path="music")` at the script stage (Review Focus 1). The mix ducks under the voice (`mix(..., music, ...)` already sidechains).
- [ ] Steps: failing tests (missing file refused at script stage; `auto` produces a non-silent bed in a fake-renderer full run) → implement → PASS → Commit `techreel: a music bed under the voice`.

### Task 3: `post.md`

**Files:** Create `reelmachine/recipes/techreel/post.py`; Modify `stages.py` (ComposeStage writes `artifacts/post.md`), `recipe.py` (artifacts list); Test `tests/test_techreel_post.py`.

**Interfaces:** `post_markdown(inputs: TechReelInputs) -> str` — sections: the hook (first beat's first caption, `*` stripped), a 3–5 line summary (the first caption of each non-hook beat, up to 5), the CTA line (the cta beat's caption lines joined), "Sources:" with each URL as a bullet (omitted when none), then 8–12 hashtags: from a per-language base set (`hi`: `#ai #tech #hindi #aitools #techhindi #opensource`; `en`: `#ai #tech #aitools #opensource #llm #coding`; `ja`: `#AI #テック #生成AI #プログラミング #オープンソース`; others: the `en` set) plus up to 4 tags made from the title words (letters/digits only, ≥ 3 chars). Reuse `mapreel.sources._clean` for text cleaning as dialoguereel's post does.
- [ ] Steps: failing tests (fixture reel → expected sections; no creator/sources → no "None", no empty Sources header; `say` text never appears) → implement → PASS → Commit `techreel: post.md with a caption, sources and hashtags`.

### Task 4: The full verify

**Files:** Modify `verify.py`; Test `tests/test_techreel_verify.py` (slow, fake renderer + a real-render case skipped without HyperFrames).

- Checks (full mode): format and audio (existing), duration (existing), `silence` — no stretch > 1000 ms without voice in the narration track outside the outro (reuse `dialoguereel.verify.check_silence` logic adapted to techreel's plan: build windows from beat/line slots), `visible` — at each beat's midpoint the stage band of the rendered video differs from the bare background (mean abs diff vs a background-only reference frame from a project with no beats > 3), `renderer` (manifest names the bound renderer, as dialoguereel). Storyboard mode → `ok=True` with a note (Review Focus 3).
- [ ] Steps: failing tests → implement → PASS → Commit `techreel: verify checks silence and that every beat shows something`.

### Task 5: Docs and the three example reels

**Files:** Create `docs/TECHREEL.md`; Modify `README.md` (recipe table row, a quick-start section mirroring dialoguereel's, layout entry), `docs/MCP.md` (a "techreel workflow" paragraph: `describe_recipe("techreel")` → research → write beats → `probe` → `storyboard` → `full`), `docs/RECIPES.md` if it lists recipes, `THIRD_PARTY_NOTICES.md` (note that Supertonic voices are OpenRAIL-M and fonts are deployment-supplied).

- `docs/TECHREEL.md`: what it makes, the script model (beats, lines with `say`/`text`, visuals, cues), the component and hero reference (link to the guide), languages and Supertonic setup (`REEL_TTS=supertonic`, `REEL_SUPERTONIC_URL`, voices, the measured digit/Latin pitfalls), fonts per OS, looks, music, artifacts, lint rules, a troubleshooting section.
- Render all three examples (`examples/hi-pocket-llm.json`, `en-what-is-rag.json`, `ja-tiny-models.json`) in `full` mode with Supertonic (`supertonic:M1` for hi/en, `supertonic:F1` for ja) to the scratchpad; make contact sheets; LOOK; transcribe each with whisper-cli to confirm intelligibility; fix example scripts (not code) where something reads badly; record durations and findings in the report. Do not commit MP4s.
- [ ] Steps: write docs → render and check → Commit `techreel: docs and example reels`.
