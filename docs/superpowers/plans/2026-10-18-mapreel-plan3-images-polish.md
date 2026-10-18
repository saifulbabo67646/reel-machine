# mapreel Plan 3: Images and Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish mapreel v1:
- Items can show real Wikimedia Commons images, found through a new `find_images` MCP tool and stored with their licence.
- Every reel ships a `sources.md`.
- Two more looks, `flat` and `dark`.
- A `verify` step that fails a broken reel.
- Docs.
- A real example reel with a real voice.

The plan also folds in the Plan 1 and Plan 2 follow-ups that reach the viewer.

**Architecture:**
- **Commons access** is a small provider-style module, `commons.py`, with a fake for tests. Both the MCP tool and the images stage use it.
- **Every image**, Commons or caller-supplied, is normalised to PNG ≤ 1024 px with its licence and provenance.
- **`sources.md`** is written by the render stage from the resolved script, images and data licences.
- **The new looks** are rasterised in Python from Natural Earth polygons into the same Mercator layer files the satellite look uses, so the runtime keeps one basemap path. Only fill styling reads `doc.look`.
- **Verify** inspects the finished file, following explainer3d's verify.

**Tech Stack:** Python 3.11+, pydantic v2, numpy, Pillow, urllib; the canvas runtime in ES modules; HyperFrames; pytest (the `slow` marker for ffmpeg, Node or Chrome).

**Spec:** `docs/superpowers/specs/2026-10-17-mapreel-design.md` (§2, §3 Items, §4 stages 3/7, Artifacts and Looks, §5 `find_images`, §8 Images/MCP/Real render, §9 item 3). Plan 2: `docs/superpowers/plans/2026-10-17-mapreel-plan2-retention.md`; its ledger's parked minors are the follow-up source for Task 6.

## Global Constraints

- **Commit messages:** `mapreel: <plain words saying what changed for a user>`, ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **The default suite** (`uv run pytest -q`) uses no network and no Chrome. Anything touching the network, ffmpeg, Node or HyperFrames is `@pytest.mark.slow`; real network tests are additionally skipped unless `REEL_NET_TESTS=1`.
- **Map tests** use the fixtures in `tests/fixtures/mapreel/`, seeded into a temp geo cache (see `_seed` in `tests/test_mapreel_engine.py`).
- **Commons:**
  - Every request sends the User-Agent `reel-machine/mapreel (https://github.com/saifulbabo67646/reel-machine)`.
  - Use the MediaWiki API at `https://commons.wikimedia.org/w/api.php`.
  - At most one request at a time, and no request loop without a limit.
- **An image problem is a warning and a text badge, never a failed job.**
- **Runtime pure maths** lives in `runtime/pure.mjs` with Node tests.
- **Captions** stay at 71 % of 1920.
- **Never add a manual project-key bump.** Runtime and template edits already change `compose.project_key`.
- **Keep passing:**
  - `examples/mapreel/brands.json` and `examples/mapreel/food.json` still pass `probe`. Food keeps no errors and ≤ 4 warnings.
  - Both slow render tests stay green.

## Review Focus

1. **A Commons file whose licence is non-free or missing, or whose extmetadata is empty.** It must be excluded by default, or shown with `permitted: false`. It must never be fetched into a reel unless `include_nonfree` is used. Test: `test_find_images_hides_files_without_a_free_licence` (Task 1).
2. **An item image that is huge (8000 px), an SVG, or a WebP.** It must end up as a PNG ≤ 1024 px with alpha kept, or as a badge with a warning; it must never fail the job. Test: `test_images_are_normalised_to_png_within_1024` (Task 2).
3. **A script with no sources at all.** `sources.md` must still list the data and voice credits and state plainly that the claims are unsourced. Test: `test_sources_md_says_when_claims_are_unsourced` (Task 3).
4. **A page error in the runtime** (`window.__mapError`). The job must fail with that message, not ship a blank map. Test: `test_a_runtime_map_error_fails_the_render` (Task 5).
5. **A `dark` or `flat` look with every kind.** The fills, plates and tags must stay readable on that background. Test: the slow render in Task 4 renders food.json in `flat` and `dark`, and the implementer inspects it.

