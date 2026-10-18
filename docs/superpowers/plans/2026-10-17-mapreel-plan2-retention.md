# mapreel Plan 2 — Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give mapreel the shots and checks that hold a viewer: `rank`, `versus` and `regions` segments, an 8-rule hook lint that `probe` reports (and the build refuses on the two hard rules), a full guide with a topic bank, and the Plan 1 rough edges fixed (world views, pop matching, palette).

**Architecture:** Each new segment kind adds a model, a resolver branch, a builder that turns it into a segment document, and runtime drawing code. The builders move out of `SegmentsStage.run` into `kinds.py` so the stage stays a loop. Admin-1 data (Natural Earth states/provinces) loads lazily only when a `regions` segment exists. The lint is a pure module run from `probe` and from the script stage.

**Tech Stack:** Python 3.11+, pydantic v2, numpy; canvas 2D runtime in ES modules (`node --test` for pure maths); HyperFrames for real renders; pytest (`slow` marker for anything that touches ffmpeg or Chrome).

**Spec:** `docs/superpowers/specs/2026-10-17-mapreel-design.md` (sections 3, 6, 9 item 2). Plan 1: `docs/superpowers/plans/2026-10-17-mapreel-plan1-foundation.md`.

## Global Constraints

- Commit messages: `mapreel: <plain words saying what changed for a user>`, ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The default test suite (`uv run pytest -q`) uses no network and no Chrome; anything touching ffmpeg, Node or HyperFrames is `@pytest.mark.slow` (or `pytestmark = pytest.mark.slow`).
- Map tests use the fixtures in `tests/fixtures/mapreel/` seeded into a temp geo cache (see `_seed` in `tests/test_mapreel_engine.py`), never the real download.
- Runtime pure maths lives in `runtime/pure.mjs` and is tested in `runtime/tests/pure.test.mjs` (`node --test "reelmachine/recipes/mapreel/runtime/tests/*.test.mjs"`).
- Screen: 1080×1920, 30 fps (`geometry.WIDTH`, `geometry.HEIGHT`, `compose.FPS`). Captions stay at 71 % of the height.
- Every change to `runtime/*.mjs` or `_TEMPLATE` already changes `compose.project_key` (it hashes them); do not add a manual version bump.
- Anything unusable in a script (unknown place, wrong list length) is `InvalidInput` with `details["path"]` like `segments[3].regions[1].region` and a `hint`. Images and unmatched names are warnings, never errors.
- Lint words are English (spec §10).
- Keep the existing `examples/mapreel/brands.json` rendering and passing `probe` (it is the README demo).

## Review Focus

1. **A `versus` or `regions` segment next to a `country` segment** — the camera into and out of it must not jump to a blurry world; a reasonable person expects a clean cut into a versus and a normal fly out of it. Test: `test_segments_after_a_versus_fly_from_its_union_frame` (Task 3).
2. **A region name that two countries share** ("Georgia" the US state vs the country, "Punjab" in IND and PAK) — resolution is scoped to the segment's country, so it must just work. Test: `test_region_names_resolve_inside_their_country` (Task 4).
3. **A rank-1 hook paid off by a highlight or a spoken reveal, not a `rank` segment** — rule 2 must not refuse a reel that does pay off. Test: `test_unpaid_loop_accepts_a_spoken_reveal` (Task 6).
4. **A highlight that names a country by an alias** ("America" for USA, "Holland") — the fill must still land on the word. Test: `test_highlight_fills_on_an_alias` (Task 1).
5. **A world-spanning highlight across the antimeridian** (USA + JPN + AUS) — the frame takes the short way round the globe, fills the screen height when it can, and never shows the map's top edge. Test: `test_world_frames_take_the_short_way_round` (Task 1).

---

### Task 1: Plan 1 rough edges — world views, pop matching, palette

**Files:**
- Modify: `reelmachine/recipes/mapreel/geometry.py` (MIN_S, frame clamping)
- Modify: `reelmachine/recipes/mapreel/compose.py` (`segment_frame` wraps)
- Modify: `reelmachine/recipes/mapreel/timing.py` (`match` consumes tokens; `match_any`)
- Modify: `reelmachine/recipes/mapreel/palette.py` (beyond 12 colours)
- Modify: `reelmachine/recipes/mapreel/places.py` (`Atlas.spoken_names(code)`)
- Modify: `reelmachine/recipes/mapreel/stages.py` (highlight fills use `match_any`)
- Modify: `reelmachine/recipes/mapreel/runtime/draw.mjs` (tiny-place marker)
- Test: `tests/test_mapreel_geometry.py`, `tests/test_mapreel_timing.py`, `tests/test_mapreel_compose.py`, `tests/test_mapreel_places.py`, `tests/test_mapreel_stages.py`

