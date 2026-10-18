# mapreel Plan 1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `mapreel` recipe that turns a script of `country` and `highlight` segments into a narrated satellite-map reel (fly-to, fill + glow, ghosted name, items that pop when spoken, word captions), with a storyboard mode, `probe`, and an MCP `find_places` tool — enough to remake the reference reel.

**Architecture:** A new recipe package `reelmachine/recipes/mapreel/` following explainer3d's stage pattern. Python does all geography: Natural Earth polygons are projected to Web Mercator (unit square), framed for a 1080×1920 screen, and the NASA Blue Marble texture is cropped and reprojected (ffmpeg + numpy) into a low-res world layer plus a high-res detail layer per segment. Each segment becomes one HyperFrames HTML project whose small canvas runtime (`runtime/*.mjs`) draws the frame for any seek time; segments are rendered and joined with hard cuts (the camera at the end of segment *i* equals the start of *i+1*, so cuts are invisible).

**Tech Stack:** Python 3.11, pydantic 2, numpy, httpx, ffmpeg/ffprobe; HyperFrames 0.8.146 via Node ≥ 22 (existing `reelmachine/render/hyperframes.py`); plain ES modules + Canvas 2D in the runtime (no JS dependencies); pytest, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-17-mapreel-design.md`

## Global Constraints

- Output is 1080×1920 at 30 fps (`WIDTH, HEIGHT, FPS = 1080, 1920, 30`).
- Map data: Natural Earth **v5.1.2** admin-0 10m GeoJSON; NASA Blue Marble NG `world.topo.bathy.200412` at 5400×2700 (world layer) and 21600×10800 (detail layer). Both public domain; licence and provenance recorded on every use.
- Downloaded data lives in the shared cache `(settings.shared_cache or settings.cache_dir) / "mapreel" / "geo"`, fetched once.
- No new Python dependencies (no Pillow, Shapely or d3); no JS dependencies in the runtime.
- Modes follow the repo: `mode` is `"build"` (default) or `"storyboard"` (the spec's "full" = `"build"`). Voices are strings `"<provider>:<voice>"` like explainer3d (`"fake:default"`, `"cartesia:<id>"`).
- Places are ISO 3166-1 alpha-3 codes or names; the canonical code is Natural Earth's `ISO_A3_EH`, falling back to `ADM0_A3` when that is `-99`.
- An item pops when its `say` (or `name`) is spoken; `at` (seconds from segment start) overrides.
- A failure to fetch or read an item image is a warning plus a text badge, never a job failure.
- Errors are `InvalidInput` / `ProviderUnavailable` / `RendererUnavailable` / `VoiceUnavailable` from `reelmachine.core.errors`, with a `hint`.
- Commit messages follow the repo style (`mapreel: <what changed, in plain words>`) and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Plan 1 scope: kinds `country` and `highlight`, look `satellite`, images from local paths and `https` URLs (a `commons:` id is accepted by the schema and becomes a badge with a warning until Plan 3).

## Review Focus

1. **Countries with far-flung parts** (France + French Guiana, USA + Alaska/Hawaii, Norway + Svalbard): the camera must frame the main landmass, not the whole spread — pinned in Task 3.
2. **Countries across the antimeridian** (Russia, Fiji): parts at −180° must be drawn next to the main body, not across the world — pinned in Task 3.
3. **Brand names that TTS splits or spells differently** ("H&M", "Dolce & Gabbana", "Levi's", "Fjällräven" said as "Fjallraven"): items must still pop on the right word — pinned in Task 5.
4. **Tiny countries** (Singapore, Luxembourg) and **huge ones** (Russia, Canada): framing must clamp zoom so the texture never upscales past 3× and the world never shrinks below screen width — pinned in Task 3.
5. **An item image that is missing, non-image or a 404 URL**: the reel still builds with a badge and a warning naming the item — pinned in Task 8.

---

## File Structure

```
reelmachine/recipes/mapreel/
  __init__.py          package marker
  models.py            input schema (MapReelInputs, segments, items) + stage output models
  geodata.py           data URLs, licences, fetch-once cache
  places.py            Atlas: load Natural Earth, resolve/search places, aliases
  geometry.py          Web Mercator, simplify, main-landmass cluster, framing, item anchors
  palette.py           vivid fill colours, no two alike in a segment or across a cut
  basemap.py           world layer + per-segment detail layer (ffmpeg crop, numpy reprojection)
  timing.py            spoken-name matching and pop times
  compose.py           segment documents, HyperFrames project writer
  stages.py            script → geo → images → narration → segments → prep → compose → render
  recipe.py            MapReelRecipe: spec, probe, guide
  runtime/
    main.mjs           boots a segment, answers HyperFrames seeks
    pure.mjs           camera/zoom interpolation, easing, pop curves (no DOM)
    draw.mjs           canvas drawing: layers, fills, glow, ghost name, items, captions, legend
    tests/pure.test.mjs
tests/
  fixtures/mapreel/countries.geojson   small real-geometry subset (built by tools/mapreel_fixture.py)
  test_mapreel_models.py
  test_mapreel_places.py
  test_mapreel_geometry.py
  test_mapreel_basemap.py
  test_mapreel_timing.py
  test_mapreel_compose.py
  test_mapreel_engine.py
  test_mapreel_render.py
tools/mapreel_fixture.py
examples/mapreel/brands.json            the reference reel, rewritten with a hook
```

Modified: `pyproject.toml` (entry point, package data), `reelmachine/recipes/explainer3d/stages.py` (make `_speak` public as `speak_line`), `reelmachine/mcp/tools.py` + `reelmachine/mcp/server.py` (`find_places`), `.github/workflows/ci.yml` (runtime tests), `docs/MCP.md` (one tool entry).

---

### Task 1: Input schema and recipe registration

**Files:**
- Create: `reelmachine/recipes/mapreel/__init__.py`, `reelmachine/recipes/mapreel/models.py`, `reelmachine/recipes/mapreel/recipe.py`
- Modify: `pyproject.toml` (entry point under `[project.entry-points."reelmachine.recipes"]`)
- Test: `tests/test_mapreel_models.py`

**Interfaces:**
- Produces: `MapReelInputs`, `Item`, `Camera`, `CountrySegment`, `HighlightSegment`, `Segment` (discriminated union on `kind`), `MapReelInputs.segment_ids() -> list[str]` (`"seg-1"`, …); `MapReelRecipe.spec` with `id="mapreel"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mapreel_models.py
import pytest
from pydantic import ValidationError

from reelmachine.recipes.mapreel.models import MapReelInputs

BASE = {"segments": [{"kind": "country", "country": "ITA", "narration": "Italy has Gucci.",
                      "items": [{"name": "Gucci"}]}]}


def test_a_country_segment_validates_with_defaults():
    inputs = MapReelInputs.model_validate(BASE)
    seg = inputs.segments[0]
    assert inputs.mode == "build" and inputs.look == "satellite" and inputs.voice == "fake:default"
    assert seg.kind == "country" and seg.items[0].name == "Gucci" and seg.camera.zoom == 1.0
    assert inputs.segment_ids() == ["seg-1"]


def test_highlight_segment_takes_many_countries_and_a_legend():
    inputs = MapReelInputs.model_validate({"segments": [
        {"kind": "highlight", "countries": ["USA", "Italy", "FRA"], "legend": "Fashion giants",
         "narration": "Only three countries..."}]})
    assert inputs.segments[0].countries == ["USA", "Italy", "FRA"]


@pytest.mark.parametrize("bad", [
    {"kind": "planet", "narration": "x"},                                           # unknown kind
    {"kind": "country", "country": "ITA", "narration": "x", "items": []},           # no items
    {"kind": "country", "country": "ITA", "narration": "x", "items": [{"name": "A"}] * 9},  # > 8
    {"kind": "country", "country": "ITA", "narration": "x", "items": [{"name": "A"}], "colour": "red"},
    {"kind": "country", "country": "ITA", "narration": "x", "items": [{"name": "A", "at": -1}]},
    {"kind": "highlight", "countries": [], "narration": "x"},
])
def test_bad_segments_are_refused(bad):
    with pytest.raises(ValidationError):
        MapReelInputs.model_validate({"segments": [bad]})


def test_item_image_accepts_paths_urls_and_commons_ids():
    for image in ("./logos/a.png", "https://example.com/a.png", "commons:File:Gucci logo.svg"):
        item = MapReelInputs.model_validate({"segments": [{"kind": "country", "country": "ITA",
            "narration": "x", "items": [{"name": "A", "image": image}]}]}).segments[0].items[0]
        assert item.image == image
    with pytest.raises(ValidationError):
        MapReelInputs.model_validate({"segments": [{"kind": "country", "country": "ITA",
            "narration": "x", "items": [{"name": "A", "image": "ftp://x/a.png"}]}]})


def test_the_recipe_is_registered():
    from importlib.metadata import entry_points

    names = {ep.name for ep in entry_points(group="reelmachine.recipes")}
    assert "mapreel" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mapreel_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reelmachine.recipes.mapreel'`

- [ ] **Step 3: Write the models**

```python
# reelmachine/recipes/mapreel/__init__.py
"""mapreel: narrated world-map reels — fly to a country, fill it, pop what the voice names."""
```

```python
# reelmachine/recipes/mapreel/models.py
"""The mapreel script: an ordered list of map segments, each with its own narration."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

Mode = Literal["build", "storyboard"]
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _colour(value: str | None) -> str | None:
    if value is not None and not _HEX.match(value):
        raise ValueError("colours are #rrggbb")
    return value


class Item(BaseModel):
    """One thing shown on a place: a brand, a dish, an invention."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    say: str | None = Field(None, max_length=80)        # how the voice says it; matched instead of name
    image: str | None = Field(None, max_length=500)     # commons:File:…, https://…, or a local path
    emoji: str | None = Field(None, max_length=8)
    source: str | None = Field(None, max_length=500)
    at: float | None = Field(None, ge=0)                # seconds from segment start; overrides speech

    @field_validator("image")
    @classmethod
    def _image(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if "://" in value and not value.startswith(("http://", "https://")):
            raise ValueError("an image is 'commons:File:<name>', an http(s) URL or a local path")
        return value


class Camera(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zoom: float = Field(1.0, ge=0.5, le=3.0)
    hold: bool = False


class _Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narration: str = Field(min_length=1, max_length=400)
    camera: Camera = Field(default_factory=Camera)
    source: str | None = Field(None, max_length=500)
    colour: str | None = None

    _check_colour = field_validator("colour")(classmethod(lambda cls, v: _colour(v)))


class CountrySegment(_Segment):
    kind: Literal["country"]
    country: str = Field(min_length=2, max_length=60)
    items: list[Item] = Field(min_length=1, max_length=8)


class HighlightSegment(_Segment):
    kind: Literal["highlight"]
    countries: list[str] = Field(min_length=1, max_length=60)
    legend: str | None = Field(None, max_length=60)


Segment = Annotated[Union[CountrySegment, HighlightSegment], Field(discriminator="kind")]


class MapReelInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode = "build"
    title: str = Field("", max_length=80)
    language: str = "en"
    voice: str = "fake:default"
    look: Literal["satellite"] = "satellite"
    music: str | None = None
    length: int | None = Field(None, ge=15, le=90)
    segments: list[Segment] = Field(min_length=1, max_length=40)
    name: str | None = None
    outdir: str | None = None

    def segment_ids(self) -> list[str]:
        return [f"seg-{index}" for index in range(1, len(self.segments) + 1)]
```

- [ ] **Step 4: Write the recipe skeleton and register it**

```python
# reelmachine/recipes/mapreel/recipe.py
"""The mapreel recipe: a script of map segments becomes a narrated satellite-map reel."""

from __future__ import annotations

from typing import Any

from ...core.recipe import CostNote, ProbeContext, ProbeReport, ProviderReq, RecipeSpec
from ...core.stage import StageContext
from .models import MapReelInputs

EXAMPLE = {
    "mode": "storyboard",
    "title": "Brands you didn't know were from these countries",
    "segments": [
        {"kind": "highlight", "countries": ["USA", "ITA", "FRA"], "legend": "Fashion superpowers",
         "narration": "Only *3 countries* dress the whole world. Number one will surprise you."},
        {"kind": "country", "country": "ITA", "narration": "Italy has Gucci, Prada and Versace.",
         "items": [{"name": "Gucci"}, {"name": "Prada"}, {"name": "Versace"}]},
    ],
}


class MapReelRecipe:
    spec = RecipeSpec(
        id="mapreel",
        title="Map reel",
        summary=(
            "A script of map segments becomes a narrated vertical reel on a satellite world map: "
            "the camera flies to each country, fills it and pops in what the voice names. "
            "`storyboard` renders one still per segment."
        ),
        input_model=MapReelInputs,
        version=1,
        stages=(),
        providers={
            "narration": ProviderReq(group="narration", default="fake",
                                     description="Text-to-speech with word timings"),
            "renderer": ProviderReq(group="renderers", default="hyperframes",
                                    description="Renders each segment (Node >= 22, HyperFrames, Chrome, ffprobe)"),
        },
        cost=CostNote(unit="tts_characters", estimate="narration length",
                      notes="the fake voice is free; cloud voices bill per character",
                      providers=("elevenlabs", "cartesia", "fake")),
        artifacts=("reel.mp4", "captions.srt", "reel.json", "timeline.json", "storyboard.jpg",
                   "script.resolved.json"),
        render_modes=("hyperframes",),
        example=EXAMPLE,
    )

    def probe(self, inputs: MapReelInputs, ctx: ProbeContext) -> ProbeReport:
        return ProbeReport(ok=True)

    def verify(self, ctx: StageContext, result: dict[str, Any]) -> None:
        return None
```

In `pyproject.toml`, under `[project.entry-points."reelmachine.recipes"]`, after the `explainer3d` line add:

```toml
mapreel = "reelmachine.recipes.mapreel.recipe:MapReelRecipe"
```

Then refresh the installed entry points: `uv pip install -e . -q` (or `uv sync`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_mapreel_models.py -v`
Expected: all PASS. If `_check_colour` does not run (pydantic ignores the lambda form), replace it with a normal method:

```python
    @field_validator("colour")
    @classmethod
    def _check_colour(cls, value: str | None) -> str | None:
        return _colour(value)
```

- [ ] **Step 6: Commit**

```bash
git add reelmachine/recipes/mapreel pyproject.toml tests/test_mapreel_models.py
git commit -m "mapreel: the script schema — country and highlight segments with items — and the recipe entry point

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Map data, the atlas and place resolution

**Files:**
- Create: `reelmachine/recipes/mapreel/geodata.py`, `reelmachine/recipes/mapreel/places.py`, `tools/mapreel_fixture.py`, `tests/fixtures/mapreel/countries.geojson` (generated)
- Test: `tests/test_mapreel_places.py`

**Interfaces:**
- Produces:
  - `geodata.geo_cache(settings) -> Path`; `geodata.ensure(root: Path, key: str) -> Path` with keys `"countries"`, `"world"`, `"detail"`; `geodata.LICENCES: dict[str, Licence]`; `geodata.FILES: dict[str, tuple[str, str]]` (key → (filename, url)).
  - `places.Place` (frozen dataclass: `code: str`, `name: str`, `polygons: tuple[tuple[tuple[tuple[float, float], ...], ...], ...]` — polygons → rings → (lon, lat); first ring outer), `label: tuple[float, float]` (lon, lat)).
  - `places.Atlas.load(path: Path) -> Atlas` (cached per path+mtime), `Atlas.resolve(query: str) -> Place` (raises `InvalidInput` with `details={"query", "candidates"|"suggestions"}`), `Atlas.search(query: str, limit: int = 8) -> list[dict]` (each `{"code", "name", "level": "country", "ambiguous": bool}`).
  - `places.atlas_for(settings) -> Atlas` (ensures the countries file, then loads).

- [ ] **Step 1: Build the test fixture from real data**

```python
# tools/mapreel_fixture.py
"""Cut a small, real-geometry Natural Earth subset for mapreel's tests.

    uv run python tools/mapreel_fixture.py

Downloads admin-0 10m once (13 MB), keeps a few countries that exercise the hard
cases (far territories, the antimeridian, ambiguous names, tiny states) and
simplifies them so the fixture stays small.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reelmachine.recipes.mapreel.geodata import FILES  # noqa: E402
from reelmachine.recipes.mapreel.geometry import simplify  # noqa: E402

KEEP = {"ITA", "FRA", "USA", "RUS", "FJI", "KOR", "PRK", "NLD", "GBR", "JPN", "SGP", "NOR", "CHE", "DNK", "ZAF", "LSO"}
PROPS = ("ISO_A3_EH", "ADM0_A3", "ISO_A2_EH", "NAME", "NAME_LONG", "NAME_EN", "ADMIN", "FORMAL_EN",
         "BRK_NAME", "ABBREV", "NAME_ALT", "LABEL_X", "LABEL_Y")
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "mapreel" / "countries.geojson"


def main() -> None:
    url = FILES["countries"][1]
    data = httpx.get(url, timeout=120, follow_redirects=True).json()
    feats = []
    for f in data["features"]:
        p = f["properties"]
        if p["ADM0_A3"] not in KEEP:
            continue
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        tol = 0.002 if p["ADM0_A3"] in {"SGP", "LSO", "CHE"} else 0.03
        slim = []
        for poly in polys:
            rings = [[list(map(float, pt)) for pt in simplify(ring, tol)] for ring in poly]
            if len(rings[0]) >= 4:
                slim.append(rings)
        feats.append({"type": "Feature", "properties": {k: p.get(k) for k in PROPS},
                      "geometry": {"type": "MultiPolygon", "coordinates": slim}})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(feats)} countries)")


if __name__ == "__main__":
    main()
```

This tool imports `simplify` from `geometry.py`, which Task 3 writes in full. Create `geometry.py` now with just this function (Task 3 adds the rest to the same file):