---

### Task 1: Commons search: `commons.py` and the `find_images` MCP tool

**Files:**
- Create: `reelmachine/recipes/mapreel/commons.py`
- Modify: `reelmachine/mcp/tools.py`, `reelmachine/mcp/server.py` (register `find_images` beside `find_places`)
- Modify: `docs/MCP.md` (the tool entry)
- Create: `tests/test_mapreel_commons.py`; modify `tests/test_mapreel_mcp.py`

**Interfaces (produces):**
- `commons.ImageHit`, a frozen dataclass with these fields:
  - `id: str`, which is `"commons:File:<title without File:>"`;
  - `title: str`;
  - `thumb_url: str`, a 320 px thumb;
  - `url: str`, the original;
  - `width: int`;
  - `height: int`;
  - `mime: str`;
  - `licence: str`, the short name, e.g. "CC BY-SA 4.0";
  - `licence_url: str | None`;
  - `attribution: str`, the Artist/Credit as plain text (HTML stripped);
  - `permitted: bool`;
  - `restrictions: str | None`, e.g. "trademarked".
- `commons.search(query: str, *, kind: Literal["any","logo","photo","flag"]="any", limit: int = 12, include_nonfree: bool = False, fetch=_http_json) -> list[ImageHit]`.
- `commons.info(file_id: str, *, width: int = 1024, fetch=_http_json) -> ImageHit`. The thumb is requested at `width`. It raises `InvalidInput` for a missing file.
- `commons.FREE` is a regex of permitted short licence names. It is case-insensitive and covers:
  - `^(cc0|cc[- ]by(-sa)?( \d\.\d)?|public domain|pd\b.*|attribution|gfdl|fal|copyrighted free use)`.

**Details:**
- **Search request:**
  - Use `action=query&format=json&generator=search&gsrnamespace=6&gsrlimit=<limit*2 capped at 50>&gsrsearch=<q>&prop=imageinfo&iiprop=url|size|mime|extmetadata&iiurlwidth=320`.
  - `kind` biases the query:
    - `logo` adds ` logo` and prefers `mime` `image/svg+xml` or `image/png`;
    - `flag` adds ` flag`;
    - `photo` excludes svg.
  - Sort the hits by the API's `index`.
  - Drop non-images (mime not starting with `image/`).
  - Unless `include_nonfree`, drop hits that are not `permitted`.
  - Truncate to `limit`.
- **`permitted`:** `LicenseShortName` matches `FREE`, and `extmetadata.NonFree` is not "true".
- **Restrictions:** `restrictions` comes from `extmetadata.Restrictions`.
- **Attribution:**
  - Use `Artist`, else `Credit`, else `"Wikimedia Commons"`.
  - Strip tags with `html.parser`.
  - Collapse whitespace.
  - Cut it to 200 characters.
- **`_http_json(params)`:** `urllib.request` with the User-Agent, a 20 s timeout, one retry on 5xx or a timeout, and `ProviderUnavailable("Wikimedia Commons did not answer", hint=...)` on failure.
- **The MCP tool:**
  - `find_images(query, kind="any", limit=12, include_nonfree=False)` returns `{"results": [asdict(hit) ...], "note": "..."}`.
  - The note says to use `id` as an item's `image` and that trademarks may apply.
  - `limit` is clamped to 1..50. An empty query gives the standard `InvalidInput` payload.

- [ ] **Step 1: Write failing tests** with a fake `fetch` that returns a canned API JSON. Build it in the test from a dict that mimics the real shape: `query.pages{pageid:{title,index,imageinfo:[{url,thumburl,width,height,mime,extmetadata:{LicenseShortName:{value},LicenseUrl:{value},Artist:{value},Restrictions:{value},NonFree:{value}}}]}}`.
  - `test_find_images_returns_ids_licences_and_attribution`: the HTML in Artist is stripped.
  - `test_find_images_hides_files_without_a_free_licence`: covers a missing LicenseShortName, "Fair use", and `NonFree: "true"`. All are hidden by default and shown with `permitted == False` when `include_nonfree`.
  - `test_logo_kind_biases_the_query_and_prefers_vector`.
  - `test_limit_is_clamped`.
  - `test_commons_failure_is_a_clear_provider_error`.
  - `test_info_reads_one_file_and_refuses_a_missing_one`.
  - In `tests/test_mapreel_mcp.py`: `test_find_images_tool_payload_and_errors`, which monkeypatches `commons._http_json`.
  - Add one `@pytest.mark.slow` test, skipped unless `REEL_NET_TESTS=1`, that really searches "Nike logo" and asserts at least one permitted hit.