**Interfaces:**
- Produces: `geometry.frame_for(bbox, *, zoom=1.0, view=(WIDTH, HEIGHT), safe=SAFE) -> Frame` (new keyword args, defaults keep Plan 1 behaviour); `geometry.clamp_frame(frame, view=(WIDTH, HEIGHT)) -> Frame`; `timing.match_any(alternatives: Sequence[Sequence[str]], words) -> list[int | None]`; `Atlas.spoken_names(code) -> list[str]`; `palette.assign` never repeats a colour inside one group even past 12 codes.

- [ ] **Step 1: World frames fill the height and take the short way round.** In `geometry.py`:
  - `MIN_S_FILL = float(HEIGHT)`: a frame never zooms out past the world filling the screen height, *unless* the places need more width; then `s` may go down to the Plan 1 floor `MIN_S = WIDTH`.
  - `frame_for` gains `view=(w, h)` and `safe=(x0, y0, x1, y1)` and computes `s` from them (Task 3 uses a panel view). After the zoom, `s = max(s, MIN_S_FILL)` only if the bbox still fits horizontally at `MIN_S_FILL` inside the safe width; otherwise `s = max(s, MIN_S)`.
  - New `clamp_frame(frame, view)`: when `frame.s * 1 >= view_h` (the world's Mercator height covers the view), clamp `frame.y` into `[view_h / 2 / s, 1 - view_h / 2 / s]` so the top and bottom edges of the world never show. Otherwise centre `y` at 0.5.
  - `frame_for` returns `clamp_frame(...)`.
  - In `compose.segment_frame`: before taking the union, shift each shape's `main` box by a whole number of worlds so the set spans the shortest arc. Sort the box centres; the widest gap between neighbours on the circle is where the cut goes; shift every box after the cut by -1. This gives USA+JPN a span through the Pacific. `place_entry` already picks each shape's nearest copy from `frame.x`, so nothing else changes.
  - Tests in `tests/test_mapreel_geometry.py` and `tests/test_mapreel_compose.py`:
    - `test_world_frames_take_the_short_way_round`: shapes USA and JPN from the fixture give a frame whose visible bbox contains both mains (after the nearest-copy shift). Its width span is < 0.5.
    - `test_a_world_frame_never_shows_the_top_edge`: `frame_for((0.1, 0.1, 0.3, 0.2))` has `s >= HEIGHT`, and `visible_bbox(frame)[1] >= 0` and `[3] <= 1`.
    - `test_a_country_frame_is_unchanged`: ITA framed alone gives the same frame as before the change (pin the Plan 1 value with `pytest.approx` from the current code before editing).
    - `test_frames_too_wide_for_the_height_still_fit`: a bbox of width 0.7 gives `s` in `[WIDTH, HEIGHT)` with the bbox inside the visible width.

- [ ] **Step 2: A tiny place on a world view gets a marker.** In `draw.mjs` `fillPlace`: when the place's on-screen bbox (computed once per frame from its rings) is under 70 px on both sides, also draw a ring around its centre. The ring is radius 46 px, lineWidth 7, in the place colour, with a white 2 px outline, scaled by `fillAlpha`. It pulses with `1 + 0.08 * sin(seconds * 6)`. Put the bbox helper `ringsBox(rings, cam)` in `pure.mjs` and add a node test.

- [ ] **Step 3: Matching consumes words.** In `timing.match`, keep a set of used token indices. A phrase may only match a run of tokens none of which is used. The fallback "said earlier than listed" search also skips used tokens. Mark the run used on every hit. Add `match_any(alternatives, words)`: for each entry, try each alternative phrase in order (earliest start wins among those that hit); the same consumption rules apply. `match(phrases, words)` becomes `match_any([[p] for p in phrases], words)`. Tests:
  - `test_a_name_inside_another_name_takes_its_own_word`: "Ralph Lauren and Lauren Hutton" with phrases ["Ralph Lauren", "Lauren"]. The second matches the third word, not the second.
  - `test_match_any_takes_the_first_alternative_heard`: alternatives `[["United States of America", "America", "USA"]]` against "America leads" give the start of "America".
  - Existing tests in `tests/test_mapreel_timing.py` still pass.

- [ ] **Step 4: Highlight fills land on aliases.** Add `Atlas.spoken_names(code)`: the place's `name`, plus every key name a person would say. Take the original-case values of the `_NAME_FIELDS` that `_load` read for this code, plus the alias keys pointing only at this code. Store the alias spellings as display strings: add `ALIAS_SAID = {"holland": "Holland", "uk": "UK", "britain": "Britain", "usa": "USA", "us": "US", "america": "America", ...}` beside `ALIASES` with one entry per alias. De-duplicate, longest first. `_load` keeps a `said: dict[str, list[str]]` on the Atlas. In `SegmentsStage` the highlight branch calls `match_any([[*atlas.spoken_names(c), query] for c, query in ...], spoken)`, where `query` is what the script wrote for that place. Tests:
  - `test_spoken_names_include_aliases` (USA → contains "United States of America" and "America").
  - `test_highlight_fills_on_an_alias`: a stage-level test with fake word timings, in the style of the existing `tests/test_mapreel_stages.py`. "America and Holland lead" fills USA at "America" and NLD at "Holland".

- [ ] **Step 5: The palette never repeats inside a group.** When all 12 colours are taken by the neighbours, `palette.assign` generates a new colour instead of falling through to the last candidate. Use hue `(137.508 * n) % 360` with n = how many extras so far, saturation 85 %, lightness 50 %, converted to `#rrggbb`. Keep generating until the colour is not in `taken`. Test: `test_sixty_countries_get_sixty_colours` with one group of 60 codes → 60 distinct colours. Existing palette tests still pass.

- [ ] **Step 6: Run and commit.**
  Run: `uv run pytest -q tests/test_mapreel_*.py -m "not slow" && node --test "reelmachine/recipes/mapreel/runtime/tests/*.test.mjs"`. Expected: all pass.
  Commit: `mapreel: world views fill the screen and take the short way round, names never steal each other's words, highlights hear aliases, sixty countries get sixty colours`.

---

### Task 2: `rank` segments — a big rank badge and a stat card

**Files:**
- Create: `reelmachine/recipes/mapreel/kinds.py`
- Modify: `reelmachine/recipes/mapreel/models.py`, `stages.py`, `compose.py`, `geometry.py` (`item_layout(..., safe=SAFE)`), `runtime/draw.mjs`, `runtime/pure.mjs`
- Test: `tests/test_mapreel_models.py`, `tests/test_mapreel_kinds.py` (new), `runtime/tests/pure.test.mjs`, `tests/test_mapreel_engine.py`

**Interfaces:**
- Consumes: Task 1's `frame_for(..., safe=)`, `match_any`.
- Produces:
  - `models.Stat {value: str (1–24 chars), label: str | None (≤ 40), source: str | None}`.
  - `models.RankSegment(kind="rank", rank: int 1..99, country: str, stat: Stat | None = None, items: list[Item] (0–8, default []))`.
  - `kinds.build(seg, spec, ctx: KindContext) -> Built`.
  - `Built` is a dataclass with `places: list[dict]`, `items: list[dict]`, `extras: dict` (merged into the segment document), `cues: list[tuple[int, str]]` (segment-relative ms, sfx name), `resolved: dict` (merged into the script.resolved entry) and `warnings: list[str]`.
  - `KindContext` is a dataclass holding what the stage loop computed: `atlas`, `landed: Frame`, `fly_ms`, `speech_start`, `speech_end`, `last_ms`, `spoken: list[WordSpan]`, `images: dict`.
  - `segment_document(..., extras=None)` merges `extras` into the document.
  - `RANK_SAFE = (0.08, 0.24, 0.92, 0.80)`.

- [ ] **Step 1: Model.** Add `Stat` and `RankSegment` to `models.py` and to the `Segment` union. In `stages.resolve_segments`, `_codes` returns `[segment.country]` for `rank`, and the error path is `segments[i].country`. Model tests:
  - rank 0 and rank 100 rejected;
  - `stat.value` of 25 chars rejected;
  - a rank with no items validates.

- [ ] **Step 2: Extract the per-kind builders.** Move the `country` and `highlight` logic out of `SegmentsStage.run` into `kinds.py` as `build_country` and `build_highlight`, dispatched by `build()` on `spec.kind`. The stage loop keeps camera/fly/layers/words/links/project writing and calls `build`. Behaviour must not change: the existing `tests/test_mapreel_stages.py` and the slow `tests/test_mapreel_engine.py` pass unchanged. Commit this refactor on its own: `mapreel: segment kinds are built in one place each`.

- [ ] **Step 3: `build_rank`.** Like `build_country`, but:
  - The frame is `frame_for(main, zoom, safe=RANK_SAFE)`, so the country sits below the badge. The geo stage frames rank segments with `safe=RANK_SAFE`.
  - Items lay out with `item_layout(..., safe=RANK_SAFE)`. Give `item_layout` a `safe` keyword that replaces its uses of `SAFE`.
  - `extras["rank"] = {"n": rank, "atMs": fill + 150, "colour": place colour, "gold": rank == 1}`, with cue `(atMs, "ding")`.
  - `extras["stat"] = {"value", "label", "atMs", "count": count_spec(value)}`. `atMs` is when the value's tokens are heard (`match_any([[value]])`), else `fill + 600`, clamped to `last_ms`. Cue `(atMs, "thud")`.
  - `count_spec(value)` in `kinds.py` parses the first number with `re.search(r"\d[\d,]*(?:\.\d+)?", value)`. It returns `{"prefix", "number": float, "decimals": int, "comma": bool, "suffix"}`, or `None` when there is no number. "70%" gives prefix "", number 70, decimals 0, comma False, suffix "%". "$1,200.50 bn" gives "$", 1200.5, 2, True, " bn".
  - Unit tests in `tests/test_mapreel_kinds.py`: `count_spec` cases; the rank badge follows the fill; a stat heard in the narration pops at its word; a stat not heard pops at fill + 600; the cues include ding and thud.

- [ ] **Step 4: Runtime.** In `draw.mjs`:
  - `rankBadge(ctx, rank, ms)` draws a disc at (190, 330), radius 125. It uses the place colour, or `#ffcc00` with dark text when `gold`, with a 10 px white ring and `#N` in a 900-weight 120 px font. It scales with `popState(atMs, ms)`. For gold, a white diagonal shine band sweeps across the disc, clipped, over 600 ms after the pop.
  - `statCard(ctx, stat, ms)` draws a white rounded card centred at x = 540, top y = 150, width = max(measured) + 80, with a 24 px radius. The value is 104 px, 900 weight, in the place colour. The label is 40 px, 700 weight, `#222`, below the value. The card pops with `popState`. The number counts up over 700 ms after `atMs` using `countText(count, p)`.
  - `countText(count, p)` goes in `pure.mjs`. It eases out, keeps decimals and comma grouping, and wraps prefix and suffix. With `count` null it returns the raw value.
  - `drawFrame` calls both when `doc.rank` / `doc.stat` exist, after items and before captions.
  - Node tests: `countText` at p = 0, 0.5 and 1 for "70%", "$1,200.50 bn" and a value with no number.

- [ ] **Step 5: Engine test.** Extend `tests/test_mapreel_engine.py`: a storyboard with a `rank` segment (rank 1, a stat, two items) succeeds. Its `script.resolved.json` entry has a `rank` and a `stat` with `atMs` inside the window.

- [ ] **Step 6: Run and commit.**
  Run: `uv run pytest -q tests/test_mapreel_*.py -m "not slow"`, then `uv run pytest -q tests/test_mapreel_engine.py tests/test_mapreel_stages.py`, then the node tests. Expected: pass.
  Commit: `mapreel: rank segments — a big number badge, a stat card that counts up, a gold number one`.

---

### Task 3: `versus` segments — split screen, one country per panel

**Files:**
- Modify: `models.py`, `stages.py` (geo panels, segments loop), `kinds.py`, `compose.py`, `runtime/draw.mjs`, `runtime/pure.mjs`
- Test: `tests/test_mapreel_models.py`, `tests/test_mapreel_kinds.py`, `tests/test_mapreel_stages.py`, `runtime/tests/pure.test.mjs`, `tests/test_mapreel_engine.py`

**Interfaces:**
- Consumes: `frame_for(bbox, zoom, view=, safe=)`, `Built`, `count_spec`, `countText`.
- Produces:
  - `models.VersusSegment(kind="versus", countries: list[str] (2–3), stats: list[Stat])`. A validator requires `len(stats) == len(countries)`, else `ValueError("versus needs one stat per country")`.
  - `GeoOutput.panels: dict[str, list[dict]]`. Each panel is `{"frame": {x,y,s}, "layer": path, "bbox": [..], "rect": [x,y,w,h]}`. The default is `{}`.
  - `kinds.panel_rects(n) -> list[tuple[int,int,int,int]]`. For n = 2: `(0,0,1080,960), (0,960,1080,960)`. For n = 3: three rows of 640.
  - `PANEL_SAFE = (0.08, 0.20, 0.92, 0.70)`.

- [ ] **Step 1: Model and resolution.** `_codes` returns `countries` for versus; the error path is `countries[j]`. Colours come from the palette as for a highlight. Tests: mismatched stats are rejected, and 4 countries are rejected.

- [ ] **Step 2: Geo panels.** For a versus segment, the geo stage computes one panel per country. The panel frame is `frame_for(shape.main, zoom=spec.camera.zoom, view=(w,h), safe=PANEL_SAFE)`. The panel's detail layer is `basemap.detail_layer(... visible_bbox for that view ...)`. Add a `view` argument to `visible_bbox` with the default `(WIDTH, HEIGHT)`. `frames[seg.id]` stays the full-screen union frame: the next segment flies from it.

- [ ] **Step 3: `build_versus`.** There is no fly; the segment cuts in.
  - `extras["panels"]` is a list of `{rect, camera: {x,y,s}, layers: [{url: "layers/panel-k.jpg", bbox}], place: place_entry(... frame=panel frame ...), name, stat: {value, label, atMs, count}, enterMs: k*120}`. The stage links `layers/panel-k.jpg`.
  - Stat k pops when its value is heard. Failing that, it pops when its country's spoken names are heard (`match_any([[value], [*spoken_names, query]])`, earliest). Failing both, it spreads with `pop_times`.
  - `extras["vs"] = {"atMs": 250}`, with cues `(0, "whoosh")`, `(250, "boom")` and `(stat.atMs, "thud")` per stat.
  - The document's top-level `places` and `items` are empty and its `layers` hold only the world layer (unused when panels exist).
  - In the stage loop, a versus segment never flies (`fly = 0`) and gets no `previous` fade. The segment after a versus flies from the versus's union frame, not a drifted frame. It has no `prev-detail` layer: its fly starts on the world layer.
  - Tests:
    - `panel_rects(2)` and `panel_rects(3)`;
    - a stat heard by value;
    - a stat heard only through its country name;
    - `test_segments_after_a_versus_fly_from_its_union_frame`: a stage-level test in `tests/test_mapreel_stages.py` style. Its camera `from` equals the versus frame and its layers do not include `prev-detail`.

- [ ] **Step 4: Runtime.** In `draw.mjs`, when `doc.panels` exists, `drawFrame` draws each panel and then the VS disc and captions.
  - Each panel: `ctx.save()`, then clip to `rect` translated by a slide-in offset. Panel k slides from the left (even k) or right (odd k) over 350 ms after `enterMs` with `easeInOut`; `slideOffset(enterMs, ms, dir)` goes in `pure.mjs`.
  - Inside the panel, draw its layers with a camera whose screen centre is the panel centre. Refactor `toScreen(cam, x, y)` to take an optional view `{cx, cy}`, default `(W/2, H/2)`, and thread it through `drawLayers` and `tracePlace`.
  - Fill its place with `fillAlpha(0, ms)`.
  - Draw the country name at the panel top (y = rect.y + 0.12·h, 72 px, 900 weight, white with a 10 px dark stroke).
  - Draw the stat card centred at y = rect.y + 0.80·h, scaled by `popState`, with the count-up.
  - Panels are separated by a 6 px white line.
  - The VS disc is radius 95, centred on the first panel boundary, `#111` with a 8 px white ring and "VS" 80 px white. It pops at `vs.atMs`.
  - Node tests for `slideOffset`.

- [ ] **Step 5: Engine test and commit.**
  Engine test: a storyboard with a versus (JPN vs USA, two stats) followed by a country segment succeeds. Its resolved entry lists two panels with stat times inside the window.
  Run the Task 2 commands. Commit: `mapreel: versus segments — two or three countries side by side, stats that count up, a VS slam`.

---

### Task 4: Admin-1 regions — data, resolution, `find_places`

**Files:**
- Modify: `reelmachine/recipes/mapreel/geodata.py` (`FILES["regions"]`, `LICENCES`)
- Create: `reelmachine/recipes/mapreel/regions.py` (`RegionAtlas`, `region_atlas_for`)
- Modify: `tools/mapreel_fixture.py` (cut a regions fixture)
- Create: `tests/fixtures/mapreel/regions.geojson`
- Modify: `reelmachine/mcp/tools.py`, `reelmachine/mcp/server.py` (`find_places(query, limit, country=None)`), `docs/MCP.md`
- Test: `tests/test_mapreel_regions.py` (new), `tests/test_mapreel_mcp.py`

**Interfaces:**
- Produces:
  - `FILES["regions"] = ("ne_10m_admin_1_states_provinces.geojson", f"{_NE}/ne_10m_admin_1_states_provinces.geojson")` (about 40 MB, fetched only when a regions segment or a region search needs it).
  - `RegionAtlas.load(path)`, cached like `Atlas`.
  - `RegionAtlas.resolve(query, country: str) -> Place` with `level="region"`.
  - `RegionAtlas.in_country(country) -> list[Place]`.
  - `RegionAtlas.search(query, country, limit) -> list[dict]`.
  - `region_atlas_for(settings) -> RegionAtlas`.
  - A region `Place.code` is the ISO 3166-2 code (`US-TX`, `JP-13`); `Place.name` is the English name.

- [ ] **Step 1: Read the real properties first.** Download the real file once to the scratch directory, not the repo. Print one feature's property keys for a US state, a Japanese prefecture and an Indian state, and pick the fields:
  - code: `iso_3166_2`;
  - parent: `adm0_a3`;
  - names: `name`, `name_en`, `name_alt` (pipe-separated), `woe_name`, `gn_name`;
  - label: `longitude`, `latitude`.

  Confirm each against the file before using it. Some features have an `iso_3166_2` ending in `~`, or a code shared with another feature. Skip features whose code does not match `^[A-Z]{2}-[A-Z0-9]{1,3}$`.

- [ ] **Step 2: Fixture.** Extend `tools/mapreel_fixture.py` with a `--regions` path that writes `tests/fixtures/mapreel/regions.geojson`. It keeps US-TX, US-CA, US-NY, US-FL, US-GA, JP-13, JP-27, JP-01 and IN-PB, plus PK-PB if present, simplified with tolerance 0.02 and only the fields above. Keep the file under 300 KB. Run it and commit the fixture.

- [ ] **Step 3: `RegionAtlas`.** Index codes and normalised names (`places.norm`) per parent country. `resolve(query, country)` checks in this order:
  - an exact code (case-insensitive) belonging to that country;
  - a code from another country, which is `InvalidInput` "US-TX is not in JPN";
  - a name within that country;
  - several matches, which is `InvalidInput` listing candidates;
  - none, which is `InvalidInput` with close names from that country (`difflib`).

  Tests in `tests/test_mapreel_regions.py`:
  - `test_region_codes_resolve`;
  - `test_region_names_resolve_inside_their_country`: "Georgia" in USA is US-GA; "Punjab" in IND is IN-PB;
  - `test_a_region_outside_its_country_is_refused`;
  - `test_unknown_region_suggests_close_names` ("Texsa" → "Texas");
  - `test_in_country_lists_regions`.

- [ ] **Step 4: `find_places` regions.** Add `country: str | None = None` to the MCP tool and server signature. With `country`, the tool searches that country's regions (loading the regions atlas). An empty query lists all of them, up to `limit`. Results are `{"code", "name", "level": "region", "parent": country, "ambiguous"}`. Without `country`, a query shaped like `XX-YYY` also searches regions; otherwise only countries are searched, as now. Each country result gains `"regions": true` when the country has admin-1 data, but only if the regions file is already cached. Never download for a plain country search. Update the hint text and `docs/MCP.md`'s `find_places` section. Tests in `tests/test_mapreel_mcp.py` seed both fixtures: a region search, the code shape, and a country-only search not touching the regions file (assert it was never opened by making the regions path absent).

- [ ] **Step 5: Run and commit.** Run `uv run pytest -q tests/test_mapreel_regions.py tests/test_mapreel_mcp.py tests/test_mcp.py`. Commit: `mapreel: states and prefectures — find_places lists a country's regions, scripts name them by code or name`.

---

### Task 5: `regions` segments — a country's states fill and carry their items

**Files:**
- Modify: `models.py`, `stages.py` (resolution, geo), `kinds.py`, `runtime/draw.mjs`
- Test: `tests/test_mapreel_models.py`, `tests/test_mapreel_kinds.py`, `tests/test_mapreel_engine.py`

**Interfaces:**
- Consumes: `RegionAtlas`, `item_layout(..., max_size=, min_size=, safe=)`, `match_any`.
- Produces:
  - `models.RegionEntry {region: str, say: str | None, items: list[Item] (0–6), colour: str | None}`.
  - `models.RegionsSegment(kind="regions", country: str, regions: list[RegionEntry] (1–12))`.
  - `ResolvedSegment.regions: list[dict] = []`. Each entry is `{code, name, colour}`.

- [ ] **Step 1: Model and resolution.** `resolve_segments` resolves the country as usual. When any segment is `regions`, it loads `region_atlas_for(settings)` once and resolves each region inside the segment's country (error path `segments[i].regions[j].region`). Region colours come from `palette.assign` over the group of that segment's region codes, with the country's own colour as a fixed neighbour. The recipe `probe` resolves regions the same way, so a bad region fails `probe`. Tests: a bad region path, and region colours that differ from each other and from the country.

- [ ] **Step 2: Geo.** The frame and detail layer are the country's, as for `country`.

- [ ] **Step 3: `build_regions`.**
  - `places` is the country with `extras["countryFaint"] = True`. The runtime fills it at alpha 0.25 when this is set, and the ghost name still shows.
  - `extras["borders"]` is the ring list of every region in the country (`in_country`), simplified with `simplify_for(landed, ring)`, drawn thin.
  - `extras["regions"]` is a list of `{code, name, colour, rings, fillMs, label: {x,y}}`. `fillMs` is when the region's `say`, its name, or its first item is heard, else spread, clamped to `last_ms`.
  - Items: each region's items lay out inside that region's own rings, with `max_size=220`, `min_size=120`. Each pops when heard (not before its region's `fillMs`). They are returned in `items` with x/y/size as usual.
  - Cues: `(fillMs, "pop")` per region and `(atMs, "pop")` per item.
  - Tests:
    - a region fills when its name is heard;
    - a region with `say` fills at the `say` words;
    - an item never pops before its region fills;
    - items lie inside their region (`geometry.inside` on the anchor).

- [ ] **Step 4: Runtime.** In `draw.mjs`:
  - Faint country fill: alpha 0.25 when `doc.countryFaint`.
  - Borders: `rgba(255,255,255,0.55)`, 2 px.
  - Each region: `fillPlace` with its own `fillMs`. A name tag at its label point appears with `fillAlpha`: 44 px, 900 weight, white, 8 px dark stroke, and never off screen (clamp the x/y like `ghostCentre`).
  - Ghost name for the country only.

- [ ] **Step 5: Engine test and commit.** Engine test: a storyboard with a regions segment (USA: Texas with BBQ, California with Avocado) seeded with both fixtures succeeds, and its resolved entry has two regions with fill times. Commit: `mapreel: regions segments — states and prefectures fill when named and carry their own items`.

---

### Task 6: The hook lint — eight rules, two of them hard

**Files:**
- Create: `reelmachine/recipes/mapreel/lint.py`
- Modify: `models.py` (`lint: Literal["strict","warn"] = "strict"`), `recipe.py` (probe), `stages.py` (ScriptStage refuses in build mode)
- Create: `tests/test_mapreel_lint.py`
- Modify: `examples/mapreel/brands.json` only if it fails a rule (it must pass rules 1 and 2)

**Interfaces:**
- Produces:
  - `lint.Finding(rule: str, severity: Literal["error","warning"], message: str, hint: str, segment: str | None = None)`, a dataclass.
  - `lint.check(inputs: MapReelInputs, windows: Sequence[ShotWindow]) -> list[Finding]`.
  - `lint.estimate_beats(inputs, windows) -> list[list[float]]`: seconds, reel time, per segment.

The rules, with exact thresholds (from the spec §6):

```python
HOOK_WINDOW_WORDS = 7         # about the first 2 s of speech
BEAT_GAP_S = 1.2
REHOOK_GAP_S = 12.0
MONOTONY_RUN = 4
NUMBER_WORDS = r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|hundred|thousand|million|billion|first|last|half"
HOOK_SIGNAL = re.compile(
    rf"\d|\?|\b(you|your|you're|only|never|nobody|no one|secret|surprise\w*|guess|wait|actually|why|how|"
    rf"what|which|most|least|biggest|smallest|richest|poorest|best|worst|weirdest|\w+est|{NUMBER_WORDS})\b", re.I)
WEAK_START = re.compile(r"^(welcome|today|hi|hello|hey guys|in this video|this is|let's talk)\b|^[\w .'-]{1,30}\s(has|have|is home to)\b", re.I)
TEASE = re.compile(r"\b(number (one|1)|no\.? ?1|the last one|surprise\w*|guess|wait (for|till|until)|stay (until|till)|at the end|the answer)\b", re.I)
PAYOFF = re.compile(r"\b(number (one|1)|no\.? ?1|finally|the answer|the winner|and the last|last but not least|drumroll)\b", re.I)
REHOOK = re.compile(r"\b(but|wait|here's|now|guess|surprising\w*|plot twist|believe it or not|number \w+|even|actually|meanwhile|and the)\b|\?", re.I)
CTA = re.compile(r"\b(comment|comments|follow|share|save|subscribe|tell me|let me know|which|what|would you|did i miss)\b|\?", re.I)
```

1. `weak-opener` (error): segment 1's spoken text (`spoken_text(parse_markup(...))`) either matches `WEAK_START`, or has no `HOOK_SIGNAL` match in its first `HOOK_WINDOW_WORDS` words.
2. `unpaid-loop` (error): segment 1 matches `TEASE`. It is paid when a later segment is `rank` with `rank == 1`, or a later segment's narration matches `PAYOFF`. Otherwise it is an error.
3. `beat-gap` (warning, one per segment): inside each segment's speech span (`start + speech_offset` to `+ speech_ms`), the gap between consecutive beats (including the span's ends) is > `BEAT_GAP_S`. Beats come from `estimate_beats`:
   - a segment start, unless it is a `camera.hold` segment;
   - fills: the country is filled at `FLY_MS`; highlight countries and regions are filled when named;
   - every item, stat, rank badge, VS and versus stat.

   A name's time is estimated from its position in the text. The first character of the match, over the text length, times the speech estimate, plus the speech offset. An unmatched name spreads like `pop_times`.
4. `no-rehook` (warning): rehook times are segment-1 end and the start of every `rank`/`versus`/`highlight` segment after the first. They also include the estimated time of every `REHOOK` match in any narration. Any gap > `REHOOK_GAP_S` between consecutive rehooks, from 0 to the reel end, is a finding naming the span.
5. `monotony` (warning): `MONOTONY_RUN` or more consecutive `country` segments. The hint suggests a `rank`, `versus` or `highlight` between them.
6. `length` (warning): with `length` set and `estimated_s < 0.85 * length`, warn that the target means padding and suggest the estimate. Over 75 s estimated, warn that most map reels stay under 60 s.
7. `unsourced` (warning, one finding listing names): every item, stat and `rank` segment with neither its own `source` nor its segment's `source`.
8. `no-ending` (warning): the last segment's narration has no `CTA` match and the last segment is not `rank` 1.

Severity: rules 1–2 are `error` only when `inputs.mode == "build"` and `inputs.lint == "strict"`; otherwise they are `warning`.

- [ ] **Step 1: Tests first.** In `tests/test_mapreel_lint.py`, write one failing and one passing script per rule (16 tests). Add `test_the_reference_reel_fails_its_hook`, a regression. It is 15 `country` segments, each "The United States has Nike, Apple, Coca-Cola and Ford." (vary the country and brands), 2–4 items each, no hook. It must produce `weak-opener`, `no-rehook` and `monotony`. Add `test_brands_example_passes_the_hard_rules`, which loads `examples/mapreel/brands.json`: no `error` findings. Add `test_unpaid_loop_accepts_a_spoken_reveal`: a hook "Number one will surprise you", where the last segment is a highlight saying "And number one is Turkey", passes rule 2.

- [ ] **Step 2: Implement `lint.py`** to pass them.

- [ ] **Step 3: Wire it.**
  - `probe` runs `lint.check` after windows are computed. It adds `details["lint"] = [asdict(f) ...]`. Error findings add a failed `CheckResult(name="hook", ok=False, detail="; ".join(messages))`; otherwise `CheckResult(name="hook", ok=True, detail=f"{n} suggestions")`.
  - `ScriptStage` runs the same check with estimated windows (reuse the probe's estimate code: move it to a helper `estimated_windows(inputs)` in `stages.py`). It raises `InvalidInput` for the first error finding, with `details={"path": "segments[0].narration", "rule": ...}`, so a build never starts on a weak hook.
  - Warnings are appended to `script.resolved.json`'s `warnings` by the segments stage. Carry them through `ScriptOutput.lint: list[dict] = []`.
  - Tests in `tests/test_mapreel_probe.py`: probe on a weak opener in build mode fails `hook`; in storyboard mode, or with `lint: "warn"`, it passes with the finding in `details["lint"]`.

- [ ] **Step 4: Run and commit.** Run `uv run pytest -q tests/test_mapreel_*.py -m "not slow"`. Commit: `mapreel: the hook lint — probe names every weak beat, and a build refuses a reel with no hook or an unpaid tease`.

---

### Task 7: The guide, a topic bank and a showcase example

**Files:**
- Create: `reelmachine/recipes/mapreel/guide.py` (`GUIDE` moves here)
- Modify: `recipe.py` (import `GUIDE`, `EXAMPLE` uses every kind)
- Create: `examples/mapreel/food.json` (every kind: highlight hook, country, rank, versus, regions, rank-1 payoff, CTA)
- Test: `tests/test_mapreel_probe.py` (the example passes `probe` with no hook error; the guide has the keys)

- [ ] **Step 1: `GUIDE`** has these keys:
  - `workflow` (the spec §6 order, naming `find_places` with `country=` for regions);
  - `kinds` (one line per kind with its required fields);
  - `hooks` (5 formulas, each with an example: curiosity gap, challenge, shock stat, "you" framing, contradiction);
  - `rehooks` (10 phrases);
  - `endings` (pay off the loop, then a comment-bait CTA; 4 examples);
  - `topics` (30 map-reel angles, e.g. "Brands you didn't know were from these countries", "What each country is weirdly famous for", "Countries that banned something normal", "Fast food chains that failed in one country", "The most expensive city to buy a Big Mac");
  - `lint` (one line per rule: what it checks and how to fix it);
  - `tips` (the Plan 1 tips plus "every stat needs a source").

- [ ] **Step 2: `examples/mapreel/food.json`.** A reel about what countries eat that others find strange (or similar), 8–10 segments, using every kind. Items have `emoji`, each stat has a `source` URL to a real page (Wikipedia is fine), and the voice is `fake:default`. Check every fact against the cited page before writing it.

- [ ] **Step 3: Tests.** The guide keys exist and `topics` has ≥ 30 entries. `food.json` and `brands.json` probe with no `error` findings, using the fixtures. food.json's places must be in the fixture or be added to it: extend `KEEP` in `tools/mapreel_fixture.py` if needed and re-cut.

- [ ] **Step 4: Commit.** `mapreel: a guide with hook formulas, re-hooks, endings and thirty topics; a food reel that uses every shot`.

---

### Task 8: A real render of every kind

**Files:**
- Modify: `tests/test_mapreel_render.py`
- Modify: `.github/workflows/ci.yml` only if the test file name changes (it does not)

- [ ] **Step 1:** Add a slow test that renders `examples/mapreel/food.json` in storyboard mode with the real map data and HyperFrames (skip when Node ≥ 22 or HyperFrames is missing, like the existing test). Assert:
  - `storyboard.jpg` exists;
  - each still is not blank: mean pixel std > 10 per tile, read with Pillow, tiles 360×640 with 8 px padding;
  - the versus tile's top and bottom halves differ (two maps).

- [ ] **Step 2:** Run it locally with `REEL_HYPERFRAMES_WORKERS=1`. Look at the storyboard yourself. Fix anything visibly broken in the runtime (overlaps, off-screen text) in this task.

- [ ] **Step 3: Commit.** `mapreel: a real render of every segment kind in the test suite`.