```python
# reelmachine/recipes/mapreel/geometry.py
"""Map geometry in Web Mercator's unit square: x, y in [0, 1], y down."""

from __future__ import annotations

from typing import Sequence

import numpy as np


def simplify(points: Sequence[Sequence[float]], tolerance: float) -> np.ndarray:
    """Douglas–Peucker on an (n, 2) polyline; keeps both ends."""
    pts = np.asarray(points, dtype=float)
    if len(pts) < 3 or tolerance <= 0:
        return pts
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        a, b = pts[lo], pts[hi]
        seg = b - a
        span = np.hypot(*seg)
        mid = pts[lo + 1:hi]
        if span == 0:
            dist = np.hypot(*(mid - a).T)
        else:
            dist = np.abs(seg[0] * (mid[:, 1] - a[1]) - seg[1] * (mid[:, 0] - a[0])) / span
        i = int(np.argmax(dist))
        if dist[i] > tolerance:
            k = lo + 1 + i
            keep[k] = True
            stack.extend([(lo, k), (k, hi)])
    return pts[keep]
```

Then write `geodata.py` (Step 3 below) before running the tool, and run: `uv run python tools/mapreel_fixture.py`. Expected output: `wrote …/countries.geojson (<300 KB, 16 countries)`. If larger than 400 KB, raise the default tolerance to 0.05.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_mapreel_places.py
from pathlib import Path

import pytest

from reelmachine.core.errors import InvalidInput
from reelmachine.recipes.mapreel.places import Atlas

FIXTURE = Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson"


@pytest.fixture(scope="module")
def atlas() -> Atlas:
    return Atlas.load(FIXTURE)


@pytest.mark.parametrize("query,code", [
    ("ITA", "ITA"), ("ita", "ITA"), ("Italy", "ITA"), ("  italy ", "ITA"),
    ("FRA", "FRA"), ("France", "FRA"),          # ISO_A3 is -99 for France in Natural Earth
    ("NOR", "NOR"), ("Norway", "NOR"),
    ("United States", "USA"), ("USA", "USA"), ("America", "USA"), ("US", "USA"),
    ("United Kingdom", "GBR"), ("UK", "GBR"), ("Britain", "GBR"),
    ("Holland", "NLD"), ("the Netherlands", "NLD"),
    ("South Korea", "KOR"), ("North Korea", "PRK"),
    ("Switzerland", "CHE"), ("Fiji", "FJI"),
])
def test_names_and_codes_resolve(atlas, query, code):
    assert atlas.resolve(query).code == code


def test_an_ambiguous_name_lists_candidates(atlas):
    with pytest.raises(InvalidInput) as err:
        atlas.resolve("Korea")
    assert set(err.value.details["candidates"]) == {"KOR", "PRK"}
    assert "KOR" in err.value.hint and "PRK" in err.value.hint


def test_a_typo_suggests_close_names(atlas):
    with pytest.raises(InvalidInput) as err:
        atlas.resolve("Itally")
    assert "Italy" in err.value.details["suggestions"]


def test_a_place_carries_lon_lat_rings_and_a_label(atlas):
    place = atlas.resolve("ITA")
    lon, lat = place.polygons[0][0][0]
    assert -180 <= lon <= 180 and -90 <= lat <= 90
    assert place.name == "Italy" and 6 < place.label[0] < 19 and 36 < place.label[1] < 48


def test_south_africa_keeps_the_lesotho_hole(atlas):
    zaf = atlas.resolve("ZAF")
    assert any(len(poly) > 1 for poly in zaf.polygons)  # outer ring + hole


def test_search_returns_ranked_candidates(atlas):
    found = atlas.search("korea")
    assert {f["code"] for f in found} >= {"KOR", "PRK"} and all(f["ambiguous"] for f in found[:2])
    assert atlas.search("ital")[0]["code"] == "ITA"


def test_ensure_downloads_once_and_reuses(tmp_path, monkeypatch):
    from reelmachine.recipes.mapreel import geodata

    calls = []

    def fake_download(url, dest):
        calls.append(url)
        dest.write_bytes(b"{}")

    monkeypatch.setattr(geodata, "_download", fake_download)
    first = geodata.ensure(tmp_path, "countries")
    second = geodata.ensure(tmp_path, "countries")
    assert first == second and first.read_bytes() == b"{}" and len(calls) == 1


def test_ensure_explains_a_network_failure(tmp_path, monkeypatch):
    import httpx

    from reelmachine.core.errors import ProviderUnavailable
    from reelmachine.recipes.mapreel import geodata

    def broken(url, dest):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(geodata, "_download", broken)
    with pytest.raises(ProviderUnavailable) as err:
        geodata.ensure(tmp_path, "world")
    assert "once" in err.value.hint and not (tmp_path / geodata.FILES["world"][0]).exists()
```

- [ ] **Step 3: Write `geodata.py`**

```python
# reelmachine/recipes/mapreel/geodata.py
"""Map data, fetched once into the shared cache: Natural Earth borders, NASA Blue Marble."""

from __future__ import annotations

from pathlib import Path

import httpx

from ...config import Settings
from ...core.assets import Licence
from ...core.errors import ProviderUnavailable

NE_VERSION = "v5.1.2"
_NE = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{NE_VERSION}/geojson"
_BM = "https://eoimages.gsfc.nasa.gov/images/imagerecords/73000/73909"
USER_AGENT = "reel-machine/mapreel"

#: key -> (file name in the cache, URL)
FILES: dict[str, tuple[str, str]] = {
    "countries": ("ne_10m_admin_0_countries.geojson", f"{_NE}/ne_10m_admin_0_countries.geojson"),
    "world": ("blue-marble-5400.jpg", f"{_BM}/world.topo.bathy.200412.3x5400x2700.jpg"),
    "detail": ("blue-marble-21600.jpg", f"{_BM}/world.topo.bathy.200412.3x21600x10800.jpg"),
}

NATURAL_EARTH = Licence(name="Public domain", url="https://www.naturalearthdata.com/about/terms-of-use/",
                        attribution=f"Made with Natural Earth ({NE_VERSION})")
BLUE_MARBLE = Licence(name="Public domain", url="https://visibleearth.nasa.gov/image-use-policy",
                      attribution="NASA Earth Observatory, Blue Marble: Next Generation (Reto Stöckli)")
LICENCES: dict[str, Licence] = {"countries": NATURAL_EARTH, "world": BLUE_MARBLE, "detail": BLUE_MARBLE}


def geo_cache(settings: Settings) -> Path:
    """Public data, so shared by every caller."""
    return (settings.shared_cache or settings.cache_dir) / "mapreel" / "geo"


def _download(url: str, dest: Path) -> None:
    with httpx.Client(timeout=300, follow_redirects=True) as client:
        with client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as response:
            response.raise_for_status()
            with dest.open("wb") as handle:
                for chunk in response.iter_bytes(1 << 20):
                    handle.write(chunk)