- [ ] **Step 2: Implement** `commons.py` and the tool.
- [ ] **Step 3: Run and commit.** Run `uv run pytest -q tests/test_mapreel_commons.py tests/test_mapreel_mcp.py`, then the full fast suite. Commit: `mapreel: find_images searches Wikimedia Commons and says which files are free to use`.

---

### Task 2: The images stage: Commons fetch, normalising, licences

**Files:**
- Modify: `reelmachine/recipes/mapreel/stages.py` (`ImagesStage`, `_fetch_image`), `models.py` (`ImagesOutput.image_credits`)
- Create: `tests/test_mapreel_images.py`

**Interfaces:**
- **Consumes:** `commons.info`.
- **Produces:**
  - `ImagesOutput.image_credits: dict[str, dict]`, keyed like `images` (`"<seg id>/<j>"`). Each value is `{"name": item.name, "source": "commons"|"caller", "id_or_path": str, "licence": str, "licence_url": str|None, "attribution": str, "restrictions": str|None}`.
  - For a caller-supplied image, the licence is `"caller-supplied"` and the attribution is `"supplied by the script author"`.

**Details:**
- **A `commons:File:…` image:**
  - `commons.info(id)`. Refuse it with a warning plus a badge if it is not `permitted`.
  - Download the original. If its width is over 2048, use the API thumb at `iiurlwidth=1024`. For SVG, always use the PNG thumb at 1024: `thumburl` with `iiurlwidth=1024` is a rasterised PNG.
  - Cache by id under `geo_cache(settings)/images/` so a second reel reuses it.
- **Normalise every image** (Commons, URL or path) with Pillow to RGBA PNG, longest side ≤ 1024 (LANCZOS), alpha kept. Name the PNG by its content hash. SVG from a caller is refused with a warning plus a badge; do not add a rasteriser dependency.
- **Every failure** is a warning naming the segment, the item and the reason, and leaves the item a badge.
- The stage stays `cacheable = False`, but downloads are cached on disk.
- The existing "Commons images arrive with find_images" warning goes away.

- [ ] **Step 1: Write failing tests.** Monkeypatch `commons.info` and the downloader to serve local generated images: a 3000×1500 JPEG, a 64×64 PNG with alpha, a WebP, and a fake SVG. Tests:
  - `test_images_are_normalised_to_png_within_1024`: sizes, mode and alpha.
  - `test_a_commons_image_is_fetched_once_and_credited`: the second run does not download, and the credit is right.
  - `test_a_non_free_commons_file_becomes_a_badge_with_a_warning`.
  - `test_a_caller_image_is_marked_caller_supplied`.
  - `test_a_broken_image_never_fails_the_job`: covers a 404, garbage bytes, and a caller SVG.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Engine check.** Extend the engine test (`tests/test_mapreel_engine.py`, storyboard) with one item using a monkeypatched Commons image. Assert that `script.resolved.json` (or the segments doc) carries the image path, and that the badge-less item renders through the existing image path.
- [ ] **Step 4: Run and commit.** Commit: `mapreel: items show Wikimedia Commons images, normalised and credited`.

---

### Task 3: `sources.md`

**Files:**
- Create: `reelmachine/recipes/mapreel/sources.py`
- Modify: `stages.py` (RenderStage, and the storyboard path, write `sources.md` next to the reel and register it as an artifact the same way `captions.srt` is). Check how artifacts are listed in `RenderOutput` and the recipe's artifact list, and follow it.
- Create: `tests/test_mapreel_sources.py`

