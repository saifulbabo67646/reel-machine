# dialoguereel Plan 3 (polish) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish dialoguereel. Add `pastel` and `night` looks, `post.md`, a `verify` of the finished reel, image characters (OpenAI and caller PNGs, with quota), docs and two example reels (Japanese and Korean).

**Architecture:** Everything lives in `reelmachine/recipes/dialoguereel/`, plus a settings field, the MCP `make_cast` reply and the docs. A new `cast` stage sits between `script` and `narration`. It is a pass-through for SVG casts. It makes or loads each image character's pose PNGs into the caller's cast folder. Compose puts them in the project as `<img>` poses, and the runtime swaps them.

**Tech Stack:** Python 3.11, pydantic 2, httpx, Pillow, numpy, ffmpeg, HyperFrames, node `--test`.

**Spec:** `docs/superpowers/specs/2026-10-18-dialoguereel-design.md` (§4.3, §5 looks, §6 verify/post.md, §7, §9, §10, §11 item 3)

## Global Constraints

- **No network or render in the default suite.** Real renders are `-m slow`, and OpenAI is only reached through a stubbed `httpx` transport.
- **Image generation:** `gpt-image-1`, `background: "transparent"`, `size: "1024x1024"`. The key comes from `REEL_OPENAI_API_KEY`; the model can be overridden by `REEL_OPENAI_IMAGE_MODEL`.
- **Poses:** `neutral`, `talk`, `happy`, `surprised`. `neutral` is generated first; each other pose is an edit of the neutral image. A failed non-neutral pose falls back to `neutral` with a warning. A failed `neutral` is an error.
- **Images:** normalised to PNG, trimmed to their alpha bounding box, at most 1024 px on the longest side.
- **Caller PNGs:** local paths are allowed only for local runs (the job's caller is `""` or `"local"`). Remote callers may use http(s) URLs only, and the host must resolve to public addresses. Downloads are capped at 8 MB, and SVG is refused.
- **Quota:** each image call made is recorded on `<caller dir>/quota/<provider>.json`. That is the ledger the engine already checks against `CallerPolicy.quotas` at submit. The `fake` provider records nothing.
- **`probe` never spends image credits and never fetches.** A cast whose poses are already on disk costs nothing.
- **Hashtags:** 5–10 in `post.md`, each `#` followed by no spaces.
- **`verify`:**
  - Format 1080×1920 with audio.
  - Duration within 100 ms of the narration.
  - The reel is audible.
  - No voice-track silence longer than 1.5 s inside the dialogue span.
  - Each line's text is visible at its meaning time.
  - The manifest names the renderer.
- Match the surrounding code's style: terse docstrings, comments only where the why is not obvious.

## Review Focus

1. **A remote caller's pose URL pointing at an internal host** (`http://127.0.0.1/…`, `http://169.254.169.254/…`, `http://10.0.0.5/…`) or at a local path must be refused with `InvalidInput` naming the pose. Task 4 adds `test_remote_caller_refuses_private_and_local_poses`.
2. **Re-running a reel with an image cast already on disk** must spend no image calls (ledger unchanged), and a pose missing from an earlier failure is retried alone. Task 5 adds `test_cast_stage_reuses_poses_and_retries_only_missing`.
3. **The `pastel` look has dark text on a light background.** No hard-coded white or navy may remain in `reel.css`. Task 1 adds `test_css_has_no_look_literals`.
4. **Line text with markdown-active characters** (`# Title`, `*bold*`, `<b>`, `[x](y)`) must come out inert in `post.md`. Task 2 adds `test_post_escapes_markdown`.
5. **A `listen_first` reel** must pass the silence check. The bridge card and gaps are short, and the recap, quiz and hook are outside the dialogue span. Task 3 adds `test_dialogue_span_listen_first`.

---

### Task 1: The `pastel` and `night` looks

**Files:**
- Modify: `reelmachine/recipes/dialoguereel/models.py` (`Look`)
- Modify: `reelmachine/recipes/dialoguereel/compose.py` (`LOOKS`)
- Modify: `reelmachine/recipes/dialoguereel/runtime/reel.css`
- Modify: `reelmachine/recipes/dialoguereel/runtime/main.mjs` (`build`)
- Test: `tests/test_dialoguereel_compose.py`

**Interfaces:**
- Produces: `Look = Literal["pop", "pastel", "night"]`. Every `LOOKS[name]` has exactly the keys `bg, text, reading, meaning, accent, bubble, ink, shade`. `ink` is the text colour on accent fills. `shade` is an `"r,g,b"` triple for shadows and dark panels.

- [ ] **Step 1: Write the failing tests**

```python
LOOK_KEYS = {"bg", "text", "reading", "meaning", "accent", "bubble", "ink", "shade"}

def test_every_look_has_every_token():
    assert set(compose.LOOKS) == {"pop", "pastel", "night"}
    for look in compose.LOOKS.values():
        assert set(look) == LOOK_KEYS
        assert re.fullmatch(r"\d{1,3},\d{1,3},\d{1,3}", look["shade"])

def test_reel_document_carries_the_look():  # build a doc with look="night" from the existing fixture helper
    assert doc["look"] == compose.LOOKS["night"]

def test_css_has_no_look_literals():
    css = (compose.RUNTIME_DIR / "reel.css").read_text()
    body = css.split("#root {", 1)[1].split("}", 1)[1]   # the defaults block on #root may name pop's values
    assert "rgba(20,0,90" not in body and "rgba(10,0,80" not in body and "#241466" not in body
```

- [ ] **Step 2: Run them to see them fail.**
  - Run: `pytest tests/test_dialoguereel_compose.py -k "look or literals" -v`
  - Expected: FAIL.

- [ ] **Step 3: Implement the looks.**
  - Values (use them verbatim):
    - `pop`: the existing tokens plus `ink "#241466"` and `shade "20,0,90"`.
    - `pastel`: `bg "#fff1e0"`, `text "#3b2a4d"`, `reading "#c2577f"`, `meaning "#5b55c8"`, `accent "#ffb0c8"`, `bubble "rgba(255,255,255,0.78)"`, `ink "#3b2a4d"`, `shade "150,105,80"`.
    - `night`: `bg "#0c1230"`, `text "#f4f6ff"`, `reading "#5ef2ff"`, `meaning "#b9a9ff"`, `accent "#ff4fd8"`, `bubble "rgba(255,255,255,0.08)"`, `ink "#0c1230"`, `shade "0,0,0"`.
  - In `reel.css`, add `--ink: #241466; --shade: 20,0,90;` to the `#root` defaults. Replace every `rgba(20,0,90,A)` and `rgba(10,0,80,A)` with `rgba(var(--shade),A)`, and every `#241466` with `var(--ink)`.
  - In `main.mjs` `build`, also set `--ink` and `--shade` from `doc.look`.

- [ ] **Step 4: Run the compose tests and the runtime tests.**
  - Run: `pytest tests/test_dialoguereel_compose.py -q && node --test reelmachine/recipes/dialoguereel/runtime/tests/`
  - Expected: PASS.

- [ ] **Step 5: Commit** ("dialoguereel: pastel and night looks").

---

### Task 2: `post.md`

**Files:**
- Create: `reelmachine/recipes/dialoguereel/post.py`
- Modify: `reelmachine/recipes/dialoguereel/strings.py`: add a `"dialogue"` key to every language (en "Dialogue", ja "会話", ko "대화", es "Diálogo", zh "对话").
- Modify: `reelmachine/recipes/dialoguereel/stages.py`: ComposeStage writes `artifacts/post.md` when `mode != "cast"`.
- Modify: `reelmachine/recipes/dialoguereel/recipe.py`: add `"post.md"` to `artifacts`.
- Test: `tests/test_dialoguereel_post.py`

**Interfaces:**
- Consumes: `strings_for(meaning_language, level)`, the keys `recap`, `follow`/`follow_plain` and `cta`; `compose.prompt_for`; `levels.primary_language`, `levels.jlpt_level`, `levels.is_japanese`; `ruby.parse_ruby`/`plain_text`; and `reelmachine.recipes.mapreel.sources._clean` (the one-line markdown escaper).
- Produces:
  - `hashtags(language: str, level: str) -> list[str]`
  - `post_markdown(inputs: DialogueReelInputs, cast: ResolvedCast) -> str`

The layout, in order:
1. The hook line: `title` plus ` scene` when set.
2. The prompt line, when `prompt_for` gives one.
3. A blank line.
4. `**<dialogue>**`.
5. Per line: `<speaker name>: <plain text>`, then the reading line (when set), then `→ <meaning>`, then a blank line.
6. When the reel has keys: `**<recap>**`, then one bullet per recap word (the same dedup and six-word cap as the reel), written as `- <plain> (<reading>) — <meaning>`, with ` · <JLPT level>` appended when rated.
7. A blank line, then the `cta` line and the follow line.
8. A blank line, then the hashtags joined by spaces.

Every text field passes through `_clean`. Hashtags:

| language | tags |
|---|---|
| `ja` | `#learnjapanese #japanese #日本語 #nihongo #japaneselanguage #studyjapanese`, plus `#JLPT<level>` and `#jlpt` when the level is N1–N5 |
| `ko` | `#learnkorean #korean #한국어 #koreanlanguage #studykorean`, plus `#TOPIK` when the level starts with "TOPIK" |
| `es` | `#learnspanish #spanish #español #spanishlanguage #aprenderespañol`, plus `#<level>` when it is a CEFR A1–C2 |
| `zh` | `#learnchinese #chinese #中文 #mandarin #studychinese`, plus `#<level>` when it matches `HSK\d` |
| other | `#languagelearning #learnlanguages #vocabulary #language #polyglot` |

Each list then gets `#languagelearning` appended, deduped in order and cut to 10. Any space is stripped from a level tag.

- [ ] **Step 1: Write the failing tests**

```python
def test_hashtags_japanese_n5():
    tags = post.hashtags("ja", "N5")
    assert "#JLPTN5" in tags and "#日本語" in tags and 5 <= len(tags) <= 10
    assert all(t.startswith("#") and " " not in t for t in tags)

@pytest.mark.parametrize("lang,level", [("ko", "TOPIK 1"), ("es", "A2"), ("zh", "HSK2"), ("de", ""), ("ja", "")])
def test_hashtags_count(lang, level):
    tags = post.hashtags(lang, level)
    assert 5 <= len(tags) <= 10 and len(set(tags)) == len(tags)

def test_post_for_the_example():          # recipe.EXAMPLE resolved through casts.resolve
    text = post.post_markdown(inputs, cast)
    assert text.startswith("Helping Mom at home 🧺\nCan you understand this?\n")
    assert "Cupcake: おかあさん。\nOkaasan.\n→ Mom" in text
    assert "- せんたく (sentaku) — laundry" in text
    assert text.rstrip().splitlines()[-1].startswith("#learnjapanese")

def test_post_escapes_markdown():          # a line whose text/meaning is "# Title", "*bold* <b> [x](y)"
    assert "\n# " not in text and "<b>" not in text and "](y)" not in text

def test_compose_writes_post_md_in_storyboard_not_cast(...)   # through ComposeStage with the existing stage fixtures
```

- [ ] **Step 2: Run them to see them fail.**
  - Run: `pytest tests/test_dialoguereel_post.py -v`
  - Expected: FAIL (no module `post`).

- [ ] **Step 3: Implement `post.py`, the strings and the ComposeStage write.**

- [ ] **Step 4: Run the tests to see them pass.**
  - Run: `pytest tests/test_dialoguereel_post.py tests/test_dialoguereel_compose.py -q`
  - Expected: PASS.

- [ ] **Step 5: Commit** ("dialoguereel: post.md with the dialogue, the words and hashtags").

---

### Task 3: `verify` the finished reel (and key the render cache by renderer)

**Files:**
- Create: `reelmachine/recipes/dialoguereel/verify.py`
- Modify: `reelmachine/recipes/dialoguereel/recipe.py` (`verify` calls `verify_dialoguereel`)
- Modify: `reelmachine/recipes/dialoguereel/stages.py`: the RenderStage cache path becomes `reels/<renderer name>/<key[:2]>/<key>.mp4`.
- Test: `tests/test_dialoguereel_verify.py` (unit), plus extend the slow test in `tests/test_dialoguereel_render.py`.

**Interfaces:**
- Consumes:
  - `mapreel.verify.check_audio(media, settings, label=)`
  - `ffmpeg.probe(path)`, which returns `.width .height .duration_s .has_video .has_audio`
  - `stages.plan_of(narration)`
  - `ctx.assets.get(asset_id)`
  - the outputs dict keyed by stage id (`"render"`, `"preview"`, `"compose"`, `"narration"`)
- Produces:
  - `SILENT_MAX_MS = 1500`, `SILENCE_DB = -50`, `TEXT_MIN = 1.5`, and the bubble band `TEXT_TOP, TEXT_BOTTOM = 372, 1000` (reel px).
  - `dialogue_span(plan: Schedule) -> tuple[int, int]`: from the first listen-pass (or first line) start to the last line's end, slow replay included.
  - `long_silences(silences: list[tuple[int, int]], span: tuple[int, int], max_ms: int = SILENT_MAX_MS) -> list[tuple[int, int]]`: each silence clipped to the span, keeping those longer than `max_ms`.
  - `parse_silences(stderr: str, total_ms: int) -> list[tuple[int, int]]`: reads ffmpeg `silencedetect` output. A `silence_start` with no end runs to `total_ms`.
  - `text_energy(band: np.ndarray) -> float`: the mean absolute horizontal grey difference.
  - `verify_dialoguereel(ctx, outputs) -> VerificationReport`. With no `render` output (cast or storyboard mode) it returns `ok=True, notes=<mode>`. Otherwise it runs the checks `format`, `duration`, `audio` (on the final video), `silence` (`silencedetect=n=-50dB:d=1.5` on the narration asset), `text` (a grey frame of 270×480 at each line's `meaning_ms + 300`, with the band rows scaled; each line must have `text_energy ≥ TEXT_MIN`) and `renderer` (the manifest's `renderer` is non-empty and equals the bound renderer's name). `render_mode="hyperframes"`.

- [ ] **Step 1: Write the failing unit tests**

```python
def test_long_silences_clips_to_span():
    assert verify.long_silences([(0, 1500), (2000, 4000), (9000, 9600)], (1500, 9200)) == [(2000, 4000)]

def test_parse_silences_open_end():
    err = "[silencedetect @ 0x1] silence_start: 1.2\n[silencedetect @ 0x1] silence_end: 3.0 | silence_duration: 1.8\n" \
          "[silencedetect @ 0x1] silence_start: 8.5\n"
    assert verify.parse_silences(err, 10_000) == [(1200, 3000), (8500, 10_000)]

def test_dialogue_span_listen_first():   # schedule([1000]*4, slow=[F,T,F,F], listen_first=True, recap=True, quiz_line=1)
    start, end = verify.dialogue_span(plan)
    assert start == plan.listen[0].start_ms
    assert end == max(w.slow_start_ms + w.slow_ms if w.slow_start_ms is not None else w.end_ms for w in plan.lines)
    assert verify.long_silences([], (start, end)) == []

def test_text_energy_flat_vs_text():
    flat = np.tile(np.linspace(40, 60, 270, dtype=np.float32), (60, 1))
    text = flat.copy(); text[20:40, ::6] = 255
    assert verify.text_energy(flat) < verify.TEXT_MIN < verify.text_energy(text)

def test_storyboard_verifies_without_a_video():
    assert verify.verify_dialoguereel(ctx, {"preview": ...}).ok
```

- [ ] **Step 2: Run them to see them fail.**
  - Run: `pytest tests/test_dialoguereel_verify.py -v`
  - Expected: FAIL.

- [ ] **Step 3: Implement `verify.py`, wire `recipe.verify` and change the cache path.**

- [ ] **Step 4: Extend the slow render test.** It must assert that `recipe.verify(...)` is `ok` with every named check present. Run it. If `TEXT_MIN` is wrong for the real frames, set it between the lowest line value measured and a frame with no bubble (measure one at `hook_ms * 0.5` in the band), and put both numbers in the report.
  - Run: `pytest tests/test_dialoguereel_verify.py -q && pytest tests/test_dialoguereel_render.py -m slow -q`
  - Expected: PASS.

- [ ] **Step 5: Commit** ("dialoguereel: verify the finished reel; the render cache is per renderer").

---

### Task 4: Image sources: the model, the generators and the pose files

**Files:**
- Modify: `reelmachine/recipes/dialoguereel/models.py`
- Create: `reelmachine/recipes/dialoguereel/images.py`
- Modify: `reelmachine/config.py`: `openai_api_key: str = ""` from `_env("REEL_OPENAI_API_KEY")`, and `openai_image_model: str = "gpt-image-1"` from `REEL_OPENAI_IMAGE_MODEL`, read the way the other `REEL_*` settings are.
- Test: `tests/test_dialoguereel_images.py`

**Interfaces:**
- Consumes: from `mapreel.stages`, `_download(ref) -> (bytes, content_type)`, `_normalise(data) -> png bytes` (≤1024 px, RGBA, refuses SVG), `IMAGE_MAX_BYTES` and `IMAGE_SUFFIXES`; `nadeshiko.QuotaLedger(path).record(n)`; `core.errors.InvalidInput` and `ProviderUnavailable`.
- Produces:
  - `models.Pose = Literal["neutral", "talk", "happy", "surprised"]`.
  - `models.ImageSource` (`extra="forbid"`) with these fields:
    - `kind: Literal["image"]`
    - `provider: Literal["openai", "fake"] | None = None`
    - `description: str | None` (1–300 chars)
    - `poses: dict[Pose, str] | None` (each value 1–500 chars)
    - `variant: int` (0–99, default 0)

    Its validator requires exactly one form: `provider` with `description`, or `poses`. `poses` must contain `neutral`, and `variant` is only meaningful with `provider`. `CharacterSource` becomes `BuiltinSource | SvgSource | ImageSource` (discriminated on `kind`).
  - In `images.py`:
    - `POSES = ("neutral", "talk", "happy", "surprised")`
    - `STYLE` and `POSE_PROMPTS` (copy below)
    - `class Generator(Protocol)`, with `name: str`, `generate(prompt: str) -> bytes` and `edit(image: bytes, prompt: str) -> bytes`
    - `OpenAIImages(api_key: str, model: str = "gpt-image-1", client: httpx.Client | None = None, base_url: str = "https://api.openai.com")`
    - `FakeImages()`
    - `generator_for(provider: str, settings) -> Generator`, which raises `ProviderUnavailable(..., hint="set REEL_OPENAI_API_KEY")` for `openai` with no key
    - `trim_png(png: bytes) -> bytes`: crops to the alpha bounding box, still at most 1024 px
    - `read_pose(ref: str, *, allow_local: bool, path: str) -> bytes`: fetched or read, then normalised and trimmed
    - `quota_ledger(caller_root: Path, provider: str) -> QuotaLedger`, at `<caller_root>/quota/<provider>.json`
    - `make_poses(source: ImageSource, folder: Path, *, settings, allow_local: bool, ledger: QuotaLedger | None, path: str) -> tuple[dict[str, Path], list[str]]`. It writes `<folder>/<pose>.png` and reuses existing files without any call. It returns the pose paths and the warnings.

Copy (verbatim):

```python
STYLE = ("A single cute kawaii sticker character, full body, centred, facing the viewer, thick white outline, "
         "flat pastel colours with one soft shade, big head, simple dot eyes, small mouth, pink blush cheeks, "
         "transparent background, no text. The character: ")
POSE_PROMPTS = {
    "talk": "The same character, identical in every way, with its mouth open as if talking.",
    "happy": "The same character, identical in every way, smiling happily with closed ^ ^ eyes.",
    "surprised": "The same character, identical in every way, surprised: wide eyes and a small round open mouth.",
}
```

Rules:
- **OpenAI requests:**
  - `generate`: `POST {base}/v1/images/generations`, JSON `{"model", "prompt", "size": "1024x1024", "background": "transparent", "n": 1, "output_format": "png"}`, with an `Authorization: Bearer` header.
  - `edit`: `POST {base}/v1/images/edits`, multipart with `image=("neutral.png", bytes, "image/png")` and the form fields `model`, `prompt`, `size` and `background`.
  - Both read `data[0].b64_json`.
  - HTTP 401, 403 and 429 (and other errors) raise `ProviderUnavailable` naming `openai` and the status, with no key in the message.
- **Prompts:**
  - The neutral prompt is `STYLE + description`, plus `f" (take {variant})"` when `variant` is set.
  - `FakeImages` draws a deterministic 512×512 RGBA PNG with Pillow: a filled ellipse coloured from `sha256(prompt)` on transparency, with an open mouth for edits whose prompt contains "talking".
- **The pose order:**
  - `neutral` first. A failure there raises `ProviderUnavailable` (when generated) or `InvalidInput` with `details.path=f"{path}.poses.neutral"` (when supplied).
  - The other poses follow in `POSES` order: edited from the neutral bytes when generated, or read from `poses[...]` when supplied and present.
  - A failed non-neutral pose adds the warning `f"{path}: the {pose} pose failed ({reason}); using neutral"` and is left out.
  - Each successful OpenAI call does `ledger.record(1)`. `fake` passes `ledger=None`.
  - Every file is written atomically.
- **`read_pose`:**
  - An http(s) URL: when `not allow_local`, refuse a host whose `socket.getaddrinfo` addresses include any that is not `ipaddress.ip_address(a).is_global`. Then `_download`, and refuse `image/svg+xml`.
  - Anything else is a local path, read only when `allow_local` (≤8 MB, an image suffix, not `.svg`). Otherwise raise `InvalidInput("local files are only read for local runs; pass an https URL", details={"path": path})`.

- [ ] **Step 1: Write the failing tests**

```python
def test_image_source_forms():
    ImageSource(kind="image", provider="openai", description="a cheerful onigiri")
    ImageSource(kind="image", poses={"neutral": "https://x/n.png"})
    for bad in ({"kind": "image"}, {"kind": "image", "provider": "openai"},
                {"kind": "image", "poses": {"talk": "a.png"}},
                {"kind": "image", "provider": "fake", "description": "x", "poses": {"neutral": "a.png"}}):
        with pytest.raises(ValidationError): ImageSource.model_validate(bad)

def test_openai_request_shape():          # httpx.MockTransport records requests, returns {"data":[{"b64_json": <png>}]}
    gen = images.OpenAIImages("sk-test", client=httpx.Client(transport=transport))
    gen.generate("p"); gen.edit(png, "q")
    assert seen[0].url.path == "/v1/images/generations" and json.loads(seen[0].content)["background"] == "transparent"
    assert seen[0].headers["authorization"] == "Bearer sk-test"
    assert seen[1].url.path == "/v1/images/edits" and b'name="image"' in seen[1].content

def test_openai_refusal_names_provider_not_key():   # 401 response
    with pytest.raises(ProviderUnavailable) as exc: gen.generate("p")
    assert "openai" in exc.value.message and "sk-test" not in exc.value.message

def test_missing_key_names_the_setting():
    with pytest.raises(ProviderUnavailable) as exc: images.generator_for("openai", settings_without_key)
    assert "REEL_OPENAI_API_KEY" in (exc.value.message + exc.value.hint)

def test_make_poses_fake_and_ledger(tmp_path):      # generator stubbed to count calls; ledger at tmp_path/quota/openai.json
    paths, warnings = images.make_poses(src_openai, tmp_path / "c", settings=s, allow_local=True, ledger=ledger, path="p")
    assert set(paths) == set(images.POSES) and warnings == [] and ledger_total(ledger.path) == 4
    images.make_poses(...same...)                   # all on disk: no call
    assert calls == 4 and ledger_total(ledger.path) == 4

def test_failed_pose_falls_back(tmp_path):          # edit() raises for "happy"
    assert "happy" not in paths and any("the happy pose failed" in w for w in warnings)

def test_trim_png_crops_to_alpha():                 # 400x400 transparent with a 100x50 opaque box -> 100x50
def test_remote_caller_refuses_private_and_local_poses():
    for ref in ("http://127.0.0.1/a.png", "http://169.254.169.254/a.png", "http://10.0.0.5/a.png", "/etc/passwd", "a.png"):
        with pytest.raises(InvalidInput) as exc: images.read_pose(ref, allow_local=False, path="cast.characters[0].source.poses.neutral")
        assert exc.value.details["path"].endswith("poses.neutral")
def test_local_caller_reads_a_png(tmp_path)
```

- [ ] **Step 2: Run them to see them fail.**
  - Run: `pytest tests/test_dialoguereel_images.py tests/test_dialoguereel_models.py -v`
  - Expected: FAIL.

- [ ] **Step 3: Implement the model, `images.py` and the settings.** Wrap the mapreel helpers' `ValueError`s as `InvalidInput` with the pose path.

- [ ] **Step 4: Run the tests to see them pass.**
  - Run: `pytest tests/test_dialoguereel_images.py tests/test_dialoguereel_models.py tests/test_config.py -q`
  - Expected: PASS.

- [ ] **Step 5: Commit** ("dialoguereel: image character sources, OpenAI and caller PNG poses").

---

### Task 5: The `cast` stage, image characters on the page

**Files:**
- Modify: `reelmachine/recipes/dialoguereel/models.py` (`ResolvedCharacter`)
- Modify: `reelmachine/recipes/dialoguereel/cast.py` (`resolve`)
- Modify: `reelmachine/recipes/dialoguereel/stages.py` (new `CastStage`; `NarrationStage` consumes its output)
- Modify: `reelmachine/recipes/dialoguereel/recipe.py` (the stage list)
- Modify: `reelmachine/recipes/dialoguereel/compose.py` (`_characters`, `_character_html`, `write_project`, `project_key`)
- Modify: `reelmachine/recipes/dialoguereel/runtime/pure.mjs`, `main.mjs`, `reel.css`
- Test: `tests/test_dialoguereel_cast.py`, `tests/test_dialoguereel_compose.py`, `runtime/tests/pure.test.mjs`

**Interfaces:**
- Consumes: Task 4's `images.make_poses`, `images.quota_ledger` and `images.POSES`; `casts.caller_root(ctx)`.
- Produces:
  - `ResolvedCharacter` gains `kind: Literal["svg", "image"] = "svg"` and `poses: dict[str, str] = {}` (pose → absolute PNG path), and `svg: str = ""`.
  - `cast.resolve` turns an image source into `kind="image"`, `svg=""`, `parts=` the poses it will have (`list(POSES)` when generated, else the supplied keys in `POSES` order) and `poses={}`. It fetches nothing.
  - `cast.cast_folder(root: Path, cast_id: str, char_id: str) -> Path`, which returns `<root>/casts/<cast_id>/<char_id>`.
  - `cast.poses_on_disk(root, resolved: ResolvedCast) -> int`: the number of image calls still needed. That counts the generated poses missing from disk; supplied poses count 0.
  - `CastStage`:
    - `id="cast"`, `output_model=ScriptOutput`, fed by `script`.
    - SVG characters pass through. Image characters get `make_poses` into `cast_folder(...)`, with `allow_local = ctx.job.caller in ("", "local")`. The ledger is `quota_ledger(caller_root, "openai")` for `openai`, else `None`. `path=f"cast.characters[{i}].source"`.
    - It sets `poses` and narrows `parts` to the poses made, and appends the pose warnings to `warnings`.
    - In `cast` mode it also copies each pose to `artifacts/cast-<char id>-<pose>.png`.
  - NarrationStage's `payload` is the `CastStage` output (same `ScriptOutput` type). In the recipe stage list, Narration has `feeds="cast"`.
  - Compose: an image character's doc entry is `{"id", "side", "kind": "image", "parts": [poses]}`, and an SVG one gets `"kind": "svg"`. Its HTML is `<div class="char {side} image" id="char-{id}">` followed by one `<img class="pose" data-pose="{pose}" src="./cast/{id}/{pose}.png" alt="">` per pose. `write_project` copies the PNGs to `dest/cast/<id>/<pose>.png`, and `project_key` hashes each PNG's bytes.
  - `pure.mjs` exports `imagePose(emotion, talking, poses)`, where `poses` is an array of names. It returns `"talk"` when `talking` and `"talk"` is in `poses`. Otherwise it returns `emotion` when that is `"happy"` or `"surprised"` and in `poses`. Otherwise `"neutral"`.
  - `main.mjs` for `kind === "image"`: there are no part lookups (no blinks or arms). Each frame shows only the `<img>` whose `data-pose` is `imagePose(faceEmotion, talking, c.parts)`. The hook greeting and cast sheet count as `"happy"`, the same rule as the SVG face. The whole-body motion is unchanged.
  - CSS: `.char.image img { position:absolute; inset:0; width:100%; height:100%; object-fit:contain; object-position:50% 100%; display:none; }`.

- [ ] **Step 1: Write the failing tests**

```python
def test_resolve_image_fetches_nothing():
    r = casts.resolve(spec_with_fake_image)
    assert r.characters[1].kind == "image" and r.characters[1].poses == {} and r.characters[1].parts == list(images.POSES)

def test_cast_stage_makes_fake_poses(tmp_path):            # ctx from the existing stage helpers, fake provider
    out = CastStage().run(ctx, script_out)
    char = out.cast.characters[1]
    assert set(char.poses) == set(images.POSES) and all(Path(p).is_file() for p in char.poses.values())

def test_cast_stage_reuses_poses_and_retries_only_missing(tmp_path):   # openai stubbed via a counting generator
    # run once (4 calls); delete happy.png; run again -> exactly 1 more call, ledger total 5

def test_cast_mode_publishes_pose_artifacts(tmp_path):
    assert (ctx.workdir / "artifacts" / "cast-mom-talk.png").is_file()

def test_image_character_html_and_key(tmp_path):
    html = (compose.write_project(doc, cast, tmp_path / "p") / "index.html").read_text()
    assert 'data-pose="talk" src="./cast/mom/talk.png"' in html and (tmp_path / "p/cast/mom/talk.png").is_file()
    # rewriting talk.png with other bytes changes compose.project_key(doc, cast)
```

```js
test("imagePose", () => {
  const all = ["neutral", "talk", "happy", "surprised"];
  assert.equal(imagePose("happy", true, all), "talk");
  assert.equal(imagePose("happy", false, all), "happy");
  assert.equal(imagePose("happy", true, ["neutral"]), "neutral");
  assert.equal(imagePose("sad", false, all), "neutral");
});
```

- [ ] **Step 2: Run them to see them fail.**
  - Run: `pytest tests/test_dialoguereel_cast.py tests/test_dialoguereel_compose.py -q; node --test reelmachine/recipes/dialoguereel/runtime/tests/`
  - Expected: FAIL.

- [ ] **Step 3: Implement it.** The existing SVG tests must stay green unchanged, apart from adding `"kind": "svg"` to expected doc entries.

- [ ] **Step 4: Run the whole dialoguereel suite and the runtime tests, then the slow render with a `fake` image character.** Add `test_render_with_an_image_character` (`-m slow`): a storyboard with one builtin character and one `{"kind":"image","provider":"fake","description":"a round dumpling"}`, asserting `storyboard.jpg` exists.
  - Run: `pytest tests -k dialoguereel -q && node --test reelmachine/recipes/dialoguereel/runtime/tests/ && pytest tests/test_dialoguereel_render.py -m slow -q`
  - Expected: PASS.

- [ ] **Step 5: Commit** ("dialoguereel: the cast stage and image characters on the page").

---

### Task 6: Probe, `make_cast` and the guide for images; small leftovers

**Files:**
- Modify: `reelmachine/recipes/dialoguereel/recipe.py` (probe, `cost`, `artifacts`)
- Modify: `reelmachine/mcp/tools.py` (the `make_cast` reply)
- Modify: `reelmachine/recipes/dialoguereel/lint.py` (no `no-title` in cast mode)
- Modify: `reelmachine/recipes/dialoguereel/guide.py` (sections `images`, `looks`, `post`)
- Test: `tests/test_dialoguereel_probe.py`, `tests/test_dialoguereel_mcp.py`, `tests/test_dialoguereel_lint.py`

**Interfaces:**
- Consumes: Task 5's `cast.poses_on_disk(root, resolved)`.
- Produces:
  - Probe changes:
    - `details["lint"]` is always present (`[]` when the script check fails).
    - When the cast has image characters, probe adds `details["castImages"] = {"provider": <"openai" | "fake" | "supplied">, "calls": <poses_on_disk>, "cached": calls == 0}`, plus a check `CheckResult(name="images", ok=..., detail=...)`. It is ok unless `openai` calls are needed and `settings.openai_api_key` is empty.
    - Probe reads no URL and generates nothing.
  - `make_cast` reply: each character gains `"kind"`, and the reply gains `"images": {"calls": n, "cached": bool}` when there are image characters. The hint says the preview job draws the poses and spends `n` OpenAI image calls when `n > 0`.
  - `cost.providers` adds `"openai"`, and the cost notes mention image calls. `artifacts` adds `"post.md"` (if Task 2 has not) and `"cast-<id>-<pose>.png"`.
  - Guide keys:
    - `guide["images"]`: when to choose images over SVG, the two forms with a JSON example each, the four poses, the cost (4 calls per generated character, counted against the caller's `openai` quota), `variant` for another take, and that local paths work only on local runs.
    - `guide["looks"]`: the three looks and when each suits.
    - `guide["post"]`: what `post.md` holds and that it is pasted as the caption.

- [ ] **Step 1: Write the failing tests**

```python
def test_probe_reports_image_calls_without_spending(tmp_path):  # fake provider, nothing on disk
    report = recipe.probe(inputs, ctx)
    assert report.details["castImages"] == {"provider": "fake", "calls": 4, "cached": False}

def test_probe_openai_without_key_fails_images_check():
    assert not next(c for c in report.checks if c.name == "images").ok

def test_probe_keeps_lint_key_when_script_fails():   # unknown cast id
    assert report.details["lint"] == []

def test_no_title_not_reported_in_cast_mode():
    assert "no-title" not in {f.rule for f in lint.check(cast_mode_inputs, speakers)}

def test_make_cast_image_cast(mcp_tools):            # fake provider, preview=False
    out = tools.make_cast(ctx, characters=[builtin_char, fake_image_char], preview=False)
    assert [c["kind"] for c in out["characters"]] == ["svg", "image"] and out["images"] == {"calls": 4, "cached": False}

def test_guide_has_images_looks_post():
    assert {"images", "looks", "post"} <= set(GUIDE)
```

- [ ] **Step 2: Run them to see them fail.**
  - Run: `pytest tests/test_dialoguereel_probe.py tests/test_dialoguereel_mcp.py tests/test_dialoguereel_lint.py -q`
  - Expected: FAIL.

- [ ] **Step 3: Implement it.**

- [ ] **Step 4: Run the tests to see them pass.**
  - Run: `pytest tests -k "dialoguereel or mcp" -q`
  - Expected: PASS.

- [ ] **Step 5: Commit** ("dialoguereel: probe and make_cast report image calls; the guide covers images, looks and post.md").

---

### Task 7: Docs and two example reels

**Files:**
- Create: `reelmachine/recipes/dialoguereel/examples/konbini-n5-ja.json` and `cafe-topik1-ko.json`
- Modify: `pyproject.toml` (package data: `recipes/dialoguereel/examples/*.json`)
- Create: `docs/DIALOGUEREEL.md`
- Modify: `docs/MCP.md` (`make_cast` with image casts and the `images` reply), `docs/RECIPES.md` (the dialoguereel row: looks, post.md, verify, image characters)
- Test: `tests/test_dialoguereel_examples.py`

**Interfaces:**
- Consumes: `DialogueReelInputs`, `recipe.probe`, `lint`.

**The two examples:**
- **Japanese** (`look: "pastel"`, `flow: "listen_first"`, level `N5`, `meaning_language` `en`): a konbini exchange of 6–8 lines between `onigiri` (customer) and `cat` (clerk). It uses ruby for kanji, 3–5 keys, one `slow` line, and emotions. Use polite clerk forms (いらっしゃいませ, 袋{ふくろ}はご利用{りよう}ですか is N4+, so prefer 袋{ふくろ}はいりますか), all N5 vocabulary.
- **Korean** (`look: "night"`, level `TOPIK 1`, `language: "ko"`, `meaning_language: "en"`): a café order between `boba` and `bunny`, 6–8 lines in natural 해요체. Each line has a romanized `reading` (Revised Romanization) and 3–5 keys.
- Both use `"voice": "fake:default"` and `mode: "storyboard"`.

**`docs/DIALOGUEREEL.md`** covers: what it makes and for whom; the agent workflow (pick a level and situation → `make_cast` → lines → `probe` → storyboard → full); the script model with an example; characters (builtin, svg contract summary, image forms, cost and quota, `REEL_OPENAI_API_KEY`); looks; flows (standard, listen_first), slow, quiz and recap; artifacts (`reel.mp4`, `captions.srt`, `post.md`, `storyboard.jpg`, `cast.png`, `script.resolved.json`); `verify`'s checks; settings; and the two examples by path. Keep it under ~250 lines, in the tone of `docs/MAPREEL.md`.

- [ ] **Step 1: Write the failing test**

```python
EXAMPLES = sorted((Path(dialoguereel.__file__).parent / "examples").glob("*.json"))

def test_two_examples_ship():
    assert {p.name for p in EXAMPLES} == {"konbini-n5-ja.json", "cafe-topik1-ko.json"}

@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_lints_clean(path):
    inputs = DialogueReelInputs.model_validate_json(path.read_text())
    found = lint.check(inputs.model_copy(update={"mode": "full"}), speakers_of(inputs))
    assert [f for f in found if f.severity == "error"] == []
    assert [f.rule for f in found if f.rule in ("level", "key-missing", "no-reading", "long-line")] == []
```

- [ ] **Step 2: Run it to see it fail.**
  - Run: `pytest tests/test_dialoguereel_examples.py -v`
  - Expected: FAIL.

- [ ] **Step 3: Write the examples, the package data and the docs.**

- [ ] **Step 4: Run the tests to see them pass, then the whole suite.**
  - Run: `pytest tests/test_dialoguereel_examples.py -q && pytest -q`
  - Expected: PASS.

- [ ] **Step 5: Commit** ("dialoguereel: docs and a Japanese and a Korean example").