def ensure(root: Path, key: str) -> Path:
    name, url = FILES[key]
    path = Path(root) / name
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(name + ".partial")
    try:
        _download(url, partial)
    except (httpx.HTTPError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise ProviderUnavailable(
            f"could not fetch the map data {name!r}: {exc}",
            hint=f"check the network; it is fetched once into {path.parent}",
            details={"url": url},
        ) from exc
    partial.replace(path)
    return path
```

- [ ] **Step 4: Write `places.py`**

```python
# reelmachine/recipes/mapreel/places.py
"""Countries from Natural Earth, found by code or by any of their names."""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...config import Settings
from ...core.errors import InvalidInput
from . import geodata

Ring = tuple[tuple[float, float], ...]

#: names people use that Natural Earth does not carry (normalised key -> codes)
ALIASES: dict[str, tuple[str, ...]] = {
    "holland": ("NLD",), "uk": ("GBR",), "britain": ("GBR",), "greatbritain": ("GBR",),
    "england": ("GBR",), "scotland": ("GBR",), "wales": ("GBR",),
    "us": ("USA",), "usa": ("USA",), "america": ("USA",), "unitedstates": ("USA",),
    "korea": ("KOR", "PRK"), "congo": ("COD", "COG"), "turkiye": ("TUR",), "burma": ("MMR",),
    "ivorycoast": ("CIV",), "uae": ("ARE",), "czechrepublic": ("CZE",), "russia": ("RUS",),
    "vatican": ("VAT",), "southkorea": ("KOR",), "northkorea": ("PRK",),
}
_NAME_FIELDS = ("NAME", "NAME_LONG", "NAME_EN", "ADMIN", "FORMAL_EN", "BRK_NAME", "ABBREV", "NAME_ALT")


def norm(text: str) -> str:
    """'  The Netherlands ' -> 'netherlands'; accents and punctuation dropped."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().casefold().strip()
    text = re.sub(r"^the\s+", "", text)
    return re.sub(r"[^a-z0-9]", "", text)


@dataclass(frozen=True)
class Place:
    code: str
    name: str
    polygons: tuple[tuple[Ring, ...], ...]
    label: tuple[float, float]
    level: str = "country"


def _code(props: dict[str, Any]) -> str:
    code = props.get("ISO_A3_EH") or "-99"
    return props["ADM0_A3"] if code == "-99" else code


def _rings(geometry: dict[str, Any]) -> tuple[tuple[Ring, ...], ...]:
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    return tuple(tuple(tuple((float(x), float(y)) for x, y, *_ in ring) for ring in poly) for poly in polys)


class Atlas:
    def __init__(self, places: dict[str, Place], keys: dict[str, set[str]]) -> None:
        self.places = places
        self.keys = keys  # normalised name/code -> codes

    @staticmethod
    def load(path: Path) -> "Atlas":
        path = Path(path)
        return _load(str(path), path.stat().st_mtime_ns)

    def resolve(self, query: str) -> Place:
        key = norm(query)
        raw = (query or "").strip()
        if raw.upper() in self.places:  # an exact code wins
            return self.places[raw.upper()]
        codes = sorted(self.keys.get(key, set()))
        if len(codes) == 1:
            return self.places[codes[0]]
        if len(codes) > 1:
            names = ", ".join(f"{c} ({self.places[c].name})" for c in codes)
            raise InvalidInput(f"{query!r} could mean more than one country", hint=f"use one of: {names}",
                               details={"query": query, "candidates": codes})
        names = sorted({p.name for p in self.places.values()})
        close = difflib.get_close_matches(query.strip().title(), names, n=5, cutoff=0.6)
        raise InvalidInput(f"no country called {query!r}",
                           hint=f"did you mean {', '.join(close)}?" if close else "use an ISO code like 'ITA' (find_places)",
                           details={"query": query, "suggestions": close})

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        key = norm(query)
        scored: dict[str, float] = {}
        for name, codes in self.keys.items():
            if not key:
                break
            if name == key:
                score = 3.0
            elif name.startswith(key):
                score = 2.0
            elif key in name:
                score = 1.0
            else:
                ratio = difflib.SequenceMatcher(None, key, name).ratio()
                score = ratio if ratio >= 0.75 else 0.0
            for code in codes:
                if score and code in self.places:
                    scored[code] = max(scored.get(code, 0.0), score)
        exact = self.keys.get(key, set())
        ranked = sorted(scored, key=lambda c: (-scored[c], self.places[c].name))[:limit]
        return [{"code": c, "name": self.places[c].name, "level": "country",
                 "ambiguous": len(exact) > 1 and c in exact} for c in ranked]


@lru_cache(maxsize=4)
def _load(path: str, _mtime: int) -> Atlas:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    places: dict[str, Place] = {}
    keys: dict[str, set[str]] = {}
    for feature in data["features"]:
        props, geometry = feature["properties"], feature.get("geometry")
        if not geometry:
            continue
        code = _code(props)
        label = (float(props.get("LABEL_X") or 0.0), float(props.get("LABEL_Y") or 0.0))
        places[code] = Place(code=code, name=props.get("NAME") or code, polygons=_rings(geometry), label=label)
        for field in ("ISO_A3_EH", "ADM0_A3", "ISO_A2_EH"):
            value = props.get(field)
            if value and value != "-99":
                keys.setdefault(norm(value), set()).add(code)
        for field in _NAME_FIELDS:
            if props.get(field):
                keys.setdefault(norm(props[field]), set()).add(code)
    for alias, codes in ALIASES.items():  # aliases replace whatever a short code collided with
        present = {c for c in codes if c in places}
        if present:
            keys[alias] = present
    return Atlas(places, keys)


def atlas_for(settings: Settings) -> Atlas:
    return Atlas.load(geodata.ensure(geodata.geo_cache(settings), "countries"))
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_mapreel_places.py -v`
Expected: all PASS. If `"US"` resolves to something else, check that `ALIASES` overwrites the key (it must, since `ISO_A2_EH` "US" and alias "us" both map to USA anyway). If `("Fiji", "FJI")` fails, confirm FJI is in the fixture.

- [ ] **Step 6: Commit**

```bash
git add reelmachine/recipes/mapreel/geodata.py reelmachine/recipes/mapreel/places.py reelmachine/recipes/mapreel/geometry.py tools/mapreel_fixture.py tests/fixtures/mapreel tests/test_mapreel_places.py
git commit -m "mapreel: Natural Earth borders and Blue Marble fetched once; places found by code, name or alias

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Geometry — Mercator, main landmass, framing, item anchors

**Files:**
- Modify: `reelmachine/recipes/mapreel/geometry.py` (append to the `simplify` written in Task 2)
- Test: `tests/test_mapreel_geometry.py`

**Interfaces:**
- Consumes: `places.Place`, `places.Atlas`.
- Produces:
  - `WIDTH, HEIGHT = 1080, 1920`; `MAX_LAT = 85.05112878`.
  - `merc(lon: np.ndarray|float, lat) -> (x, y)`; `lat_of(y) -> lat` (inverse).
  - `Shape` dataclass: `rings: list[np.ndarray]` (each (n,2) Mercator, unwrapped), `main: tuple[float, float, float, float]` bbox (x0, y0, x1, y1) of the main landmass cluster, `bbox` of all rings.
  - `shape_of(place: Place) -> Shape`.
  - `Frame` dataclass: `x: float, y: float, s: float` (centre in Mercator units; `s` = screen px per Mercator unit). `Frame.to_dict() -> {"x","y","s"}`.
  - `frame_for(bbox, *, zoom: float = 1.0) -> Frame` (fits bbox into the safe area; clamps `MIN_S <= s <= MAX_S`).
  - `to_screen(frame, pts: np.ndarray) -> np.ndarray`.
  - `visible_bbox(frame, margin: float = 0.0) -> (x0, y0, x1, y1)`.
  - `item_anchors(rings_px: list[np.ndarray], n: int) -> list[tuple[float, float]]`.
  - `simplify_for(frame, ring) -> np.ndarray` (simplify at ~1.5 screen px).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mapreel_geometry.py
from pathlib import Path

import numpy as np
import pytest

from reelmachine.recipes.mapreel import geometry as g
from reelmachine.recipes.mapreel.places import Atlas

ATLAS = Atlas.load(Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson")


def _lonlat(bbox):
    (x0, y0, x1, y1) = bbox
    return (x0 * 360 - 180, g.lat_of(y1), x1 * 360 - 180, g.lat_of(y0))


def test_mercator_round_trips_and_is_y_down():
    x, y = g.merc(np.array([12.5]), np.array([41.9]))
    assert 0.5 < x[0] < 0.6 and 0.3 < y[0] < 0.5
    assert g.lat_of(y[0]) == pytest.approx(41.9, abs=1e-6)
    assert g.merc(0.0, 60.0)[1] < g.merc(0.0, 0.0)[1]  # north is up (smaller y)


def test_france_frames_the_mainland_not_french_guiana():
    lon0, lat0, lon1, lat1 = _lonlat(g.shape_of(ATLAS.resolve("FRA")).main)
    assert lon0 > -6 and lat0 > 40  # Guiana sits near -54°, 4°N


def test_usa_frames_the_lower_48():
    lon0, lat0, lon1, lat1 = _lonlat(g.shape_of(ATLAS.resolve("USA")).main)
    assert lon0 > -130 and lat1 < 50.5  # no Alaska, no Hawaii


def test_japan_keeps_its_main_islands_together():
    lon0, lat0, lon1, lat1 = _lonlat(g.shape_of(ATLAS.resolve("JPN")).main)
    assert lat1 > 44 and lat0 < 32  # Hokkaido and Kyushu both in


def test_russia_and_fiji_are_unwrapped_across_the_antimeridian():
    for code in ("RUS", "FJI"):
        shape = g.shape_of(ATLAS.resolve(code))
        xs = np.concatenate([r[:, 0] for r in shape.rings])
        assert xs.max() - xs.min() < 0.6, code  # no part flung to the far side of the world


def test_a_frame_fits_the_country_inside_the_safe_area():
    shape = g.shape_of(ATLAS.resolve("ITA"))
    frame = g.frame_for(shape.main)
    pts = g.to_screen(frame, np.concatenate(shape.rings))
    x0, y0, x1, y1 = shape.main
    corners = g.to_screen(frame, np.array([[x0, y0], [x1, y1]]))
    assert corners[:, 0].min() >= 0.08 * g.WIDTH - 1 and corners[:, 0].max() <= 0.92 * g.WIDTH + 1
    assert corners[:, 1].min() >= 0.10 * g.HEIGHT - 1 and corners[:, 1].max() <= 0.80 * g.HEIGHT + 1
    assert pts.shape[1] == 2


def test_tiny_and_huge_countries_clamp_the_zoom():
    sgp = g.frame_for(g.shape_of(ATLAS.resolve("SGP")).main)
    rus = g.frame_for(g.shape_of(ATLAS.resolve("RUS")).main)
    assert sgp.s == pytest.approx(g.MAX_S) and g.MIN_S <= rus.s < g.MAX_S
    assert g.frame_for((0.0, 0.0, 1.0, 1.0)).s == pytest.approx(g.MIN_S)


def test_zoom_scales_the_frame():
    shape = g.shape_of(ATLAS.resolve("ITA"))
    assert g.frame_for(shape.main, zoom=2.0).s == pytest.approx(2 * g.frame_for(shape.main).s)


def test_item_anchors_fall_inside_the_country_and_spread_out():
    shape = g.shape_of(ATLAS.resolve("ITA"))
    frame = g.frame_for(shape.main)
    rings = [g.to_screen(frame, r) for r in shape.rings]
    anchors = g.item_anchors(rings, 4)
    assert len(anchors) == 4
    assert all(g.inside(rings, x, y) for x, y in anchors)
    gaps = [np.hypot(a[0] - b[0], a[1] - b[1]) for i, a in enumerate(anchors) for b in anchors[i + 1:]]
    assert min(gaps) > 150


def test_item_anchors_fall_back_to_a_column_for_a_sliver():
    sliver = [np.array([[500.0, 900.0], [520.0, 900.0], [520.0, 920.0], [500.0, 920.0]])]
    anchors = g.item_anchors(sliver, 3)
    assert len(anchors) == 3 and len({round(y) for _, y in anchors}) == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mapreel_geometry.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'merc'`

- [ ] **Step 3: Implement**

Append to `reelmachine/recipes/mapreel/geometry.py`:

```python
from dataclasses import dataclass, field

from .places import Place

WIDTH, HEIGHT = 1080, 1920
MAX_LAT = 85.05112878
#: px per Mercator unit: the whole world never narrower than the screen; never closer than
#: 6° of longitude across it (the 21600-px texture would upscale past 3×)
MIN_S = float(WIDTH)
MAX_S = WIDTH / (6.0 / 360.0)
#: where a framed place sits on screen: clear of the legend at the top and captions below
SAFE = (0.08, 0.10, 0.92, 0.80)  # x0, y0, x1, y1 as fractions of the screen


def merc(lon, lat):
    lon = np.asarray(lon, dtype=float)
    lat = np.clip(np.asarray(lat, dtype=float), -MAX_LAT, MAX_LAT)
    x = (lon + 180.0) / 360.0
    y = 0.5 - np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) / (2 * np.pi)
    return x, y


def lat_of(y):
    return np.degrees(2 * np.arctan(np.exp((0.5 - np.asarray(y, dtype=float)) * 2 * np.pi)) - np.pi / 2)


def _area(ring: np.ndarray) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return float(abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 2)


def _bbox(rings: list[np.ndarray]) -> tuple[float, float, float, float]:
    pts = np.concatenate(rings)
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def _gap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return float(np.hypot(dx, dy))


@dataclass
class Shape:
    rings: list[np.ndarray]
    main: tuple[float, float, float, float]
    bbox: tuple[float, float, float, float]
    outers: list[np.ndarray] = field(default_factory=list)


def shape_of(place: Place) -> Shape:
    """Project to Mercator, bring parts across the antimeridian next to the main body,
    and find the main landmass cluster the camera frames."""
    polys = []
    for poly in place.polygons:
        rings = []
        for ring in poly:
            arr = np.asarray(ring, dtype=float)
            x, y = merc(arr[:, 0], arr[:, 1])
            rings.append(np.column_stack([x, y]))
        polys.append(rings)
    areas = [_area(p[0]) for p in polys]
    main = int(np.argmax(areas))
    cx = float(polys[main][0][:, 0].mean())
    for rings in polys:  # unwrap: a part more than half a world away moves by one world
        shift = round(cx - float(rings[0][:, 0].mean()))
        if shift:
            for ring in rings:
                ring[:, 0] += shift
    cluster = _bbox([polys[main][0]])
    order = sorted(range(len(polys)), key=lambda i: -areas[i])
    changed = True
    taken = {main}
    while changed:  # grow the cluster with parts close to it (relative to its own size)
        changed = False
        diag = float(np.hypot(cluster[2] - cluster[0], cluster[3] - cluster[1]))
        for i in order:
            if i in taken or areas[i] < areas[main] * 1e-4:
                continue
            box = _bbox([polys[i][0]])
            if _gap(cluster, box) <= 0.35 * diag:
                taken.add(i)
                cluster = (min(cluster[0], box[0]), min(cluster[1], box[1]),
                           max(cluster[2], box[2]), max(cluster[3], box[3]))
                changed = True
    all_rings = [ring for rings in polys for ring in rings]
    return Shape(rings=all_rings, main=cluster, bbox=_bbox(all_rings), outers=[p[0] for p in polys])


@dataclass(frozen=True)
class Frame:
    x: float
    y: float
    s: float

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "s": self.s}


def frame_for(bbox: tuple[float, float, float, float], *, zoom: float = 1.0) -> Frame:
    x0, y0, x1, y1 = bbox
    sx0, sy0, sx1, sy1 = SAFE
    w = max(x1 - x0, 1e-9)
    h = max(y1 - y0, 1e-9)
    s = min((sx1 - sx0) * WIDTH / w, (sy1 - sy0) * HEIGHT / h) * zoom
    s = float(min(MAX_S, max(MIN_S, s)))
    # centre the bbox on the safe area's centre, not the screen's
    cx_px, cy_px = (sx0 + sx1) / 2 * WIDTH, (sy0 + sy1) / 2 * HEIGHT
    x = (x0 + x1) / 2 - (cx_px - WIDTH / 2) / s
    y = (y0 + y1) / 2 - (cy_px - HEIGHT / 2) / s
    return Frame(float(x), float(y), s)


def to_screen(frame: Frame, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=float)
    return np.column_stack([(pts[:, 0] - frame.x) * frame.s + WIDTH / 2,
                            (pts[:, 1] - frame.y) * frame.s + HEIGHT / 2])


def visible_bbox(frame: Frame, margin: float = 0.0) -> tuple[float, float, float, float]:
    hw, hh = WIDTH / 2 / frame.s * (1 + margin), HEIGHT / 2 / frame.s * (1 + margin)
    return frame.x - hw, frame.y - hh, frame.x + hw, frame.y + hh


def simplify_for(frame: Frame, ring: np.ndarray) -> np.ndarray:
    return simplify(ring, 1.5 / frame.s)


def inside(rings_px: list[np.ndarray], x: float | np.ndarray, y: float | np.ndarray) -> np.ndarray | bool:
    """Even-odd point-in-polygon over every ring (holes subtract)."""
    xs, ys = np.atleast_1d(np.asarray(x, dtype=float)), np.atleast_1d(np.asarray(y, dtype=float))
    hit = np.zeros(xs.shape, dtype=bool)
    for ring in rings_px:
        ax, ay = ring[:, 0], ring[:, 1]
        bx, by = np.roll(ax, -1), np.roll(ay, -1)
        for i in range(len(ring)):
            cond = (ay[i] > ys) != (by[i] > ys)
            with np.errstate(divide="ignore", invalid="ignore"):
                cross = ax[i] + (ys - ay[i]) * (bx[i] - ax[i]) / (by[i] - ay[i])
            hit ^= cond & (xs < cross)
    return hit if np.ndim(x) else bool(hit[0])


def item_anchors(rings_px: list[np.ndarray], n: int, *, step: float = 24.0) -> list[tuple[float, float]]:
    """`n` points inside the place and inside the safe area, spread as far apart as possible
    (greedy farthest-point). A place too small to hold them gets a column at its centre."""
    sx0, sy0, sx1, sy1 = SAFE
    gx, gy = np.meshgrid(np.arange(sx0 * WIDTH + 140, sx1 * WIDTH - 140, step),
                         np.arange(sy0 * HEIGHT + 100, sy1 * HEIGHT - 100, step))
    gx, gy = gx.ravel(), gy.ravel()
    mask = inside(rings_px, gx, gy)
    pts = np.column_stack([gx[mask], gy[mask]])
    if len(pts) < n * 4:
        allpts = np.concatenate(rings_px)
        cx = float(np.clip(allpts[:, 0].mean(), sx0 * WIDTH + 180, sx1 * WIDTH - 180))
        cy = float(np.clip(allpts[:, 1].mean(), sy0 * HEIGHT + 200, sy1 * HEIGHT - 200))
        spacing = 220.0
        top = cy - spacing * (n - 1) / 2
        return [(cx, top + i * spacing) for i in range(n)]
    centre = pts.mean(axis=0)
    first = int(np.argmin(np.hypot(*(pts - centre).T)))
    chosen = [pts[first]]
    dist = np.hypot(*(pts - pts[first]).T)
    while len(chosen) < n:
        k = int(np.argmax(dist))
        chosen.append(pts[k])
        dist = np.minimum(dist, np.hypot(*(pts - pts[k]).T))
    chosen.sort(key=lambda p: (p[1], p[0]))  # read top to bottom, as they are spoken
    return [(float(x), float(y)) for x, y in chosen]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mapreel_geometry.py -v`
Expected: all PASS. Tuning knobs if a framing test fails: the cluster threshold `0.35 * diag` (USA must exclude Alaska at gap ≈ diag·0.5; Japan must include Hokkaido at gap ≈ 0). Change only that constant, and rerun the whole file.

- [ ] **Step 5: Commit**

```bash
git add reelmachine/recipes/mapreel/geometry.py tests/test_mapreel_geometry.py
git commit -m "mapreel: Mercator geometry — the camera frames the main landmass, parts across 180° stay together, items spread inside the country

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Basemap layers and the palette

**Files:**
- Create: `reelmachine/recipes/mapreel/basemap.py`, `reelmachine/recipes/mapreel/palette.py`
- Test: `tests/test_mapreel_basemap.py`

**Interfaces:**
- Consumes: `geometry.lat_of`, `geometry.MAX_LAT`, `geodata.ensure`, `Settings.ffmpeg`, `reelmachine.ffmpeg.run`, `reelmachine.ffmpeg.probe`.
- Produces:
  - `basemap.world_layer(settings, root: Path) -> Path` — 2048×2048 Mercator JPEG of the whole world (`bbox (0, 0, 1, 1)`), cached at `root / "layers" / "world-2048.jpg"`.
  - `basemap.detail_layer(settings, root: Path, bbox) -> tuple[Path, tuple[float, float, float, float]]` — a Mercator crop of the detail texture covering `bbox` (clamped to y ∈ [0, 1]; x may be < 0 or > 1 for unwrapped shapes), ≤ 2160 px on its long side; cached by rounded bbox; returns (path, actual bbox).
  - `palette.assign(groups: list[list[str]], fixed: dict[str, str] | None = None) -> dict[str, str]` — code → `#rrggbb`; codes in the same group and in consecutive groups never share a colour family.
  - `palette.PALETTE: list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mapreel_basemap.py
import dataclasses
import shutil
import subprocess

import numpy as np
import pytest

from reelmachine.config import get_settings
from reelmachine.recipes.mapreel import basemap, geodata, palette

pytestmark = pytest.mark.slow


def _settings(tmp_path):
    return dataclasses.replace(get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out")


def _seed_textures(settings, root):
    """Equirectangular stand-ins: left half red, right half blue; top row band green
    (the north pole), so the reprojection's orientation is checkable."""
    root.mkdir(parents=True, exist_ok=True)
    for key, (w, h) in {"world": (720, 360), "detail": (1440, 720)}.items():
        src = (f"color=red:size={w // 2}x{h}[l];color=blue:size={w // 2}x{h}[r];[l][r]hstack[m];"
               f"color=green:size={w}x{h // 18}[top];[m][top]overlay=0:0")
        subprocess.run([settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", src, "-frames:v", "1",
                        str(root / geodata.FILES[key][0])], check=True)


def _rgb(settings, path):
    out = subprocess.run([settings.ffmpeg, "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    w, h = (int(v) for v in subprocess.run([settings.ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True).stdout.strip().split(","))
    return np.frombuffer(out, dtype=np.uint8).reshape(h, w, 3)


def test_world_layer_is_square_mercator_with_west_red_and_east_blue(tmp_path):
    settings = _settings(tmp_path)
    root = tmp_path / "geo"
    _seed_textures(settings, root)
    img = _rgb(settings, basemap.world_layer(settings, root))
    assert img.shape[0] == img.shape[1] == 2048
    mid = img.shape[0] // 2
    assert img[mid, 100, 0] > 150 and img[mid, 100, 2] < 100      # west red
    assert img[mid, 1900, 2] > 150 and img[mid, 1900, 0] < 100    # east blue
    assert img[2, 1000, 1] > 100                                   # north edge green (latitudes ≥ 80°)


def test_detail_layer_crops_and_caches(tmp_path):
    settings = _settings(tmp_path)
    root = tmp_path / "geo"
    _seed_textures(settings, root)
    bbox = (0.40, 0.30, 0.55, 0.45)
    path, actual = basemap.detail_layer(settings, root, bbox)
    again, _ = basemap.detail_layer(settings, root, bbox)
    assert path == again and actual == pytest.approx(bbox)
    img = _rgb(settings, path)
    assert img.shape[1] <= 2160 and img.shape[0] / img.shape[1] == pytest.approx(1.0, abs=0.02)
    split = int((0.5 - 0.40) / 0.15 * img.shape[1])  # the red/blue border at lon 0
    assert img[img.shape[0] // 2, split - 10, 0] > 150 and img[img.shape[0] // 2, split + 10, 2] > 150


def test_detail_layer_wraps_across_the_antimeridian(tmp_path):
    settings = _settings(tmp_path)
    root = tmp_path / "geo"
    _seed_textures(settings, root)
    path, actual = basemap.detail_layer(settings, root, (0.95, 0.30, 1.05, 0.40))
    img = _rgb(settings, path)
    mid = img.shape[0] // 2
    assert img[mid, 5, 2] > 150 and img[mid, -5, 0] > 150  # east (blue) on the left, west (red) on the right


def test_palette_keeps_neighbours_and_cuts_distinct():
    colours = palette.assign([["USA"], ["GBR"], ["ITA", "FRA", "DEU"], ["FRA"]])
    assert len({colours["ITA"], colours["FRA"], colours["DEU"]}) == 3
    assert colours["USA"] != colours["GBR"]
    assert all(c.startswith("#") and len(c) == 7 for c in colours.values())


def test_palette_respects_fixed_colours():
    assert palette.assign([["ITA"]], fixed={"ITA": "#123456"})["ITA"] == "#123456"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mapreel_basemap.py -v`
Expected: FAIL with `ImportError: cannot import name 'basemap'`

- [ ] **Step 3: Implement the palette**

```python
# reelmachine/recipes/mapreel/palette.py
"""Vivid fills that read on a satellite map: no two alike on screen or across a cut."""

from __future__ import annotations

#: the reference's purple, red, blue, orange, magenta, green … then more
PALETTE = ["#8a2be2", "#e0201b", "#3333ff", "#f28c0f", "#e01ec8", "#35b52a",
           "#00b3c7", "#ffcc00", "#ff5a7a", "#1e90ff", "#7cdf00", "#b8005c"]


def assign(groups: list[list[str]], fixed: dict[str, str] | None = None) -> dict[str, str]:
    """`groups` are the codes on screen together, in reel order. A code keeps its colour for
    the whole reel; a new code takes the first palette colour not used by its own group or
    the group before it, cycling the start so consecutive reels of one country vary."""
    colours: dict[str, str] = dict(fixed or {})
    cursor = 0
    previous: set[str] = set()
    for group in groups:
        taken = {colours[c] for c in group if c in colours} | previous
        for code in group:
            if code in colours:
                continue
            for step in range(len(PALETTE)):
                candidate = PALETTE[(cursor + step) % len(PALETTE)]
                if candidate not in taken:
                    break
            colours[code] = candidate
            taken.add(candidate)
            cursor = (PALETTE.index(candidate) + 1) % len(PALETTE)
        previous = {colours[c] for c in group}
    return colours
```

- [ ] **Step 4: Implement the basemap**

```python
# reelmachine/recipes/mapreel/basemap.py
"""Satellite layers in Mercator: one low-res world for flights, one sharp crop per segment.

The source texture is equirectangular, so Mercator is a row remap: each output row is one
latitude. ffmpeg crops and scales the columns; numpy picks the rows."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np

from ... import ffmpeg
from ...config import Settings
from . import geodata
from .geometry import MAX_LAT, lat_of

WORLD_PX = 2048
DETAIL_MAX_PX = 2160


def _raw(settings: Settings, src: Path, *, x: int, y: int, w: int, h: int, out_w: int, out: Path) -> np.ndarray:
    ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(src),
                "-vf", f"crop={w}:{h}:{x}:{y},scale={out_w}:{h}:flags=area",
                "-f", "rawvideo", "-pix_fmt", "rgb24", str(out)])
    data = np.fromfile(out, dtype=np.uint8).reshape(h, out_w, 3)
    out.unlink(missing_ok=True)
    return data


def _encode(settings: Settings, rgb: np.ndarray, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = dest.with_suffix(".rgb")
    np.ascontiguousarray(rgb).tofile(raw)
    h, w = rgb.shape[:2]
    partial = dest.with_name(dest.stem + ".partial.jpg")
    ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "rawvideo",
                "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-i", str(raw), "-q:v", "3", str(partial)])
    raw.unlink(missing_ok=True)
    partial.replace(dest)
    return dest


def _mercator(settings: Settings, src: Path, lon0: float, lon1: float, y0: float, y1: float,
              out_w: int, out_h: int, scratch: Path) -> np.ndarray:
    """Mercator image of [lon0, lon1] × [y0, y1]; lon may run past ±180 (wraps)."""
    info = ffmpeg.probe(src)
    sw, sh = info.width, info.height
    lat_top, lat_bot = float(lat_of(y0)), float(lat_of(y1))
    row0 = max(0, int(math.floor((90 - lat_top) / 180 * sh)))
    row1 = min(sh, int(math.ceil((90 - lat_bot) / 180 * sh)) + 1)
    pieces, span = [], lon1 - lon0
    lon = lon0
    while lon < lon1 - 1e-9:  # split at the antimeridian
        wrapped = ((lon + 180) % 360) - 180
        end = min(lon1, lon + (180 - wrapped))
        c0 = int(round((wrapped + 180) / 360 * sw))
        c1 = max(c0 + 1, int(round((wrapped + (end - lon) + 180) / 360 * sw)))
        width_px = max(1, int(round(out_w * (end - lon) / span)))
        pieces.append(_raw(settings, src, x=c0, y=row0, w=min(c1, sw) - c0, h=row1 - row0,
                           out_w=width_px, out=scratch / f"piece-{len(pieces)}.rgb"))
        lon = end
    strip = np.concatenate(pieces, axis=1)
    if strip.shape[1] != out_w:  # rounding across pieces
        idx = np.linspace(0, strip.shape[1] - 1, out_w).round().astype(int)
        strip = strip[:, idx]
    ys = y0 + (np.arange(out_h) + 0.5) / out_h * (y1 - y0)
    src_rows = (90 - lat_of(ys)) / 180 * sh - row0 - 0.5
    lo = np.clip(np.floor(src_rows).astype(int), 0, strip.shape[0] - 1)
    hi = np.clip(lo + 1, 0, strip.shape[0] - 1)
    t = np.clip(src_rows - lo, 0, 1)[:, None, None]
    return (strip[lo] * (1 - t) + strip[hi] * t).astype(np.uint8)


def world_layer(settings: Settings, root: Path) -> Path:
    dest = Path(root) / "layers" / f"world-{WORLD_PX}.jpg"
    if dest.is_file():
        return dest
    src = geodata.ensure(root, "world")
    scratch = dest.parent / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    rgb = _mercator(settings, src, -180.0, 180.0, 0.0, 1.0, WORLD_PX, WORLD_PX, scratch)
    return _encode(settings, rgb, dest)


def detail_layer(settings: Settings, root: Path,
                 bbox: tuple[float, float, float, float]) -> tuple[Path, tuple[float, float, float, float]]:
    x0, y0, x1, y1 = bbox
    y0, y1 = max(0.0, y0), min(1.0, y1)
    key = hashlib.sha256(repr(tuple(round(v, 5) for v in (x0, y0, x1, y1))).encode()).hexdigest()[:16]
    dest = Path(root) / "layers" / f"detail-{key}.jpg"
    actual = (x0, y0, x1, y1)
    if dest.is_file():
        return dest, actual
    src = geodata.ensure(root, "detail")
    info = ffmpeg.probe(src)
    native_w = (x1 - x0) * info.width  # source px across the crop
    long_side = DETAIL_MAX_PX
    aspect = (y1 - y0) / (x1 - x0)
    out_w = int(min(native_w, long_side if aspect <= 1 else long_side / aspect))
    out_w = max(64, out_w)
    out_h = max(64, int(round(out_w * aspect)))
    scratch = dest.parent / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    rgb = _mercator(settings, src, x0 * 360 - 180, x1 * 360 - 180, y0, y1, out_w, out_h, scratch)
    return _encode(settings, rgb, dest), actual
```

Check `ffmpeg.probe(...)` returns an object with `.width` / `.height` (it does for video streams; for a JPEG ffprobe reports a video stream). If it does not, read them with `ffprobe -show_entries stream=width,height` as in the test helper.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_mapreel_basemap.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add reelmachine/recipes/mapreel/basemap.py reelmachine/recipes/mapreel/palette.py tests/test_mapreel_basemap.py
git commit -m "mapreel: satellite layers reprojected to Mercator — a world layer for flights, a sharp crop per segment — and a vivid palette

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Spoken-name matching and pop times

**Files:**
- Create: `reelmachine/recipes/mapreel/timing.py`
- Test: `tests/test_mapreel_timing.py`

**Interfaces:**
- Consumes: `reelmachine.core.timeline.WordSpan` (`text`, `start_ms`, `end_ms`).
- Produces:
  - `FLY_MS = 900`, `POP_GAP_MS = 250`, `MIN_SEGMENT_MS = 2200`.
  - `tokens(text: str) -> list[str]` (normalised: casefold, accents stripped, `&`→`and`, split on non-alphanumerics, apostrophes dropped so "Levi's" → `["levis"]`).
  - `match(phrases: list[str], words: list[WordSpan]) -> list[int | None]` — start ms of each phrase's first word, in order of speech; `None` when not spoken.
  - `pop_times(spoken: list[int | None], *, overrides: list[int | None], speech_start_ms: int, speech_end_ms: int) -> tuple[list[int], list[int]]` — final ms (segment-relative) for each entry and the indexes that had to be spread (unmatched, no override).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mapreel_timing.py
from reelmachine.core.timeline import WordSpan
from reelmachine.recipes.mapreel.timing import FLY_MS, POP_GAP_MS, match, pop_times, tokens


def _words(text, step=300):
    return [WordSpan(text=w, start_ms=i * step, end_ms=i * step + 250) for i, w in enumerate(text.split())]


def test_tokens_normalise_brand_spellings():
    assert tokens("Levi's") == ["levis"]
    assert tokens("Dolce & Gabbana") == ["dolce", "and", "gabbana"]
    assert tokens("Fjällräven") == ["fjallraven"]
    assert tokens("H&M") == ["h", "and", "m"]


def test_items_match_their_spoken_words_in_order():
    words = _words("Italy has Gucci, Prada, Dolce and Gabbana, and Valentino.")
    assert match(["Gucci", "Prada", "Dolce & Gabbana", "Valentino"], words) == [600, 900, 1200, 2100]


def test_a_name_spoken_differently_falls_back_to_none():
    words = _words("Sweden has H and M and Acne Studios.")
    assert match(["H&M", "Acne Studios", "Fjallraven"], words) == [600, 1500, None]


def test_a_repeated_word_matches_the_next_occurrence():
    words = _words("Canada has Canada Goose and Roots.")
    assert match(["Canada Goose", "Roots"], words) == [600, 1500]


def test_an_out_of_order_name_is_still_found():
    words = _words("France has Dior and Chanel.")
    assert match(["Chanel", "Dior"], words) == [1200, 600]


def test_pop_times_wait_for_the_flight_and_keep_a_gap():
    times, spread = pop_times([100, 120, None, 3000], overrides=[None, None, None, None],
                              speech_start_ms=250, speech_end_ms=3500)
    assert times[0] >= FLY_MS - 100 and times[1] - times[0] >= POP_GAP_MS
    assert spread == [2] and times[1] < times[2] < times[3]


def test_overrides_win():
    times, spread = pop_times([500, 900], overrides=[2000, None], speech_start_ms=0, speech_end_ms=3000)
    assert times[0] == 2000 and spread == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mapreel_timing.py -v`
Expected: FAIL with `ModuleNotFoundError: ... mapreel.timing`

- [ ] **Step 3: Implement**

```python
# reelmachine/recipes/mapreel/timing.py
"""Things pop when they are said: names are matched against the voice's word timings."""

from __future__ import annotations

import re
import unicodedata
from typing import Sequence

from ...core.timeline import WordSpan

FLY_MS = 900          # the camera's flight at the start of a segment
POP_GAP_MS = 250      # never two pops closer than this
MIN_SEGMENT_MS = 2200


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().casefold()
    text = text.replace("&", " and ").replace("'", "").replace("’", "")
    return [t for t in re.split(r"[^a-z0-9]+", text) if t]


def _stream(words: Sequence[WordSpan]) -> tuple[list[str], list[int]]:
    flat, owner = [], []
    for index, word in enumerate(words):
        for tok in tokens(word.text):
            flat.append(tok)
            owner.append(index)
    return flat, owner


def _find(flat: list[str], phrase: list[str], start: int) -> int | None:
    k = len(phrase)
    for i in range(start, len(flat) - k + 1):
        if flat[i:i + k] == phrase:
            return i
    return None


def match(phrases: Sequence[str], words: Sequence[WordSpan]) -> list[int | None]:
    flat, owner = _stream(words)
    out: list[int | None] = []
    cursor = 0
    for phrase in phrases:
        toks = tokens(phrase)
        if not toks:
            out.append(None)
            continue
        hit = _find(flat, toks, cursor)
        if hit is None:
            hit = _find(flat, toks, 0)  # said earlier than listed
        else:
            cursor = hit + len(toks)
        out.append(None if hit is None else words[owner[hit]].start_ms)
    return out


def pop_times(spoken: Sequence[int | None], *, overrides: Sequence[int | None],
              speech_start_ms: int, speech_end_ms: int) -> tuple[list[int], list[int]]:
    """Segment-relative pop times. Spoken times are offset by `speech_start_ms`; an override
    is already segment-relative. Unmatched entries spread across the gaps; nothing pops
    before the camera lands; pops keep `POP_GAP_MS` apart."""
    n = len(spoken)
    raw: list[int | None] = []
    for said, forced in zip(spoken, overrides):
        raw.append(forced if forced is not None else (None if said is None else speech_start_ms + said))
    spread = [i for i, (said, forced) in enumerate(zip(spoken, overrides)) if said is None and forced is None]
    floor = FLY_MS - 100
    for i in spread:  # interpolate between known neighbours, else across the speech
        prev = next((raw[j] for j in range(i - 1, -1, -1) if raw[j] is not None), floor)
        nxt = next((raw[j] for j in range(i + 1, n) if raw[j] is not None), speech_end_ms)
        raw[i] = int(prev + (nxt - prev) / 2)
    times: list[int] = []
    last = floor - POP_GAP_MS
    for value in raw:
        t = max(int(value), floor, last + POP_GAP_MS)
        times.append(t)
        last = t
    return times, spread
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mapreel_timing.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add reelmachine/recipes/mapreel/timing.py tests/test_mapreel_timing.py
git commit -m "mapreel: items pop on the word that names them — brand spellings, ampersands and repeats matched

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The canvas runtime

**Files:**
- Create: `reelmachine/recipes/mapreel/runtime/pure.mjs`, `runtime/draw.mjs`, `runtime/main.mjs`, `runtime/tests/pure.test.mjs`
- Modify: `.github/workflows/ci.yml` (add the runtime tests next to explainer3d's at line ~79), `tests/test_mapreel_compose.py` (created in Task 7; the Node test runner hook goes there)

**Interfaces:**
- Consumes: the segment document written by Task 7 (`compose.segment_document`). Its shape, which this task defines and Task 7 must produce exactly:

```json
{
  "width": 1080, "height": 1920, "duration": 4.2,
  "camera": {"from": {"x": 0.5, "y": 0.4, "s": 1080}, "to": {"x": 0.53, "y": 0.36, "s": 9000},
             "flyMs": 900, "drift": 0.04},
  "layers": [{"url": "layers/world.jpg", "bbox": [0, 0, 1, 1]},
             {"url": "layers/detail.jpg", "bbox": [0.5, 0.3, 0.56, 0.42]}],
  "places": [{"code": "ITA", "name": "Italy", "colour": "#3333ff", "rings": [[[0.52, 0.33], [0.53, 0.34]]],
              "fillMs": 700, "label": {"x": 0.535, "y": 0.355}}],
  "previous": [{"colour": "#e0201b", "rings": [[[0.48, 0.30]]]}],
  "items": [{"name": "Gucci", "image": "images/gucci.png", "emoji": null, "atMs": 1100,
             "x": 520, "y": 700, "size": 300}],
  "words": [{"text": "Italy", "start": 0.25, "end": 0.6, "hot": false}],
  "legend": null
}
```

  Coordinates in `rings`, `label`, `camera` and `layers[].bbox` are Mercator units; `items[].x/y/size` are screen px at the landed camera; `words` times are seconds from segment start.
- Produces: `pure.mjs` exports `zoomPath(a, b)` → `(t∈[0,1]) => {x, y, s}` (van Wijk–Nuij, ρ = √2), `cameraAt(camera, ms)`, `easeInOut(t)`, `backOut(t)`, `popState(atMs, ms)` → `{visible, scale, alpha, blur}`, `fillAlpha(fillMs, ms)`, `activeWord(words, seconds)` → index or −1. `main.mjs` resolves `window.__mapReady` after images load and answers `hf-seek`.

- [ ] **Step 1: Write the failing Node tests**

```js
// reelmachine/recipes/mapreel/runtime/tests/pure.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { zoomPath, cameraAt, popState, fillAlpha, activeWord, backOut } from "../pure.mjs";

const A = { x: 0.2, y: 0.4, s: 1080 };
const B = { x: 0.55, y: 0.35, s: 20000 };

test("a zoom path starts and ends on its frames", () => {
  const path = zoomPath(A, B);
  for (const [t, f] of [[0, A], [1, B]]) {
    const p = path(t);
    assert.ok(Math.abs(p.x - f.x) < 1e-9 && Math.abs(p.y - f.y) < 1e-9 && Math.abs(p.s - f.s) / f.s < 1e-9);
  }
});

test("a long flight pulls back before it lands", () => {
  const path = zoomPath({ x: 0.1, y: 0.4, s: 20000 }, { x: 0.8, y: 0.4, s: 20000 });
  assert.ok(path(0.5).s < 20000 / 3);
});

test("the camera lands at flyMs and then drifts in slightly", () => {
  const cam = { from: A, to: B, flyMs: 900, drift: 0.04 };
  assert.deepEqual(cameraAt(cam, 0), A);
  const landed = cameraAt(cam, 900);
  assert.ok(Math.abs(landed.s - B.s) / B.s < 1e-6);
  assert.ok(cameraAt(cam, 4000).s > B.s && cameraAt(cam, 4000).s <= B.s * 1.04 + 1e-6);
});

test("a held camera does not move", () => {
  const cam = { from: B, to: B, flyMs: 0, drift: 0 };
  assert.deepEqual(cameraAt(cam, 0), B);
  assert.deepEqual(cameraAt(cam, 3000), B);
});

test("an item is hidden before it pops, overshoots, then settles", () => {
  assert.equal(popState(1000, 900).visible, false);
  const mid = popState(1000, 1150);
  assert.ok(mid.visible && mid.alpha > 0 && mid.blur > 0);
  const done = popState(1000, 1600);
  assert.equal(done.alpha, 1);
  assert.equal(done.blur, 0);
  assert.ok(Math.abs(done.scale - 1) < 1e-6);
  assert.ok(backOut(0.7) > 1); // the overshoot
});

test("fills fade in from their time", () => {
  assert.equal(fillAlpha(700, 600), 0);
  assert.ok(fillAlpha(700, 900) > 0 && fillAlpha(700, 900) < 1);
  assert.equal(fillAlpha(700, 2000), 1);
});

test("the active word is the one being said, held briefly after", () => {
  const words = [{ start: 0.2, end: 0.5 }, { start: 0.6, end: 0.9 }];
  assert.equal(activeWord(words, 0.1), -1);
  assert.equal(activeWord(words, 0.3), 0);
  assert.equal(activeWord(words, 0.55), 0);
  assert.equal(activeWord(words, 0.7), 1);
  assert.equal(activeWord(words, 2), -1);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test "reelmachine/recipes/mapreel/runtime/tests/*.test.mjs"`
Expected: FAIL with `Cannot find module '../pure.mjs'`

- [ ] **Step 3: Write `pure.mjs`**

```js
// reelmachine/recipes/mapreel/runtime/pure.mjs
// Pure timing and camera maths for a map segment: no DOM, so Node can test it.
// Frames are {x, y, s}: the Mercator point at screen centre and px per Mercator unit.

const RHO = Math.SQRT2;
const W = 1080;

export const clamp01 = (t) => Math.max(0, Math.min(1, t));
export const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
export const backOut = (t) => {
  const c1 = 1.70158, c3 = c1 + 1;
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
};

// van Wijk & Nuij, "Smooth and efficient zooming and panning" (the d3.interpolateZoom path)
export function zoomPath(a, b) {
  const w0 = W / a.s, w1 = W / b.s;
  const dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy, d1 = Math.sqrt(d2);
  if (d1 < 1e-12) {
    const S = Math.log(w1 / w0) / RHO;
    return (t) => ({ x: a.x + t * dx, y: a.y + t * dy, s: W / (w0 * Math.exp(RHO * t * S)) });
  }
  const b0 = (w1 * w1 - w0 * w0 + RHO ** 4 * d2) / (2 * w0 * RHO * RHO * d1);
  const b1 = (w1 * w1 - w0 * w0 - RHO ** 4 * d2) / (2 * w1 * RHO * RHO * d1);
  const r0 = Math.log(Math.sqrt(b0 * b0 + 1) - b0);
  const r1 = Math.log(Math.sqrt(b1 * b1 + 1) - b1);
  const S = (r1 - r0) / RHO;
  return (t) => {
    if (t >= 1) return { x: b.x, y: b.y, s: b.s };
    const s = t * S;
    const coshr0 = Math.cosh(r0);
    const u = (w0 / (RHO * RHO * d1)) * (coshr0 * Math.tanh(RHO * s + r0) - Math.sinh(r0));
    const w = (w0 * coshr0) / Math.cosh(RHO * s + r0);
    return { x: a.x + u * dx, y: a.y + u * dy, s: W / w };
  };
}

export function cameraAt(camera, ms) {
  const { from, to, flyMs, drift } = camera;
  if (!flyMs || ms >= flyMs) {
    const after = Math.max(0, ms - (flyMs || 0));
    const grow = 1 + drift * clamp01(after / 6000);
    return { x: to.x, y: to.y, s: to.s * grow };
  }
  if (ms <= 0) return { x: from.x, y: from.y, s: from.s };
  return zoomPath(from, to)(easeInOut(ms / flyMs));
}

const POP_MS = 350;
export function popState(atMs, ms) {
  if (ms < atMs) return { visible: false, scale: 0, alpha: 0, blur: 0 };
  const p = clamp01((ms - atMs) / POP_MS);
  return { visible: true, scale: p >= 1 ? 1 : backOut(p), alpha: clamp01(p * 2), blur: p >= 1 ? 0 : (1 - p) * 10 };
}

export function fillAlpha(fillMs, ms) {
  return clamp01((ms - fillMs) / 400);
}

const HOLD_S = 0.15;
export function activeWord(words, seconds) {
  for (let i = words.length - 1; i >= 0; i--) {
    const w = words[i];
    const next = words[i + 1];
    const end = next ? Math.min(w.end + HOLD_S, next.start) : w.end + HOLD_S;
    if (seconds >= w.start && seconds < end) return i;
  }
  return -1;
}
```

- [ ] **Step 4: Run Node tests**

Run: `node --test "reelmachine/recipes/mapreel/runtime/tests/*.test.mjs"`
Expected: all pass. If "a long flight pulls back" fails, check the `b1` sign (it uses `- RHO**4 * d2`).

- [ ] **Step 5: Write `draw.mjs` and `main.mjs`**

```js
// reelmachine/recipes/mapreel/runtime/draw.mjs
// Draws one frame of a map segment on a 2D canvas.
import { cameraAt, fillAlpha, popState, activeWord } from "./pure.mjs";

const W = 1080, H = 1920;
const FONT = '"Arial Black", "Helvetica Neue", Arial, sans-serif';

function toScreen(cam, x, y) {
  return [(x - cam.x) * cam.s + W / 2, (y - cam.y) * cam.s + H / 2];
}

function tracePlace(ctx, cam, rings) {
  ctx.beginPath();
  for (const ring of rings) {
    ring.forEach(([x, y], i) => {
      const [px, py] = toScreen(cam, x, y);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    ctx.closePath();
  }
}

function drawLayers(ctx, cam, layers, images) {
  ctx.fillStyle = "#0b2236";
  ctx.fillRect(0, 0, W, H);
  for (const layer of layers) {
    const img = images.get(layer.url);
    if (!img) continue;
    const [x0, y0, x1, y1] = layer.bbox;
    for (const shift of [-1, 0, 1]) { // the world repeats east and west
      if (layer.bbox[2] - layer.bbox[0] < 0.999 && shift !== 0) continue;
      const [px0, py0] = toScreen(cam, x0 + shift, y0);
      const [px1, py1] = toScreen(cam, x1 + shift, y1);
      if (px1 < 0 || px0 > W) continue;
      ctx.drawImage(img, px0, py0, px1 - px0, py1 - py0);
    }
  }
}

function fillPlace(ctx, cam, place, alpha) {
  if (alpha <= 0) return;
  ctx.save();
  ctx.globalAlpha = 0.8 * alpha;
  tracePlace(ctx, cam, place.rings);
  ctx.fillStyle = place.colour;
  ctx.fill("evenodd");
  ctx.globalAlpha = alpha;
  ctx.clip("evenodd"); // inner glow: a soft, bright stroke kept inside the border
  ctx.shadowColor = "rgba(255,255,255,0.9)";
  ctx.shadowBlur = 28;
  ctx.lineWidth = 10;
  ctx.strokeStyle = "rgba(255,255,255,0.55)";
  ctx.stroke();
  ctx.restore();
  ctx.save();
  ctx.globalAlpha = 0.7 * alpha;
  tracePlace(ctx, cam, place.rings);
  ctx.lineWidth = 2;
  ctx.strokeStyle = "rgba(255,255,255,0.8)";
  ctx.stroke();
  ctx.restore();
}

function ghostName(ctx, cam, place, alpha, seconds) {
  if (alpha <= 0) return;
  const [px, py] = toScreen(cam, place.label.x, place.label.y);
  ctx.save();
  ctx.globalAlpha = 0.22 * alpha;
  ctx.translate(px + seconds * 12, py);
  ctx.rotate(-0.14);
  ctx.font = `900 ${Math.round(W * 0.16)}px ${FONT}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = "#ffffff";
  ctx.fillText(place.name, 0, 0);
  ctx.restore();
}

function drawItem(ctx, item, ms, images) {
  const pop = popState(item.atMs, ms);
  if (!pop.visible) return;
  const driftY = -Math.max(0, ms - item.atMs) / 1000 * 6;
  ctx.save();
  ctx.globalAlpha = pop.alpha;
  if (pop.blur > 0.2) ctx.filter = `blur(${pop.blur.toFixed(1)}px)`;
  ctx.translate(item.x, item.y + driftY);
  ctx.scale(pop.scale, pop.scale);
  ctx.shadowColor = "rgba(0,0,0,0.45)";
  ctx.shadowBlur = 18;
  ctx.shadowOffsetY = 6;
  const img = item.image ? images.get(item.image) : null;
  if (img) {
    const k = Math.min(item.size / img.width, (item.size * 0.62) / img.height);
    ctx.drawImage(img, (-img.width * k) / 2, (-img.height * k) / 2, img.width * k, img.height * k);
  } else {
    let size = Math.round(item.size * 0.24);
    ctx.font = `900 ${size}px ${FONT}`;
    while (ctx.measureText(item.name).width > item.size * 1.1 && size > 28) {
      size -= 4;
      ctx.font = `900 ${size}px ${FONT}`;
    }
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    if (item.emoji) {
      ctx.font = `${Math.round(item.size * 0.34)}px sans-serif`;
      ctx.fillText(item.emoji, 0, -size * 0.95);
      ctx.font = `900 ${size}px ${FONT}`;
    }
    ctx.shadowColor = "rgba(255,255,255,0.75)";
    ctx.shadowBlur = 14;
    ctx.shadowOffsetY = 0;
    ctx.fillStyle = "#111111";
    ctx.fillText(item.name, 0, item.emoji ? size * 0.25 : 0);
  }
  ctx.restore();
}

function caption(ctx, words, seconds) {
  const i = activeWord(words, seconds);
  if (i < 0) return;
  const w = words[i];
  ctx.save();
  ctx.font = `900 60px ${FONT}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 10;
  ctx.strokeStyle = "rgba(0,0,0,0.85)";
  ctx.lineJoin = "round";
  ctx.strokeText(w.text, W / 2, H * 0.71);
  ctx.fillStyle = w.hot ? "#ffe14a" : "#ffffff";
  ctx.fillText(w.text, W / 2, H * 0.71);
  ctx.restore();
}

function legend(ctx, text) {
  if (!text) return;
  ctx.save();
  ctx.font = `900 50px ${FONT}`;
  const width = ctx.measureText(text).width + 64;
  ctx.fillStyle = "rgba(255,255,255,0.94)";
  ctx.beginPath();
  ctx.roundRect((W - width) / 2, 150, width, 86, 24);
  ctx.fill();
  ctx.fillStyle = "#111";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, W / 2, 193);
  ctx.restore();
}

export function drawFrame(ctx, doc, images, seconds) {
  const ms = seconds * 1000;
  const cam = cameraAt(doc.camera, ms);
  drawLayers(ctx, cam, doc.layers, images);
  const fadeOut = 1 - Math.min(1, ms / Math.max(1, doc.camera.flyMs * 0.8));
  for (const place of doc.previous || []) fillPlace(ctx, cam, place, fadeOut);
  for (const place of doc.places) fillPlace(ctx, cam, place, fillAlpha(place.fillMs, ms));
  if (doc.places.length === 1) ghostName(ctx, cam, doc.places[0], fillAlpha(doc.places[0].fillMs, ms), seconds);
  for (const item of doc.items) drawItem(ctx, item, ms, images);
  legend(ctx, doc.legend);
  caption(ctx, doc.words, seconds);
}
```

```js
// reelmachine/recipes/mapreel/runtime/main.mjs
// Boots a map segment: load its images, then draw whatever time HyperFrames seeks to.
// HyperFrames waits on window.__hf.buildReady (set inline in index.html) before capturing.
import { drawFrame } from "./draw.mjs";

const doc = JSON.parse(document.getElementById("segment-data").textContent);
const done = () => window.__mapResolve && window.__mapResolve();

function load(url) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve([url, img]);
    img.onerror = () => resolve([url, null]); // a missing image draws as nothing, not a crash
    img.src = url;
  });
}

try {
  const urls = [...doc.layers.map((l) => l.url), ...doc.items.filter((i) => i.image).map((i) => i.image)];
  const images = new Map((await Promise.all(urls.map(load))).filter(([, img]) => img));
  const canvas = document.getElementById("map");
  const ctx = canvas.getContext("2d");
  const renderAt = (t) => drawFrame(ctx, doc, images, t);
  window.addEventListener("hf-seek", (event) => renderAt(event.detail.time));
  window.__mapRenderAt = renderAt;
  renderAt(window.__hfThreeTime || 0);
} catch (error) {
  window.__mapError = String((error && error.stack) || error);
  console.error("mapreel: the segment could not be built", error);
} finally {
  done();
}
```

- [ ] **Step 6: Wire the runtime tests into CI**

In `.github/workflows/ci.yml`, next to the existing step at line ~79 that runs `node --test "reelmachine/recipes/explainer3d/runtime/tests/*.test.mjs"`, add:

```yaml
      - name: mapreel runtime tests
        run: node --test "reelmachine/recipes/mapreel/runtime/tests/*.test.mjs"
```

Also make sure the runtime ships in the wheel: check `[tool.setuptools.package-data]` (or the equivalent section) in `pyproject.toml` for an explainer3d `runtime/**` line and add the same for `"reelmachine.recipes.mapreel" = ["runtime/*.mjs"]`.

- [ ] **Step 7: Commit**

```bash
git add reelmachine/recipes/mapreel/runtime .github/workflows/ci.yml pyproject.toml
git commit -m "mapreel: the canvas runtime — a van Wijk fly-to, glowing fills, ghosted names, popping items, word captions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Segment documents and HyperFrames projects

**Files:**
- Create: `reelmachine/recipes/mapreel/compose.py`
- Test: `tests/test_mapreel_compose.py`

**Interfaces:**
- Consumes: `geometry.Shape/Frame/frame_for/to_screen/item_anchors/simplify_for/visible_bbox/WIDTH/HEIGHT`, the document shape from Task 6.
- Produces:
  - `RUNTIME_DIR`, `FPS = 30`, `ITEM_SIZE = 300`.
  - `segment_frame(shapes: list[Shape], zoom: float) -> Frame` (frames the union of the shapes' `main` bboxes).
  - `place_entry(code, name, colour, shape, frame, fill_ms, label_lonlat) -> dict` (rings simplified for the frame, unwrapped near the frame's x).
  - `item_entries(items: list[dict], shape: Shape | None, frame: Frame) -> list[dict]` where each input dict is `{"name", "image" (project-relative url or None), "emoji", "atMs"}`.
  - `segment_document(*, duration_ms, camera, layers, places, previous, items, words, legend) -> dict`.
  - `write_project(doc, dest: Path, links: dict[str, Path]) -> Path` (writes `index.html`, copies runtime `*.mjs`, links/copies files at the given project-relative paths).
  - `project_key(doc, links) -> str` (sha256 over the document JSON + each linked file's size and mtime + `RUNTIME_VERSION`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mapreel_compose.py
import json
import re
import subprocess
from pathlib import Path

import pytest

from reelmachine.recipes.mapreel import compose, geometry as g
from reelmachine.recipes.mapreel.places import Atlas

ATLAS = Atlas.load(Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson")


def _doc(tmp_path):
    shape = g.shape_of(ATLAS.resolve("ITA"))
    frame = compose.segment_frame([shape], 1.0)
    place = compose.place_entry("ITA", "Italy", "#3333ff", shape, frame, 700, (12.5, 42.5))
    items = compose.item_entries([{"name": "Gucci", "image": None, "emoji": None, "atMs": 1100},
                                  {"name": "Prada", "image": "images/prada.png", "emoji": None, "atMs": 1500}],
                                 shape, frame)
    return compose.segment_document(
        duration_ms=3000, camera={"from": frame.to_dict(), "to": frame.to_dict(), "flyMs": 0, "drift": 0.04},
        layers=[{"url": "layers/world.jpg", "bbox": [0, 0, 1, 1]}], places=[place], previous=[],
        items=items, words=[{"text": "Italy", "start": 0.25, "end": 0.6, "hot": False}], legend=None)


def test_a_document_has_what_the_runtime_reads(tmp_path):
    doc = _doc(tmp_path)
    assert doc["width"] == 1080 and doc["height"] == 1920 and doc["duration"] == pytest.approx(3.0)
    place = doc["places"][0]
    assert place["code"] == "ITA" and place["fillMs"] == 700 and len(place["rings"]) >= 1
    assert all(len(pt) == 2 for pt in place["rings"][0])
    assert [i["name"] for i in doc["items"]] == ["Gucci", "Prada"]
    assert all(0 < i["x"] < 1080 and 0 < i["y"] < 1920 and i["size"] > 0 for i in doc["items"])


def test_rings_are_simplified_for_the_screen(tmp_path):
    doc = _doc(tmp_path)
    raw = sum(len(r) for r in g.shape_of(ATLAS.resolve("ITA")).rings)
    assert sum(len(r) for r in doc["places"][0]["rings"]) <= raw


def test_the_project_has_html_runtime_and_linked_files(tmp_path):
    doc = _doc(tmp_path)
    layer = tmp_path / "world.jpg"
    layer.write_bytes(b"jpeg")
    project = compose.write_project(doc, tmp_path / "p", {"layers/world.jpg": layer})
    html = (project / "index.html").read_text()
    assert 'data-duration="3.000"' in html and 'id="segment-data"' in html and "__mapReady" in html
    assert {"main.mjs", "draw.mjs", "pure.mjs"} <= {p.name for p in (project / "runtime").iterdir()}
    assert (project / "layers" / "world.jpg").read_bytes() == b"jpeg"
    data = re.search(r'<script id="segment-data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert json.loads(data.replace("<\\/", "</"))["places"][0]["code"] == "ITA"


def test_the_project_key_changes_with_content(tmp_path):
    doc = _doc(tmp_path)
    layer = tmp_path / "world.jpg"
    layer.write_bytes(b"jpeg")
    a = compose.project_key(doc, {"layers/world.jpg": layer})
    doc["legend"] = "changed"
    assert compose.project_key(doc, {"layers/world.jpg": layer}) != a


def test_the_runtime_unit_tests_pass():
    proc = subprocess.run(["node", "--test", str(compose.RUNTIME_DIR / "tests" / "pure.test.mjs")],
                          capture_output=True, text=True)
    if proc.returncode == 127:
        pytest.skip("node not installed")
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mapreel_compose.py -v`
Expected: FAIL with `ImportError: cannot import name 'compose'`

- [ ] **Step 3: Implement**

```python
# reelmachine/recipes/mapreel/compose.py
"""A segment becomes a document the canvas runtime draws, inside a HyperFrames project."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from .geometry import HEIGHT, WIDTH, Frame, Shape, frame_for, item_anchors, merc, simplify_for, to_screen

HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE / "runtime"
FPS = 30
ITEM_SIZE = 300


def _runtime_version() -> str:
    digest = hashlib.sha256()
    for path in sorted(RUNTIME_DIR.glob("*.mjs")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


RUNTIME_VERSION = _runtime_version()


def segment_frame(shapes: list[Shape], zoom: float) -> Frame:
    boxes = [s.main for s in shapes]
    union = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    return frame_for(union, zoom=zoom)


def _near(frame: Frame, ring: np.ndarray) -> np.ndarray:
    shift = round(frame.x - float(ring[:, 0].mean()))
    return ring + np.array([shift, 0.0]) if shift else ring


def place_entry(code: str, name: str, colour: str, shape: Shape, frame: Frame, fill_ms: int,
                label_lonlat: tuple[float, float]) -> dict[str, Any]:
    rings = []
    for ring in shape.rings:
        slim = simplify_for(frame, _near(frame, ring))
        if len(slim) >= 3:
            rings.append([[round(float(x), 7), round(float(y), 7)] for x, y in slim])
    lx, ly = merc(label_lonlat[0], label_lonlat[1])
    lx = float(lx) + round(frame.x - float(lx))
    return {"code": code, "name": name, "colour": colour, "rings": rings, "fillMs": int(fill_ms),
            "label": {"x": lx, "y": float(ly)}}


def item_entries(items: list[dict[str, Any]], shape: Shape | None, frame: Frame) -> list[dict[str, Any]]:
    if not items:
        return []
    if shape is None:
        anchors = [(WIDTH / 2, HEIGHT * (0.25 + 0.45 * i / max(1, len(items) - 1))) for i in range(len(items))]
    else:
        rings_px = [to_screen(frame, _near(frame, r)) for r in shape.rings]
        anchors = item_anchors(rings_px, len(items))
    gap = min((np.hypot(a[0] - b[0], a[1] - b[1]) for i, a in enumerate(anchors) for b in anchors[i + 1:]),
              default=ITEM_SIZE * 2)
    size = int(max(180, min(ITEM_SIZE, gap * 0.95)))
    return [{"name": it["name"], "image": it.get("image"), "emoji": it.get("emoji"), "atMs": int(it["atMs"]),
             "x": round(x, 1), "y": round(y, 1), "size": size} for it, (x, y) in zip(items, anchors)]


def segment_document(*, duration_ms: int, camera: dict[str, Any], layers: list[dict[str, Any]],
                     places: list[dict[str, Any]], previous: list[dict[str, Any]], items: list[dict[str, Any]],
                     words: list[dict[str, Any]], legend: str | None) -> dict[str, Any]:
    return {"width": WIDTH, "height": HEIGHT, "duration": duration_ms / 1000, "camera": camera, "layers": layers,
            "places": places, "previous": previous, "items": items, "words": words, "legend": legend}


_CSS = "html,body{margin:0;background:#000}#root{position:relative;width:100%;height:100%;overflow:hidden}" \
       "#map{position:absolute;inset:0;width:100%;height:100%;display:block}"


def _link(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def write_project(doc: dict[str, Any], dest: Path, links: dict[str, Path]) -> Path:
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "runtime").mkdir(parents=True)
    for path in RUNTIME_DIR.glob("*.mjs"):
        shutil.copy2(path, dest / "runtime" / path.name)
    for rel, src in links.items():
        _link(Path(src), dest / rel)
    data = json.dumps(doc, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width={WIDTH}, height={HEIGHT}" />
<style>{_CSS}</style>
<script>window.__hf = window.__hf || {{}}; window.__mapReady = new Promise((r) => {{ window.__mapResolve = r; }}); window.__hf.buildReady = {{ mapreel: window.__mapReady }};</script>
</head>
<body>
<div id="root" data-composition-id="segment" data-start="0" data-width="{WIDTH}" data-height="{HEIGHT}" data-duration="{doc['duration']:.3f}" data-no-timeline>
<canvas id="map" width="{WIDTH}" height="{HEIGHT}"></canvas>
</div>
<script id="segment-data" type="application/json">{data}</script>
<script type="module" src="./runtime/main.mjs"></script>
</body>
</html>
"""
    (dest / "index.html").write_text(html, encoding="utf-8")
    return dest


def project_key(doc: dict[str, Any], links: dict[str, Path]) -> str:
    digest = hashlib.sha256(json.dumps(doc, sort_keys=True).encode())
    digest.update(RUNTIME_VERSION.encode())
    for rel in sorted(links):
        stat = Path(links[rel]).stat()
        digest.update(f"{rel}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mapreel_compose.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add reelmachine/recipes/mapreel/compose.py tests/test_mapreel_compose.py
git commit -m "mapreel: segments become documents the runtime draws, written as HyperFrames projects

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Stages and the engine run

**Files:**
- Create: `reelmachine/recipes/mapreel/stages.py`
- Modify: `reelmachine/recipes/mapreel/models.py` (stage output models), `reelmachine/recipes/mapreel/recipe.py` (stages), `reelmachine/recipes/explainer3d/stages.py` (rename `_speak` → `speak_line`, keep `_speak = speak_line`)
- Test: `tests/test_mapreel_engine.py`

**Interfaces:**
- Consumes: everything above; from explainer3d: `stages.speak_line(ctx, provider, text, voice, language) -> (Path, int, list[WordSpan], Licence | None)`, `audio.pad_speech`, `audio.mix`, `audio.synth_sfx`, `video.join_shots`, `video.mux`, `markup.parse_markup/spoken_text/display_words/display_text`, `timing.shot_windows`, `timing.fit_windows`; `render.captions.write_srt`; `doodle.narration.provider_for`.
- Produces stage ids `script`, `geo`, `images`, `narration`, `segments`, `prep`, `compose`, `render` (the executor collects `reel.mp4`, `captions.srt`, `reel.json`, `timeline.json` from `compose`/`render`, and anything in `<stage workdir>/artifacts/`).
- Output models (in `models.py`): `ResolvedSegment` (`id`, `index`, `kind`, `codes: list[str]`, `names: dict[str, str]`, `colours: dict[str, str]`), `ScriptOutput`, `GeoOutput` (adds `layers: dict[str, str]` segment id → detail layer path, `world: str`, `frames: dict[str, dict]`, `detail_bboxes: dict[str, list[float]]`), `ImagesOutput` (adds `images: dict[str, str | None]` key `"<seg id>/<item index>"` → local file or None, `warnings: list[str]`), `NarrationOutput` (adds `audio_asset`, `duration_ms`, `windows: list[Window]`, `words: list[list[WordSpan]]`, `hot: list[list[bool]]`, `spoken: list[list[WordSpan]]` raw voice words per segment, `licence`), `SegmentsOutput` (`narration`, `projects: list[SegmentProject]`, `cues: list[dict]`), `PrepOutput`, `ComposeOutput`, `RenderOutput` — mirror explainer3d's names and fields (`reelmachine/recipes/explainer3d/models.py`) wherever the meaning is the same.

- [ ] **Step 1: Make `speak_line` public in explainer3d**

In `reelmachine/recipes/explainer3d/stages.py`, rename `def _speak(` to `def speak_line(`, update its one caller in `NarrationStage.run`, and add right after the function:

```python
_speak = speak_line  # the old private name, kept for callers outside this module
```

Run: `uv run pytest tests/test_explainer3d.py -q`
Expected: PASS (unchanged behaviour).

- [ ] **Step 2: Write the failing engine test**

```python
# tests/test_mapreel_engine.py
import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from reelmachine.config import get_settings
from reelmachine.core.job import JobRequest, JobState
from reelmachine.engine import Engine
from reelmachine.recipes.mapreel import geodata

pytestmark = pytest.mark.slow
FIXTURE = Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson"

SCRIPT = {
    "title": "Brands",
    "segments": [
        {"kind": "highlight", "countries": ["USA", "ITA", "FRA"], "legend": "Fashion superpowers",
         "narration": "Only *three countries* dress the world. Number one will surprise you."},
        {"kind": "country", "country": "Italy", "narration": "Italy has Gucci, Prada and Versace.",
         "items": [{"name": "Gucci"}, {"name": "Prada", "image": "LOGO"}, {"name": "Versace", "image": "/nope.png"}]},
    ],
}


def _settings(tmp_path):
    return dataclasses.replace(get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out",
                               tts="fake", shared_cache=tmp_path / "shared")


def _seed(settings):
    root = geodata.geo_cache(settings)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURE, root / geodata.FILES["countries"][0])
    for key, size in (("world", "720x360"), ("detail", "1440x720")):
        subprocess.run([settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}",
                        "-frames:v", "1", str(root / geodata.FILES[key][0])], check=True)


def _logo(tmp_path, settings):
    path = tmp_path / "prada.png"
    subprocess.run([settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:size=200x80",
                    "-frames:v", "1", str(path)], check=True)
    return str(path)


def _run(tmp_path, inputs):
    settings = _settings(tmp_path)
    _seed(settings)
    script = json.loads(json.dumps(inputs).replace('"LOGO"', json.dumps(_logo(tmp_path, settings))))
    engine = Engine(settings, provider_overrides={"renderer": "explainer3d-fake"})
    try:
        job = engine.submit(JobRequest(recipe="mapreel", inputs=script), caller="local")
        return engine.wait(job.id, caller="local", timeout_s=600)
    finally:
        engine.close()


def _artifacts(job):
    return {a.name: Path(a.path) for a in job.result.artifacts}


def test_a_storyboard_stops_with_a_sheet_and_the_resolved_script(tmp_path):
    job = _run(tmp_path, {**SCRIPT, "mode": "storyboard"})
    assert job.state == JobState.SUCCEEDED, job.error
    arts = _artifacts(job)
    assert {"storyboard.jpg", "script.resolved.json"} <= set(arts) and "reel.mp4" not in arts
    resolved = json.loads(arts["script.resolved.json"].read_text())
    seg = resolved["segments"][1]
    assert seg["codes"] == ["ITA"] and len(seg["items"]) == 3
    assert seg["items"][0]["atMs"] < seg["items"][1]["atMs"] < seg["items"][2]["atMs"]
    assert seg["items"][1]["image"] and seg["items"][2]["image"] is None  # the missing file became a badge
    assert any("Versace" in w for w in resolved["warnings"])


def test_a_build_makes_a_reel_with_captions(tmp_path):
    job = _run(tmp_path, SCRIPT)
    assert job.state == JobState.SUCCEEDED, job.error
    arts = _artifacts(job)
    from reelmachine import ffmpeg

    info = ffmpeg.probe(arts["reel.mp4"])
    assert (info.width, info.height) == (1080, 1920) and info.has_audio and info.duration_s > 3
    assert "Gucci" in arts["captions.srt"].read_text()


def test_an_unknown_country_fails_with_a_suggestion(tmp_path):
    bad = {"segments": [{"kind": "country", "country": "Itally", "narration": "x", "items": [{"name": "a"}]}]}
    job = _run(tmp_path, bad)
    assert job.state == JobState.FAILED and "Italy" in json.dumps(job.error.model_dump() if hasattr(job.error, "model_dump") else str(job.error))


def test_the_camera_continues_across_a_cut(tmp_path):
    job = _run(tmp_path, {**SCRIPT, "mode": "storyboard"})
    resolved = json.loads(_artifacts(job)["script.resolved.json"].read_text())
    a, b = resolved["segments"]
    assert b["camera"]["from"] == a["camera"]["to"]
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_mapreel_engine.py -v`
Expected: FAIL — the recipe has no stages, so the job fails or produces no artifacts.

- [ ] **Step 4: Add the output models**

Append to `reelmachine/recipes/mapreel/models.py`:

```python
from ...core.assets import Licence
from ...core.stage import StageOutput
from ...core.timeline import Timeline, WordSpan


class ResolvedSegment(BaseModel):
    id: str
    index: int
    kind: str
    codes: list[str]
    names: dict[str, str]
    colours: dict[str, str]


class ScriptOutput(StageOutput):
    inputs: MapReelInputs
    segments: list[ResolvedSegment]


class GeoOutput(ScriptOutput):
    world: str
    layers: dict[str, str]                 # segment id -> detail layer file
    detail_bboxes: dict[str, list[float]]
    frames: dict[str, dict[str, float]]    # segment id -> landed frame {x, y, s}


class ImagesOutput(GeoOutput):
    images: dict[str, str | None]          # "<segment id>/<item index>" -> local file, or None for a badge
    warnings: list[str] = Field(default_factory=list)


class Window(BaseModel):
    start_ms: int
    duration_ms: int
    speech_offset_ms: int
    speech_ms: int


class NarrationOutput(ImagesOutput):
    audio_asset: str
    duration_ms: int
    windows: list[Window]
    words: list[list[WordSpan]]            # shown words, reel time
    hot: list[list[bool]]
    spoken: list[list[WordSpan]]           # the voice's own words, line time
    provider: str
    licence: Licence | None = None


class SegmentProject(BaseModel):
    segment_id: str
    index: int
    dir: str
    key: str
    nominal_ms: int


class SegmentsOutput(StageOutput):
    narration: NarrationOutput
    projects: list[SegmentProject]
    cues: list[dict] = Field(default_factory=list)


class Track(BaseModel):
    segment_id: str
    path: str
    render_ms: int


class PrepOutput(StageOutput):
    segments: SegmentsOutput
    tracks: list[Track] = Field(default_factory=list)
    storyboard: str = ""
    renderer: str = ""


class ComposeOutput(StageOutput):
    prep: PrepOutput
    timeline: Timeline
    render_options: dict


class RenderOutput(StageOutput):
    video: str
    srt: str
    manifest: str
    duration_ms: int
```

Check `StageOutput`'s own fields (`reelmachine/core/stage.py:26`) — if it sets `model_config = ConfigDict(extra="forbid")`, these subclasses inherit it, which is what we want. Check that explainer3d's `RenderOutput` field names match (`video`, `srt`, `manifest`, `duration_ms`) — the executor reads `render.video`, `render.srt`, `render.manifest`.

- [ ] **Step 5: Write `stages.py`**

```python
# reelmachine/recipes/mapreel/stages.py
"""mapreel stages: script → geo → images → narration → segments → prep → compose → render."""

from __future__ import annotations

import json
import math
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ... import ffmpeg
from ...config import Settings
from ...core.assets import AssetKind, Provenance
from ...core.errors import InvalidInput, RendererUnavailable, VoiceUnavailable
from ...core.stage import StageContext
from ...core.timeline import Caption, Clip, Timeline, TimedAudioRef, WordSpan
from ...render.captions import write_srt
from ..doodle.narration import provider_for
from ..explainer3d.audio import mix, pad_speech, synth_sfx
from ..explainer3d.markup import display_text, display_words, parse_markup, spoken_text
from ..explainer3d.stages import speak_line
from ..explainer3d.timing import fit_windows, shot_windows
from ..explainer3d.video import join_shots, mux
from . import basemap, compose, geodata, palette
from .geometry import Frame, shape_of, visible_bbox
from .models import (
    ComposeOutput, GeoOutput, ImagesOutput, MapReelInputs, NarrationOutput, PrepOutput, RenderOutput,
    ResolvedSegment, ScriptOutput, SegmentProject, SegmentsOutput, Track, Window,
)
from .places import Atlas, atlas_for
from .timing import FLY_MS, MIN_SEGMENT_MS, match, pop_times

IMAGE_MAX_BYTES = 8 * 1024 * 1024


def _artifacts(ctx: StageContext) -> Path:
    path = ctx.workdir / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _codes(segment: Any) -> list[str]:
    return [segment.country] if segment.kind == "country" else list(segment.countries)


def resolve_segments(inputs: MapReelInputs, atlas: Atlas) -> list[ResolvedSegment]:
    """Places to codes (errors carry the segment path) and a colour for every place."""
    resolved: list[tuple[str, int, Any, list[str], dict[str, str]]] = []
    for index, (seg_id, segment) in enumerate(zip(inputs.segment_ids(), inputs.segments)):
        codes, names = [], {}
        for j, query in enumerate(_codes(segment)):
            try:
                place = atlas.resolve(query)
            except InvalidInput as exc:
                field = "country" if segment.kind == "country" else f"countries[{j}]"
                exc.details["path"] = f"segments[{index}].{field}"
                raise
            codes.append(place.code)
            names[place.code] = place.name
        resolved.append((seg_id, index, segment, codes, names))
    fixed = {codes[0]: seg.colour for _, _, seg, codes, _ in resolved if seg.colour}
    colours = palette.assign([codes for *_, codes, _ in resolved], fixed=fixed)
    return [ResolvedSegment(id=seg_id, index=index, kind=seg.kind, codes=codes, names=names,
                            colours={c: colours[c] for c in codes})
            for seg_id, index, seg, codes, names in resolved]


class ScriptStage:
    id = "script"
    revision = 1
    cacheable = False
    output_model = ScriptOutput

    def run(self, ctx: StageContext, payload: Any) -> ScriptOutput:
        inputs = MapReelInputs.model_validate(payload)
        try:
            segments = resolve_segments(inputs, atlas_for(ctx.config))
        except InvalidInput as exc:
            exc.stage = self.id
            raise
        return ScriptOutput(inputs=inputs, segments=segments)


class GeoStage:
    """Frames for every segment, the world layer, and one sharp layer per segment."""

    id = "geo"
    revision = 1
    cacheable = False
    output_model = GeoOutput

    def run(self, ctx: StageContext, payload: ScriptOutput) -> GeoOutput:
        settings: Settings = ctx.config
        root = geodata.geo_cache(settings)
        atlas = atlas_for(settings)
        world = basemap.world_layer(settings, root)
        frames, layers, bboxes = {}, {}, {}
        previous = None
        for seg, spec in zip(payload.segments, payload.inputs.segments):
            ctx.cancel.raise_if_cancelled()
            if spec.camera.hold and previous is not None:
                frame = previous
            else:
                frame = compose.segment_frame([shape_of(atlas.places[c]) for c in seg.codes], spec.camera.zoom)
            frames[seg.id] = frame.to_dict()
            path, bbox = basemap.detail_layer(settings, root, visible_bbox(frame, margin=0.25))
            layers[seg.id], bboxes[seg.id] = str(path), list(bbox)
            previous = frame
        return GeoOutput(**payload.model_dump(), world=str(world), layers=layers, detail_bboxes=bboxes, frames=frames)


def _fetch_image(ref: str, dest_dir: Path) -> Path:
    """A local file or an http(s) URL, copied into the job; raises on anything unusable."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if ref.startswith(("http://", "https://")):
        response = httpx.get(ref, timeout=30, follow_redirects=True, headers={"User-Agent": geodata.USER_AGENT})
        response.raise_for_status()
        kind = response.headers.get("content-type", "")
        if not kind.startswith("image/") or len(response.content) > IMAGE_MAX_BYTES:
            raise ValueError(f"not an image under 8 MB ({kind or 'no content type'})")
        suffix = "." + kind.split("/", 1)[1].split(";")[0].replace("svg+xml", "svg").replace("jpeg", "jpg")
        path = dest_dir / f"{abs(hash(ref)) % 10**12}{suffix}"
        path.write_bytes(response.content)
        return path
    src = Path(ref).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"no file at {src}")
    if src.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}:
        raise ValueError(f"{src.suffix or 'no extension'} is not an image type the renderer draws")
    path = dest_dir / f"{abs(hash(str(src))) % 10**12}{src.suffix.lower()}"
    shutil.copy2(src, path)
    return path


class ImagesStage:
    """Caller-supplied images (paths, URLs). Anything that fails becomes a text badge."""

    id = "images"
    revision = 1
    cacheable = False
    output_model = ImagesOutput

    def run(self, ctx: StageContext, payload: GeoOutput) -> ImagesOutput:
        images: dict[str, str | None] = {}
        warnings: list[str] = []
        for seg, spec in zip(payload.segments, payload.inputs.segments):
            for j, item in enumerate(getattr(spec, "items", [])):
                key = f"{seg.id}/{j}"
                images[key] = None
                if not item.image:
                    continue
                if item.image.startswith("commons:"):
                    warnings.append(f"{seg.id} {item.name}: Commons images arrive with find_images; shown as a badge")
                    continue
                try:
                    images[key] = str(_fetch_image(item.image, ctx.workdir / "images"))
                except (OSError, ValueError, httpx.HTTPError) as exc:
                    warnings.append(f"{seg.id} {item.name}: image not usable ({exc}); shown as a badge")
        return ImagesOutput(**payload.model_dump(), images=images, warnings=warnings)


class NarrationStage:
    id = "narration"
    revision = 1
    cacheable = False
    output_model = NarrationOutput

    def run(self, ctx: StageContext, payload: ImagesOutput) -> NarrationOutput:
        inputs, settings = payload.inputs, ctx.config
        provider = ctx.providers.get("narration")
        name = getattr(provider, "name", "")
        if provider_for(inputs.voice) != name:
            raise VoiceUnavailable(
                f"the voice {inputs.voice!r} needs the {provider_for(inputs.voice)!r} narration provider, "
                f"but this deployment serves {name!r}",
                hint=f"use voice '{name}:<voice-id>', or set REEL_TTS on the deployment", stage=self.id)
        work = ctx.workdir / "lines"
        work.mkdir(parents=True, exist_ok=True)
        lines, licence = [], None
        for seg, spec in zip(payload.segments, inputs.segments):
            ctx.cancel.raise_if_cancelled()
            tokens = parse_markup(spec.narration)
            path, ms, words, line_licence = speak_line(ctx, provider, spoken_text(tokens), inputs.voice, inputs.language)
            local = work / f"{seg.id}.wav"
            shutil.copy2(path, local)
            lines.append((local, ms, words, tokens))
            licence = line_licence or licence
        windows = shot_windows([ms for _, ms, _, _ in lines], min_ms=MIN_SEGMENT_MS)
        if inputs.length:
            windows = fit_windows(windows, inputs.length * 1000)
        padded, shown_all, hot_all, spoken_all = [], [], [], []
        for seg, (path, ms, words, tokens), window in zip(payload.segments, lines, windows):
            padded.append(pad_speech(path, work / f"{seg.id}.padded.wav", lead_ms=window.speech_offset_ms,
                                     total_ms=window.duration_ms, settings=settings, cancel=ctx.cancel))
            base = window.start_ms + window.speech_offset_ms
            shown = display_words(tokens, words, total_ms=ms)
            shown_all.append([WordSpan(text=w.text, start_ms=base + w.start_ms, end_ms=base + w.end_ms) for w in shown])
            hot_all.append([w.hot for w in shown])
            spoken_all.append(list(words))
        joined = ctx.workdir / "narration.wav"
        if len(padded) == 1:
            shutil.copy2(padded[0], joined)
        else:
            ffmpeg.concat_audio(padded, joined, cancel=ctx.cancel)
        asset = ctx.assets.put(joined, kind=AssetKind.NARRATION, licence=licence,
                               provenance=Provenance(provider=name, source=inputs.voice), ext=".wav")
        return NarrationOutput(
            **payload.model_dump(), audio_asset=asset.id, duration_ms=windows[-1].end_ms,
            windows=[Window(start_ms=w.start_ms, duration_ms=w.duration_ms, speech_offset_ms=w.speech_offset_ms,
                            speech_ms=w.speech_ms) for w in windows],
            words=shown_all, hot=hot_all, spoken=spoken_all, provider=name, licence=licence)


class SegmentsStage:
    """Pop times, item layout and one HyperFrames project per segment; script.resolved.json."""

    id = "segments"
    revision = 1
    cacheable = False
    output_model = SegmentsOutput

    def run(self, ctx: StageContext, payload: NarrationOutput) -> SegmentsOutput:
        atlas = atlas_for(ctx.config)
        inputs = payload.inputs
        projects, cues, resolved = [], [], []
        previous_frame, previous_places = None, []
        for i, (seg, spec, window) in enumerate(zip(payload.segments, inputs.segments, payload.windows)):
            frame = payload.frames[seg.id]
            start = previous_frame or frame
            fly = 0 if start == frame else FLY_MS
            speech_start = window.speech_offset_ms
            speech_end = window.speech_offset_ms + window.speech_ms
            spoken = payload.spoken[i]
            shapes = {c: shape_of(atlas.places[c]) for c in seg.codes}
            landed = Frame(**frame)

            # places fill when named (highlight) or as the camera lands (country)
            place_names = [seg.names[c] for c in seg.codes]
            heard = match(place_names, spoken) if spec.kind == "highlight" else [None] * len(seg.codes)
            fills, _ = pop_times(heard, overrides=[None] * len(seg.codes),
                                 speech_start_ms=speech_start, speech_end_ms=speech_end)
            if spec.kind == "country":
                fills = [max(300, fly - 200)]
            places = [compose.place_entry(c, seg.names[c], seg.colours[c], shapes[c], landed, fills[k],
                                          atlas.places[c].label) for k, c in enumerate(seg.codes)]

            items_in = []
            if spec.kind == "country":
                heard_items = match([it.say or it.name for it in spec.items], spoken)
                overrides = [None if it.at is None else int(it.at * 1000) for it in spec.items]
                times, spread = pop_times(heard_items, overrides=overrides,
                                          speech_start_ms=speech_start, speech_end_ms=speech_end)
                for j, (item, at) in enumerate(zip(spec.items, times)):
                    local = payload.images.get(f"{seg.id}/{j}")
                    items_in.append({"name": item.name, "emoji": item.emoji, "atMs": at,
                                     "image": f"images/{Path(local).name}" if local else None, "_local": local})
                for j in spread:
                    payload.warnings.append(f"{seg.id} {spec.items[j].name}: not heard in the narration; "
                                            "its pop is spread evenly (add `say` or `at`)")
                items = compose.item_entries(items_in, shapes[seg.codes[0]], landed)
            else:
                items = []

            words = [{"text": w.text, "start": (w.start_ms - window.start_ms) / 1000,
                      "end": (w.end_ms - window.start_ms) / 1000, "hot": hot}
                     for w, hot in zip(payload.words[i], payload.hot[i])]
            layers = [{"url": "layers/world.jpg", "bbox": [0, 0, 1, 1]},
                      {"url": "layers/detail.jpg", "bbox": payload.detail_bboxes[seg.id]}]
            camera = {"from": start, "to": frame, "flyMs": fly, "drift": 0.04}
            doc = compose.segment_document(
                duration_ms=window.duration_ms, camera=camera, layers=layers, places=places,
                previous=[{"colour": p["colour"], "rings": p["rings"]} for p in previous_places] if fly else [],
                items=items, words=words, legend=getattr(spec, "legend", None))
            links = {"layers/world.jpg": Path(payload.world), "layers/detail.jpg": Path(payload.layers[seg.id])}
            for it in items_in:
                if it["_local"]:
                    links[it["image"]] = Path(it["_local"])
            directory = compose.write_project(doc, ctx.workdir / "projects" / seg.id, links)
            projects.append(SegmentProject(segment_id=seg.id, index=i, dir=str(directory),
                                           key=compose.project_key(doc, links), nominal_ms=window.duration_ms))
            if fly:
                cues.append({"at_ms": window.start_ms, "sfx": "whoosh"})
            cues.extend({"at_ms": window.start_ms + it["atMs"], "sfx": "pop"} for it in items)
            resolved.append({"id": seg.id, "kind": spec.kind, "codes": seg.codes, "colours": seg.colours,
                             "window": window.model_dump(), "camera": camera,
                             "places": [{"code": p["code"], "fillMs": p["fillMs"]} for p in places],
                             "items": [{"name": it["name"], "atMs": it["atMs"], "image": it["image"],
                                        "x": it["x"], "y": it["y"]} for it in items]})
            previous_frame, previous_places = frame, places
        (_artifacts(ctx) / "script.resolved.json").write_text(
            json.dumps({"segments": resolved, "warnings": payload.warnings}, indent=2, ensure_ascii=False),
            encoding="utf-8")
        return SegmentsOutput(narration=payload, projects=projects, cues=cues)
```

Check `synth_sfx` supports `"pop"` and `"whoosh"`: run `grep -n "SFX_NAMES" -A3 reelmachine/recipes/explainer3d/audio.py`. If `"pop"` is missing, use the closest existing short percussive name in `SFX_NAMES` and note it in the commit message.

Continue `stages.py` with prep, compose and render — these follow explainer3d's `PrepStage`, `ComposeStage` and `RenderStage` (`reelmachine/recipes/explainer3d/stages.py:254-400`) with segment names:

```python
def _caller_cache(ctx: StageContext) -> Path:
    settings: Settings = ctx.config
    job_id = getattr(ctx.job, "id", "")
    if job_id and settings.workdir.name == job_id:
        return settings.workdir.parent / "cache" / "mapreel"
    return settings.cache_dir / "mapreel"


class PrepStage:
    """Render every segment (cached by content) or, in storyboard mode, one still each."""

    id = "prep"
    revision = 1
    cacheable = False
    output_model = PrepOutput

    def run(self, ctx: StageContext, payload: SegmentsOutput) -> PrepOutput:
        settings: Settings = ctx.config
        renderer = ctx.providers.get("renderer")
        problems = renderer.missing()
        if problems:
            raise RendererUnavailable("the segment renderer is not usable on this deployment",
                                      hint="; ".join(problems), stage=self.id)
        if payload.narration.inputs.mode == "storyboard":
            return self._storyboard(ctx, payload, renderer, settings)
        cache = _caller_cache(ctx) / "segments"

        def render_one(project: SegmentProject) -> Track:
            cached = cache / project.key[:2] / f"{project.key}.mp4"
            if not cached.is_file():
                ctx.progress.detail(f"  · rendering {project.segment_id} ({project.nominal_ms / 1000:.1f}s)")
                partial = cached.with_name(f"{project.key}.partial.mp4")
                partial.parent.mkdir(parents=True, exist_ok=True)
                renderer.render(Path(project.dir), partial, fps=compose.FPS, seconds=project.nominal_ms / 1000,
                                cancel=ctx.cancel)
                partial.replace(cached)
            return Track(segment_id=project.segment_id, path=str(cached), render_ms=project.nominal_ms)

        with ThreadPoolExecutor(max_workers=2) as pool:
            tracks = list(pool.map(render_one, payload.projects))
        return PrepOutput(segments=payload, tracks=tracks, renderer=renderer.name)

    def _storyboard(self, ctx: StageContext, payload: SegmentsOutput, renderer: Any, settings: Settings) -> PrepOutput:
        stills = ctx.workdir / "stills"
        frames = [renderer.snapshot(Path(p.dir), p.nominal_ms / 1000 * 0.75, stills / f"{p.index:03d}.png",
                                    cancel=ctx.cancel) for p in payload.projects]
        cols = min(4, len(frames))
        rows = math.ceil(len(frames) / cols)
        sheet_in = ctx.workdir / "sheet"
        sheet_in.mkdir(parents=True, exist_ok=True)
        for index, frame in enumerate(frames, start=1):
            shutil.copy2(frame, sheet_in / f"{index:03d}.png")
        for index in range(len(frames) + 1, cols * rows + 1):
            ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "lavfi",
                        "-i", "color=white:size=1080x1920", "-frames:v", "1", str(sheet_in / f"{index:03d}.png")],
                       cancel=ctx.cancel)
        sheet = _artifacts(ctx) / "storyboard.jpg"
        ffmpeg.run([settings.ffmpeg, "-hide_banner", "-nostdin", "-y", "-framerate", "1", "-start_number", "1",
                    "-i", str(sheet_in / "%03d.png"), "-vf", f"scale=360:640,tile={cols}x{rows}:padding=8:color=white",
                    "-frames:v", "1", "-q:v", "3", str(sheet)], cancel=ctx.cancel)
        return PrepOutput(segments=payload, storyboard=str(sheet), renderer=renderer.name, halt_pipeline=True)


class ComposeStage:
    id = "compose"
    revision = 1
    cacheable = False
    output_model = ComposeOutput

    def run(self, ctx: StageContext, payload: PrepOutput) -> ComposeOutput:
        narration = payload.segments.narration
        inputs = narration.inputs
        settings: Settings = ctx.config
        video = [Clip(element_id=t.segment_id, asset=t.path, start_ms=w.start_ms, duration_ms=w.duration_ms,
                      origin="scene") for t, w in zip(payload.tracks, narration.windows)]
        captions = [Caption(start_ms=words[0].start_ms, end_ms=words[-1].end_ms,
                            text=display_text(parse_markup(spec.narration)), lang=inputs.language,
                            style_ref="mapreel.caption", words=words, meta={"source": seg.id})
                    for seg, spec, words in zip(narration.segments, inputs.segments, narration.words) if words]
        timeline = Timeline(duration_ms=narration.duration_ms, aspect="vertical", render_mode="hyperframes",
                            style=inputs.look, video=video, captions=captions,
                            audio=[TimedAudioRef(element_id="narration", asset=narration.audio_asset,
                                                 duration_ms=narration.duration_ms, role="narration")],
                            meta={"renderer": payload.renderer})
        outdir = Path(inputs.outdir).expanduser() if inputs.outdir else settings.outdir
        name = inputs.name or ctx.job.id
        return ComposeOutput(prep=payload, timeline=timeline, render_options={
            "outdir": str(outdir), "reel_name": name, "workdir": str(settings.workdir / "render" / name),
            "credits": [f"Narration: {inputs.voice}", geodata.NATURAL_EARTH.attribution,
                        geodata.BLUE_MARBLE.attribution]})


class RenderStage:
    id = "render"
    revision = 1
    cacheable = False
    output_model = RenderOutput

    def run(self, ctx: StageContext, payload: ComposeOutput) -> RenderOutput:
        settings: Settings = ctx.config
        prep, timeline, options = payload.prep, payload.timeline, payload.render_options
        narration = prep.segments.narration
        inputs = narration.inputs
        work, outdir = Path(options["workdir"]), Path(options["outdir"])
        work.mkdir(parents=True, exist_ok=True)
        outdir.mkdir(parents=True, exist_ok=True)
        name = options["reel_name"]
        joined = join_shots([Path(t.path) for t in prep.tracks], ["cut"] * len(prep.tracks),
                            [t.render_ms for t in prep.tracks], work / "video.mp4",
                            settings=settings, fps=compose.FPS, cancel=ctx.cancel)
        cues = [(cue["at_ms"], synth_sfx(cue["sfx"], work / "sfx" / f"{cue['sfx']}.wav", settings=settings,
                                         designed=True)) for cue in prep.segments.cues]
        music = Path(inputs.music).expanduser() if inputs.music else None
        audio = mix(ctx.assets.get(narration.audio_asset), cues, music, work / "audio.wav",
                    total_ms=timeline.duration_ms, settings=settings, profile="studio", cancel=ctx.cancel)
        video = mux(joined, audio, outdir / f"{name}.mp4", total_ms=timeline.duration_ms, settings=settings,
                    cancel=ctx.cancel)
        srt = write_srt(timeline.captions, work / f"{name}.srt")
        manifest = {"reel": str(video), "recipe": "mapreel", "mode": "hyperframes",
                    "created": datetime.now(timezone.utc).isoformat(), "durationMs": timeline.duration_ms,
                    "segments": len(prep.tracks), "look": inputs.look, "renderer": prep.renderer,
                    "data": {k: v.model_dump() for k, v in geodata.LICENCES.items()},
                    "credits": options["credits"]}
        manifest_path = outdir / f"{name}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return RenderOutput(video=str(video), srt=str(srt), manifest=str(manifest_path),
                            duration_ms=timeline.duration_ms)
```

Check `join_shots`'s signature in `reelmachine/recipes/explainer3d/video.py:14` before relying on the argument order above (tracks, joins, nominal ms, dest). Check `mix(...)`'s `profile` values (`grep -n "profile" reelmachine/recipes/explainer3d/audio.py`) and use the one explainer3d's default look uses.

- [ ] **Step 6: Register the stages in the recipe**

In `reelmachine/recipes/mapreel/recipe.py`:

```python
from ...core.recipe import CostNote, ProbeContext, ProbeReport, ProviderReq, RecipeSpec, StageSpec
from .stages import (ComposeStage, GeoStage, ImagesStage, NarrationStage, PrepStage, RenderStage,
                     ScriptStage, SegmentsStage)
```

and replace `stages=(),` with:

```python
        stages=(
            StageSpec(stage=ScriptStage()),
            StageSpec(stage=GeoStage(), feeds="script"),
            StageSpec(stage=ImagesStage(), feeds="geo"),
            StageSpec(stage=NarrationStage(), feeds="images", uses=("narration",)),
            StageSpec(stage=SegmentsStage(), feeds="narration"),
            StageSpec(stage=PrepStage(), feeds="segments", uses=("renderer",)),
            StageSpec(stage=ComposeStage(), feeds="prep"),
            StageSpec(stage=RenderStage(), feeds="compose"),
        ),
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_mapreel_engine.py -v`
Expected: all PASS. Common failures and fixes:
- `test_an_unknown_country_fails_with_a_suggestion`: check how `job.error` is shaped (`grep -n "class JobError\|error:" reelmachine/core/job.py`) and assert on its `hint` / `details`.
- Item order assertion fails because the fake voice words carry punctuation — `tokens()` strips it; confirm `spoken` holds the voice's own `WordSpan`s, not the shown words.

- [ ] **Step 8: Run the full fast suite to confirm explainer3d is untouched**

Run: `uv run pytest -q -m "not slow" && uv run pytest -q tests/test_explainer3d_engine.py`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add reelmachine/recipes/mapreel reelmachine/recipes/explainer3d/stages.py tests/test_mapreel_engine.py
git commit -m "mapreel: the pipeline — places, layers, images, voice, one project per segment, storyboard or reel

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Probe and the starter guide

**Files:**
- Modify: `reelmachine/recipes/mapreel/recipe.py`
- Test: `tests/test_mapreel_probe.py`

**Interfaces:**
- Consumes: `stages.resolve_segments`, `places.atlas_for`, `explainer3d.timing.estimate_speech_ms`, `explainer3d.timing.shot_windows`, `timing.MIN_SEGMENT_MS`, `doodle.narration.provider_for`.
- Produces: `MapReelRecipe.probe(inputs, ctx) -> ProbeReport` with checks `script`, `renderer`, `voice`; `details` keys `segments` (`[{"id", "codes", "estimatedMs"}]`), `estimatedMs`, `cost` (`{"ttsCharacters", "voice"}`), and on failure `error` (`{"path", "message", "hint"}`); `spec.guide` (`{"workflow": [...], "tips": [...]}`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mapreel_probe.py
import dataclasses
import shutil
from pathlib import Path

from reelmachine.config import get_settings
from reelmachine.core.recipe import ProbeContext
from reelmachine.recipes.mapreel import geodata
from reelmachine.recipes.mapreel.models import MapReelInputs
from reelmachine.recipes.mapreel.recipe import MapReelRecipe

FIXTURE = Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson"


class _Providers(dict):
    pass


class _Renderer:
    name = "fake"

    def missing(self):
        return []


class _Voice:
    name = "fake"


def _ctx(tmp_path):
    settings = dataclasses.replace(get_settings(), workdir=tmp_path / "w", shared_cache=tmp_path / "shared")
    root = geodata.geo_cache(settings)
    root.mkdir(parents=True)
    shutil.copy2(FIXTURE, root / geodata.FILES["countries"][0])
    return ProbeContext(config=settings, providers=_Providers(renderer=_Renderer(), narration=_Voice()))


def _probe(tmp_path, segments, **extra):
    inputs = MapReelInputs.model_validate({"segments": segments, **extra})
    return MapReelRecipe().probe(inputs, _ctx(tmp_path))


def test_a_good_script_probes_ok_with_estimates(tmp_path):
    report = _probe(tmp_path, [{"kind": "country", "country": "Italy", "narration": "Italy has Gucci and Prada.",
                                "items": [{"name": "Gucci"}, {"name": "Prada"}]}])
    assert report.ok, report.checks
    seg = report.details["segments"][0]
    assert seg["codes"] == ["ITA"] and seg["estimatedMs"] >= 2200
    assert report.details["cost"]["ttsCharacters"] == len("Italy has Gucci and Prada.")


def test_a_bad_place_fails_with_its_path_and_suggestion(tmp_path):
    report = _probe(tmp_path, [{"kind": "highlight", "countries": ["ITA", "Itally"], "narration": "x"}])
    assert not report.ok
    assert report.details["error"]["path"] == "segments[0].countries[1]"
    assert "Italy" in report.details["error"]["hint"]


def test_a_voice_the_deployment_cannot_serve_fails_the_voice_check(tmp_path):
    report = _probe(tmp_path, [{"kind": "country", "country": "ITA", "narration": "x", "items": [{"name": "a"}]}],
                    voice="cartesia:abc")
    voice = next(c for c in report.checks if c.name == "voice")
    assert not voice.ok and not report.ok


def test_the_guide_explains_the_workflow():
    guide = MapReelRecipe.spec.guide
    assert any("find_places" in step for step in guide["workflow"])
    assert any("hook" in step.lower() for step in guide["workflow"])
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mapreel_probe.py -v`
Expected: FAIL (probe returns an empty report; guide missing).

- [ ] **Step 3: Implement**

In `reelmachine/recipes/mapreel/recipe.py` add imports and the guide, and replace `probe`:

```python
from ...core.errors import InvalidInput
from ...core.manifest import CheckResult
from ..doodle.narration import provider_for
from ..explainer3d.markup import parse_markup, spoken_text
from ..explainer3d.timing import estimate_speech_ms, shot_windows
from .places import atlas_for
from .stages import resolve_segments
from .timing import MIN_SEGMENT_MS

GUIDE = {
    "workflow": [
        "1. Pick a topic people argue about in the comments (brands, dishes, inventions, bans) and research "
        "every fact you will say; keep the source URL.",
        "2. Write the hook first: segment 1 opens on a surprise, a number, a challenge or 'you' — never "
        "'X has…'. Tease a payoff you deliver at the end.",
        "3. Resolve places with find_places (codes like 'ITA'); one country per 'country' segment, 1-8 items, "
        "listed in the order the narration names them.",
        "4. Use a 'highlight' segment for the hook or a reveal (several countries at once with a legend).",
        "5. probe and fix every failed check; start_job with mode 'storyboard', look at storyboard.jpg, "
        "then start_job with mode 'build'.",
    ],
    "tips": [
        "Items pop when the voice says their name: write the name in the narration exactly, or give `say` "
        "for how it is pronounced and `at` (seconds) to force a time.",
        "Keep a new visual every second: 3-5 items per country segment, segments of 3-5 seconds.",
        "`image` takes a local file or an https URL; anything unusable shows as a bold text badge.",
    ],
}
```

Set `guide=GUIDE,` in the `RecipeSpec(...)`, then:

```python
    def probe(self, inputs: MapReelInputs, ctx: ProbeContext) -> ProbeReport:
        checks: list[CheckResult] = []
        details: dict[str, Any] = {}
        try:
            segments = resolve_segments(inputs, atlas_for(ctx.config))
            estimates = [estimate_speech_ms(spoken_text(parse_markup(s.narration)), inputs.language)
                         for s in inputs.segments]
            windows = shot_windows(estimates, min_ms=MIN_SEGMENT_MS)
            details["segments"] = [{"id": seg.id, "codes": seg.codes, "estimatedMs": w.duration_ms}
                                   for seg, w in zip(segments, windows)]
            details["estimatedMs"] = windows[-1].end_ms
            checks.append(CheckResult(name="script", ok=True, detail=f"{len(segments)} segments"))
        except InvalidInput as exc:
            path = exc.details.get("path", "")
            checks.append(CheckResult(name="script", ok=False, detail=f"{path}: {exc.message}"))
            details["error"] = {"path": path, "message": exc.message, "hint": exc.hint}
        details["cost"] = {"ttsCharacters": sum(len(spoken_text(parse_markup(s.narration))) for s in inputs.segments),
                           "voice": inputs.voice}
        providers = ctx.providers
        renderer = providers.get("renderer") if providers else None
        problems = renderer.missing() if renderer is not None else ["no renderer bound"]
        checks.append(CheckResult(name="renderer", ok=not problems,
                                  detail="; ".join(problems) or getattr(renderer, "name", "")))
        bound = getattr(providers.get("narration") if providers else None, "name", "")
        checks.append(CheckResult(name="voice", ok=provider_for(inputs.voice) == bound,
                                  detail=f"requested {provider_for(inputs.voice)!r}, deployment serves {bound!r}"))
        return ProbeReport(ok=all(c.ok for c in checks), checks=checks, details=details)
```

If `ProbeContext.config` is `None` when the engine probes (check `grep -n "ProbeContext(" reelmachine/engine/*.py`), fall back to `get_settings()` from `reelmachine.config`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mapreel_probe.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add reelmachine/recipes/mapreel/recipe.py tests/test_mapreel_probe.py
git commit -m "mapreel: probe resolves places and estimates each segment; a starter guide for the writing agent

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The `find_places` MCP tool

**Files:**
- Modify: `reelmachine/mcp/tools.py` (method after `find_assets`, ~line 437), `reelmachine/mcp/server.py` (description in `DESCRIPTIONS` ~line 117; registration after `find_models` ~line 226), `docs/MCP.md` (one section, and the tool count in the opening line)
- Test: `tests/test_mapreel_mcp.py`

**Interfaces:**
- Consumes: `places.atlas_for(settings)`, `Atlas.search`.
- Produces: `McpTools.find_places(ctx, query: str, limit: int = 8) -> {"schemaVersion", "query", "places": [{"code","name","level","ambiguous"}], "hint"}`.

- [ ] **Step 1: Write the failing test**

First look at how an existing MCP tool test builds `McpTools` and a context: `grep -n "McpTools(\|def _tools\|find_models" tests/test_mcp*.py | head`. Use the same fixture; the test below assumes a helper `_tools(settings)` that returns `(tools, ctx)` — adapt to the real one.

```python
# tests/test_mapreel_mcp.py
import dataclasses
import shutil
from pathlib import Path

from reelmachine.config import get_settings
from reelmachine.recipes.mapreel import geodata

FIXTURE = Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson"


def _tools(tmp_path):
    from reelmachine.engine import Engine
    from reelmachine.mcp.tools import McpTools

    settings = dataclasses.replace(get_settings(), workdir=tmp_path / "w", shared_cache=tmp_path / "shared")
    root = geodata.geo_cache(settings)
    root.mkdir(parents=True)
    shutil.copy2(FIXTURE, root / geodata.FILES["countries"][0])
    return McpTools(engine=Engine(settings)), None


def test_find_places_returns_codes_and_flags_ambiguity(tmp_path):
    tools, ctx = _tools(tmp_path)
    found = tools.find_places(ctx, "korea")
    assert found["schemaVersion"] == 1
    codes = {p["code"] for p in found["places"]}
    assert {"KOR", "PRK"} <= codes and any(p["ambiguous"] for p in found["places"])
    assert "country" in found["hint"]


def test_find_places_with_no_match_says_so(tmp_path):
    tools, ctx = _tools(tmp_path)
    found = tools.find_places(ctx, "zzzz")
    assert found["places"] == [] and "ISO" in found["hint"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mapreel_mcp.py -v`
Expected: FAIL with `AttributeError: 'McpTools' object has no attribute 'find_places'`

- [ ] **Step 3: Implement**

In `reelmachine/mcp/tools.py` after `find_assets`:

```python
    @_guard
    def find_places(self, ctx: Any, query: str, limit: int = 8) -> dict[str, Any]:
        """Countries for mapreel segments, by code or by any of their names."""
        from ..recipes.mapreel.places import atlas_for

        self._caller(ctx)
        found = atlas_for(self.engine.settings).search(query, limit=max(1, min(int(limit), 50)))
        return {
            "schemaVersion": SCHEMA_VERSION,
            "query": query,
            "places": found,
            "hint": (
                'use the code in a mapreel segment: {"kind": "country", "country": "<code>"} or '
                '{"kind": "highlight", "countries": ["<code>", ...]}; an ambiguous name needs its code'
                if found else "nothing matched; try the English name or an ISO 3166 alpha-3 code like 'ITA'"
            ),
        }
```

In `reelmachine/mcp/server.py`, add to `DESCRIPTIONS`:

```python
    "find_places": (
        "Find countries for a mapreel script by name or code ('Holland', 'Korea', 'ITA'). Returns ISO "
        "codes and flags names that mean more than one country. Fetches the map data once on first use."
    ),
```

and after the `find_models` registration:

```python
    @server.tool(name="find_places", description=DESCRIPTIONS["find_places"])
    def find_places(ctx: Context, query: str, limit: int = 8) -> dict[str, Any]:
        return tools.find_places(ctx, query, limit)
```

In `docs/MCP.md`, bump the tool count in the first sentence by one and add a section after `find_models` (or at the end of the tool list):

```markdown
### `find_places`

Countries for a `mapreel` script, by code or by any name (`Holland` → `NLD`). A name that
means more than one country (`Korea`) comes back with `ambiguous: true` on each candidate —
use the code. The Natural Earth data is fetched once on first use.

```json
{"query": "korea"}
```
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mapreel_mcp.py -v && uv run pytest -q tests/test_mcp*.py`
Expected: PASS (if an MCP test counts the tools, update its expected count).

- [ ] **Step 5: Commit**

```bash
git add reelmachine/mcp docs/MCP.md tests/test_mapreel_mcp.py
git commit -m "mcp: find_places — countries for a map reel by code or name, ambiguous names flagged

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: A real render, the reference example, and a look at it

**Files:**
- Create: `tests/test_mapreel_render.py`, `examples/mapreel/brands.json`
- Test: `tests/test_mapreel_render.py`

**Interfaces:**
- Consumes: the whole recipe with the real `hyperframes` renderer and real (downloaded) map data.

- [ ] **Step 1: Write the slow real-render test**

```python
# tests/test_mapreel_render.py
import dataclasses
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from reelmachine.config import get_settings
from reelmachine.core.job import JobRequest, JobState
from reelmachine.engine import Engine
from reelmachine.render.hyperframes import HyperframesRenderer

SETTINGS = get_settings()
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(bool(HyperframesRenderer(SETTINGS).missing()), reason="HyperFrames renderer not available"),
]
SCRIPT = {
    "segments": [
        {"kind": "highlight", "countries": ["USA", "ITA"], "legend": "Two giants",
         "narration": "Only two countries matter here."},
        {"kind": "country", "country": "ITA", "narration": "Italy has Gucci and Prada.",
         "items": [{"name": "Gucci"}, {"name": "Prada"}]},
    ],
}


def _build(tmp_path, inputs):
    settings = dataclasses.replace(SETTINGS, workdir=tmp_path / "work", outdir=tmp_path / "out", tts="fake")
    engine = Engine(settings)  # real map data in the shared cache: downloaded once on first run
    try:
        job = engine.submit(JobRequest(recipe="mapreel", inputs=inputs), caller="local")
        return engine.wait(job.id, caller="local", timeout_s=1800)
    finally:
        engine.close()


def _frame(video: Path, index: int) -> np.ndarray:
    out = subprocess.run([SETTINGS.ffmpeg, "-v", "error", "-i", str(video), "-vf", f"select=eq(n\\,{index})",
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(out, dtype=np.uint8).reshape(1920, 1080, 3).astype(np.int16)


def test_a_real_reel_moves_and_fills(tmp_path):
    job = _build(tmp_path, SCRIPT)
    assert job.state == JobState.SUCCEEDED, job.error
    reel = next(Path(a.path) for a in job.result.artifacts if a.name == "reel.mp4")
    first, later = _frame(reel, 0), _frame(reel, 45)
    assert np.abs(first - later).mean() > 3  # the camera and fills moved
    assert later.std() > 20                  # a map, not a blank canvas
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_mapreel_render.py -v -s`
Expected: PASS (first run downloads ~45 MB of map data into the shared cache). If frames are identical, HyperFrames is not delivering seeks to this runtime: open `work/**/projects/seg-1/index.html` with `npx hyperframes@0.8.146 preview` (see `reelmachine/render/hyperframes.py` for the exact CLI) and compare the seek wiring with `reelmachine/recipes/explainer3d/runtime/main.mjs`; fix in `main.mjs` / `compose.write_project` and add a regression assertion here.

- [ ] **Step 3: Write the reference example with a hook**

```json
{
  "title": "Brands you didn't know were from these countries",
  "mode": "storyboard",
  "voice": "fake:default",
  "segments": [
    {"kind": "highlight", "countries": ["USA", "ITA", "FRA", "JPN", "SWE"], "legend": "Who dresses the world?",
     "narration": "*Five countries* make almost everything you wear. Number five will surprise you."},
    {"kind": "country", "country": "USA", "narration": "The United States has Nike, Levi's, Calvin Klein and Ralph Lauren.",
     "items": [{"name": "Nike"}, {"name": "Levi's"}, {"name": "Calvin Klein"}, {"name": "Ralph Lauren"}]},
    {"kind": "country", "country": "ITA", "narration": "Italy has Gucci, Prada, Versace and Armani.",
     "items": [{"name": "Gucci"}, {"name": "Prada"}, {"name": "Versace"}, {"name": "Armani", "say": "Armani"}]},
    {"kind": "country", "country": "FRA", "narration": "France has Louis Vuitton, Chanel, Dior and Yves Saint Laurent.",
     "items": [{"name": "Louis Vuitton"}, {"name": "Chanel"}, {"name": "Dior"}, {"name": "YSL", "say": "Yves Saint Laurent"}]},
    {"kind": "country", "country": "JPN", "narration": "Japan has Uniqlo, Comme des Garçons and Onitsuka Tiger.",
     "items": [{"name": "Uniqlo"}, {"name": "Comme des Garçons", "say": "Comme des Garçons"}, {"name": "Onitsuka Tiger"}]},
    {"kind": "country", "country": "SWE", "narration": "And number five? *Sweden*. H&M, Acne Studios and Fjällräven.",
     "items": [{"name": "H&M"}, {"name": "Acne Studios"}, {"name": "Fjällräven", "emoji": "🦊"}]},
    {"kind": "highlight", "countries": ["USA", "ITA", "FRA", "JPN", "SWE"],
     "narration": "Which country did I miss? Tell me in the comments."}
  ]
}
```

Save it as `examples/mapreel/brands.json`.

- [ ] **Step 4: Render the example as a storyboard and a reel, and look at them**

```bash
uv run reel run mapreel examples/mapreel/brands.json            # check `uv run reel --help` for the exact subcommand
```

(If the CLI's run command differs, use the same Engine snippet as in the test with `inputs=json.load(open("examples/mapreel/brands.json"))`.) Open `storyboard.jpg` and read it: each still should show the satellite map, the country filled in its colour with the glowing edge, the ghosted name, and items inside the country. Then run again with `"mode": "build"` and watch `reel.mp4` start to end: the camera should swoop between countries, items should pop on their words, the caption should follow the voice. Note anything off (item overlap, fill alignment, legend clipping) and fix it in the runtime or `compose.py` before committing; each fix gets its own small test where one is possible (e.g. an anchor-spacing case in `test_mapreel_geometry.py`).

- [ ] **Step 5: Commit**

```bash
git add tests/test_mapreel_render.py examples/mapreel/brands.json reelmachine/recipes/mapreel
git commit -m "mapreel: a real HyperFrames render test and the brands reel, rewritten with a hook

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage (Plan 1 slice):** script model for `country`/`highlight` (Task 1); place resolution, aliases, ambiguity, `find_places` (Tasks 2, 10); geo data + framing + far territories + antimeridian (Tasks 2–3); satellite look (Task 4); auto colours (Task 4); spoken-name timing with `say`/`at` (Task 5); fly-to, fill + glow, ghosted name, pops, captions, legend (Task 6); storyboard + build, `script.resolved.json`, captions, manifest with data licences (Tasks 7–8); probe + starter guide (Task 9); a reference-style reel (Task 11). Deferred by the spec to Plans 2–3: `rank`/`versus`/`regions`, the eight-rule hook lint and full guide/topic bank, `find_images`/Commons, `sources.md`, `flat`/`dark` looks, `verify`, `docs/MAPREEL.md`.
- **Known deviation from the spec text:** mode `"build"` instead of `"full"`, and `voice` as a `"provider:voice"` string — both to match the repo's existing recipes.
- **Review Focus coverage:** far territories (Task 3 France/USA tests), antimeridian (Task 3 Russia/Fiji), brand spellings (Task 5 tokens/match tests), tiny/huge countries (Task 3 clamp test), unusable images (Task 8 engine test with `/nope.png`).