**Interfaces (produces):** `sources.render(inputs: MapReelInputs, segments_ids: list[str], image_credits: dict[str, dict], *, voice_licence: str, data: dict[str, Licence]) -> str` (Markdown).

**Layout (exact headings):**

```
# Sources — <title>

## Claims
- <segment label>: <claim text> — <url>
...
## Images
- <item name>: <licence> — <attribution> (<id_or_path>)[ — <restrictions>]
## Map data and voice
- Natural Earth — Public domain — https://www.naturalearthdata.com/
- NASA Blue Marble — Public domain — https://visibleearth.nasa.gov/   (satellite look only)
- Narration — <voice> — <voice licence>
```

**Details:**
- **Segment label:**
  - `#N <country>` for rank;
  - `<A> vs <B>` for versus;
  - the country name otherwise.
- **Claim text:**
  - an item's `name`;
  - for a stat, `<value> <label>`;
  - for a segment `source`, the segment narration's spoken text cut to 120 characters.
- **Deduplication:** the same URL for the same segment is listed once, with the claims joined by ", ".
- **No sources at all:** "Claims" holds the single line `- No sources were given for the claims in this reel.`
- **No images:** "Images" says `- No images (items are shown as text badges).`
- **Ordering:** stable order (segments in order, items in order), so the same script gives the same file.

- [ ] **Step 1: Write failing tests:**
  - `test_sources_md_lists_claims_images_and_credits`
  - `test_sources_md_says_when_claims_are_unsourced`
  - `test_sources_md_merges_a_url_used_twice_in_one_segment`
  - `test_flat_look_omits_the_blue_marble_credit` (it needs Task 4's model change; write it then, or give the look as a plain parameter now)
  - `test_a_build_writes_sources_md` (engine, storyboard mode is enough if storyboard also writes it; it must)
- [ ] **Step 2: Implement and wire.**
- [ ] **Step 3: Commit.** Commit: `mapreel: every reel ships a sources.md with each claim's source, each image's licence and the data credits`.

---

### Task 4: The `flat` and `dark` looks

**Files:**
- Modify: `models.py`: `look: Literal["satellite","flat","dark"] = "satellite"`.
- Modify: `basemap.py`:
  - add `world_layer(settings, root, look="satellite")` and `detail_layer(..., look="satellite")`;
  - add `_vector_layer(root, look, x0, y0, x1, y1, out_w, out_h)`, which rasterises Natural Earth admin-0 with Pillow `ImageDraw` in Web Mercator.
- Modify: `stages.py` / `compose.py` (pass the look; the layer cache key includes the look).
- Modify: `runtime/draw.mjs` (fill, glow and ghost-name styling per `doc.look`), `pure.mjs` (`lookStyle(look)` returns the numbers).
- Modify: `sources.py` (no Blue Marble credit unless the look is satellite), `guide.py` (`looks` line).
- Test: `tests/test_mapreel_basemap.py` (create or extend), `runtime/tests/pure.test.mjs`, and `tests/test_mapreel_render.py` (slow).

**Values (exact):**

| look | sea | land | borders | fill alpha | glow | ghost name |
|---|---|---|---|---|---|---|
| flat | `#a8d8f0` | `#f3efe4` | `#9a948a`, 2 px at 1024-wide world | 0.92 | soft white, 0.35 | `rgba(0,0,0,0.18)` |
| dark | `#070b14` | `#1a2233` | `#34425e`, 2 px | 0.85 | neon (the fill colour), 0.9, blur 28 | `rgba(255,255,255,0.14)` |
| satellite | unchanged | unchanged | unchanged | unchanged | unchanged | unchanged |

**Details:**
- **Rasterising:** rasterise from `geodata.ensure(root, "countries")`, the 50m admin-0 already used, at the same pixel geometry the satellite layers use:
  - `WORLD_PX` for the world;
  - the detail crop at the satellite detail size.
- **Coverage:**
  - Antimeridian-crossing polygons (RUS, FJI, USA) must not draw a horizontal streak; reuse the ring-splitting from `geometry`/`compose.place_rings`.
  - Polar rows past ±85° are sea colour.
- **Seam:** the flat and dark looks have no Blue Marble seam.
- **Plates and tags:** they keep the luminance rule from the Plan 2 fix wave (`textOn`), so they remain readable on any look.

- [ ] **Step 1: Write failing tests:**
  - `test_flat_world_layer_colours_land_and_sea`: sample a pixel in the Sahara and one in the mid-Pacific from the fixture data's layer.
  - `test_vector_layer_has_no_antimeridian_streak`: the rows through Fiji's latitude away from Fiji are all sea.
  - `test_look_is_part_of_the_layer_cache_key`.
  - A Node test that `lookStyle` returns each row of the table.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Slow render.** Render `examples/mapreel/food.json` storyboard in `flat` and `dark` (override `look`), using `REEL_HYPERFRAMES_WORKERS=1`. Assert that each storyboard exists and that its tiles are not blank. Look at both yourself, fix anything unreadable, and copy them to the scratchpad for the controller.
- [ ] **Step 4: Commit.** Commit: `mapreel: flat and dark looks, drawn from Natural Earth with no satellite texture`.

---

### Task 5: `verify`, and runtime errors fail the job

**Files:**
- Create: `reelmachine/recipes/mapreel/verify.py`
- Modify: `recipe.py` (`verify` returns the report), the render path that runs HyperFrames (find where `__mapError` could be read: the HyperFrames renderer or the compose template). After the page loads, a set `window.__mapError` must turn into a `RenderFailed`/`InvalidInput`-style job error whose message includes the text.
- Test: `tests/test_mapreel_verify.py` (slow, real ffmpeg) and fast unit tests with synthetic videos made by ffmpeg `testsrc`/`color` (slow too).

**Checks (exact names):**
- `format`: 1080×1920, video and audio present.
- `duration`: within 100 ms of the timeline.
- `audio`: the narration's mean volume is above −45 dB (ffmpeg `volumedetect`) and the audio is not silent.
- `frozen`: no stretch longer than `lint.BEAT_GAP_S * 2` (2.4 s) in which the picture between y 12 % and 70 % changes by less than `MOTION_MIN = 0.5`. Sample every 0.4 s using explainer3d's `_grey` approach (import it, do not copy it).
- `items`: for each item with a pop time, the frame at `pop + 400 ms` differs from the frame at `pop − 200 ms` inside the item's anchor box (from the segments doc) by more than 6 mean grey levels. Report the item names that fail.
- `renderer`: the same as explainer3d's.

In storyboard mode, verify returns ok with the note "storyboard", as explainer3d does.

- [ ] **Step 1: Write failing tests:**
  - `test_verify_passes_a_good_reel`: reuse the slow engine build's output, or render the shortest possible 2-segment reel.
  - `test_verify_flags_a_frozen_stretch`: a synthetic video with a 3 s still in the middle.
  - `test_verify_flags_silence`.
  - `test_a_runtime_map_error_fails_the_render`: make a layer URL unloadable in the composed project, e.g. point a layer at a missing file, and assert that the job fails with "mapreel: layer … failed to load".
- [ ] **Step 2: Implement and wire.**
- [ ] **Step 3: Commit.** Commit: `mapreel: verify checks the finished reel, and a map that fails to load fails the job`.

---

### Task 6: Follow-ups that reach the viewer or the agent

**Files:** as each item needs. Tests go next to the code they pin.

1. **The Blue Marble seam at ~80.4° N** (Plan 2 ledger, Task 8). In `basemap._mercator`, cross-fade the source rows over ±12 px around the seam latitude. Test it on a synthetic source with a hard edge.
2. **Lint for regions uses the names the voice says** (Plan 2 final review, Minor 2):
   - `heard_names` covers region entries (`say`, else the region's name from the atlas), not the code.
   - Lint also applies the backdrop and region-fill floors, as `kinds.build_regions` does.
3. **A held rank keeps the badge clear, and a hold after a versus is refused:**
   - `camera.hold` on a rank re-frames inside `RANK_SAFE` when the held frame would put the country under the badge or the card. Test the frame.
   - `hold: true` right after a versus gives `InvalidInput` at `segments[i].camera.hold`, with the hint "a versus has no single frame to hold; drop hold".
4. **Main-landmass framing per country** (Plan 1 memory). Add per-country framing tests, against fixture cuts added to `tools/mapreel_fixture.py` `KEEP`, for IDN, PHL, GRC and NOR (Svalbard excluded). Each frame must contain the main body, with the country filling at least 35 % of the view's short side. Fix the cluster threshold if any fails.
5. **The `find_places` regions flag** no longer parses the 40 MB file:
   - On first parse, write a sidecar `regions-index.json` (country alpha-3 → count) into the geo cache.
   - Read the sidecar afterwards.
   - Test that the second call never opens the GeoJSON.
6. **The guide:** its `looks` and `images` lines describe `find_images`, `commons:File:…`, the licence note, and `sources.md` for the video description. The workflow step for images names `find_images`.

- [ ] **Step 1:** Each item gets a test first, then its fix. Commit each item separately with its own `mapreel: …` message.

---

### Task 7: Docs

**Files:**
- Create: `docs/MAPREEL.md`
- Modify: `docs/MCP.md`, `docs/RECIPES.md` (the recipe list), and `README.md` (one mapreel section with the demo command)
- Test: none (docs). The doc examples must actually run. Run every command in MAPREEL.md that does not need a paid TTS.

**`docs/MAPREEL.md` contents:**
- **What it makes,** with one storyboard image from `docs/images/mapreel-food-storyboard.jpg`. Render it, commit the JPEG, and keep it ≤ 400 KB.
- **The agent workflow:** `describe_recipe` → `find_places` → `find_images` → `probe` → `start_job` storyboard → `start_job` build.
- **The script model:** every kind with a minimal JSON, and items, images, `say`, `at`, `source`, `camera` and `colour`.
- **The hook lint:** the 8 rules, which two are hard, and `lint: "warn"`.
- **Looks.**
- **Voices:**
  - `REEL_TTS=fake|macos|cartesia|elevenlabs`;
  - macOS for free local tests (`macos:Samantha`), and Cartesia or ElevenLabs for production;
  - what each costs (the probe's `cost` detail).
- **Artifacts:** `reel.mp4`, `captions.srt`, `storyboard.jpg`, `script.resolved.json`, `sources.md`.
- **Data and licences:** Natural Earth, NASA, Commons.
- **Troubleshooting:** a weak-hook refusal, an unknown place, a missing Node or HyperFrames, and out-of-credit TTS.

- [ ] **Step 1: Write.** **Step 2: Run the commands.** **Step 3: Commit.** Commit: `mapreel: docs — how an agent goes from a topic to a finished map reel`.

---

### Task 8: A real example reel with a real voice

**Files:**
- Modify: `examples/mapreel/food.json`. Give at least 3 items Commons images found with `find_images` (real network, permitted licences only).
- Create: `examples/mapreel/README.md`. It names the commands for the fake, macos and cartesia voices.

**Steps:**
- [ ] **Step 1: Choose the images.**
  - With the real `find_images`, choose permitted Commons images for 3–5 food items, e.g. Marmite, natto, lutefisk.
  - Record each id in food.json.
  - Keep food.json's voice as `fake:default`, so CI and probe stay voice-free. Document `"voice": "macos:Samantha"` (local) and a Cartesia voice id (production) as overrides.
- [ ] **Step 2: Build a full reel locally.**
  - Use `REEL_TTS=macos` with the voice overridden to `macos:Samantha`, mode `full`.
  - Check that `verify` passes and that `sources.md` lists the images' licences.
  - Copy `reel.mp4`, `storyboard.jpg` and `sources.md` to the scratchpad for the controller.
  - Do not commit the MP4.
- [ ] **Step 3: Watch the reel.** Sample frames every 0.5 s with ffmpeg and look at a contact sheet. Fix anything visibly broken (in this task, with a test where possible).
- [ ] **Step 4: Commit.** Commit: `mapreel: the food example shows real Commons images and builds end to end with a real voice`.

Cartesia is out of credits on this machine (2026-10-17). The production command is documented but not run here.
