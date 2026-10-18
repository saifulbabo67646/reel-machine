# tests/test_mapreel_basemap.py
import dataclasses
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from reelmachine.config import get_settings
from reelmachine.recipes.mapreel import basemap, geodata, palette
from reelmachine.recipes.mapreel.palette import PALETTE

FIXTURE = Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson"


def _settings(tmp_path):
    return dataclasses.replace(get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out")


def _seed_textures(settings, root):
    """Equirectangular stand-ins: left half red, right half blue; top row band green
    (the north pole), so the reprojection's orientation is checkable."""
    root.mkdir(parents=True, exist_ok=True)
    for key, (w, h) in {"world": (720, 360), "detail": (1440, 720)}.items():
        src = (f"color=red:size={w // 2}x{h}[l];color=blue:size={w // 2}x{h}[r];[l][r]hstack[m];"
               f"color=green:size={w}x{h // 18}[top];[m][top]overlay=0:0")
        subprocess.run([settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", src, "-frames:v", "1", "-pix_fmt", "yuvj444p",
                        str(root / geodata.FILES[key][0])], check=True)


def _rgb(settings, path):
    out = subprocess.run([settings.ffmpeg, "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    w, h = (int(v) for v in subprocess.run([settings.ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True).stdout.strip().split(","))
    return np.frombuffer(out, dtype=np.uint8).reshape(h, w, 3)


@pytest.mark.slow
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
    green = img[:, 1000, 1] > 64  # (0,128,0) halfway into the red below it
    boundary = int(np.argmin(green))  # first non-green row; Mercator y of 80° × 2048 ≈ 230, linear would be ≈ 114
    assert abs(boundary - 230) <= 5


@pytest.mark.slow
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


@pytest.mark.slow
def test_detail_layer_wraps_across_the_antimeridian(tmp_path):
    settings = _settings(tmp_path)
    root = tmp_path / "geo"
    _seed_textures(settings, root)
    path, actual = basemap.detail_layer(settings, root, (0.95, 0.30, 1.05, 0.40))
    img = _rgb(settings, path)
    mid = img.shape[0] // 2
    assert img[mid, 5, 2] > 150 and img[mid, -5, 0] > 150  # east (blue) on the left, west (red) on the right


@pytest.mark.slow
def test_two_threads_building_different_layers_at_once_each_get_their_own(tmp_path):
    """Jobs run in threads of one process and share the geo cache: no scratch file may be shared."""
    from concurrent.futures import ThreadPoolExecutor

    settings = _settings(tmp_path)
    root = tmp_path / "geo"
    _seed_textures(settings, root)
    west, east = (0.10, 0.30, 0.30, 0.50), (0.70, 0.30, 0.90, 0.50)  # all red, all blue
    for _ in range(3):  # a few rounds, each from an empty layer cache
        shutil.rmtree(root / "layers", ignore_errors=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            (west_path, _), (east_path, _) = pool.map(lambda b: basemap.detail_layer(settings, root, b),
                                                      [west, east])
        red, blue = _rgb(settings, west_path), _rgb(settings, east_path)
        assert red[..., 0].mean() > 150 and red[..., 2].mean() < 100
        assert blue[..., 2].mean() > 150 and blue[..., 0].mean() < 100
    layers = root / "layers"
    assert not list(layers.rglob("*.rgb")) and not list(layers.rglob("*.partial*"))
    assert not any((layers / "scratch").iterdir())


def test_palette_keeps_neighbours_and_cuts_distinct():
    colours = palette.assign([["USA"], ["GBR"], ["ITA", "FRA", "DEU"], ["FRA"]])
    assert len({colours["ITA"], colours["FRA"], colours["DEU"]}) == 3
    assert colours["USA"] != colours["GBR"]
    assert all(c.startswith("#") and len(c) == 7 for c in colours.values())


def test_palette_respects_fixed_colours():
    assert palette.assign([["ITA"]], fixed={"ITA": "#123456"})["ITA"] == "#123456"


def test_palette_reserves_fixed_colours_for_neighbouring_groups():
    colours = palette.assign([["FRA"], ["ITA"]], fixed={"ITA": PALETTE[0]})
    assert colours["ITA"] == PALETTE[0] and colours["FRA"] != colours["ITA"]


def test_palette_reappearing_code_keeps_colour_and_is_avoided_by_neighbours():
    colours = palette.assign([["FRA"], ["DEU"], ["FRA", "ITA"]])
    assert colours["DEU"] != colours["FRA"] and colours["ITA"] != colours["FRA"]
    colours = palette.assign([["FRA", "DEU"], ["ITA"], ["FRA"]])
    assert colours["ITA"] not in (colours["FRA"], colours["DEU"])


def test_sixty_countries_get_sixty_colours():
    codes = [f"C{i:02d}" for i in range(60)]
    colours = palette.assign([codes])
    assert len(set(colours.values())) == 60
    assert all(c.startswith("#") and len(c) == 7 for c in colours.values())


# -- the flat and dark looks: drawn from Natural Earth, no texture (and no ffmpeg) --------------------------


def _countries(root):
    """The fixture's countries as the cached admin-0 file; no satellite texture anywhere."""
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURE, root / geodata.FILES["countries"][0])
    return root


def _pixels(path):
    from PIL import Image

    return np.asarray(Image.open(path).convert("RGB")).astype(int)


def _hex(colour):
    return np.array([int(colour[i:i + 2], 16) for i in (1, 3, 5)])


def _at(img, lon, lat, x0=0.0, y0=0.0, x1=1.0, y1=1.0):
    from reelmachine.recipes.mapreel.geometry import merc

    x, y = merc(lon, lat)
    h, w = img.shape[:2]
    return img[int((float(y) - y0) / (y1 - y0) * h), int((float(x) - x0) / (x1 - x0) * w)]


def _near(pixel, colour, tol=12):
    return int(np.abs(pixel - _hex(colour)).max()) <= tol


@pytest.mark.parametrize("look, sea, land", [("flat", "#a8d8f0", "#f3efe4"), ("dark", "#070b14", "#1a2233")])
def test_flat_world_layer_colours_land_and_sea(tmp_path, look, sea, land):
    root = _countries(tmp_path / "geo")
    path = basemap.world_layer(None, root, look=look)
    img = _pixels(path)
    assert img.shape == (basemap.WORLD_PX, basemap.WORLD_PX, 3)  # the satellite world's pixel geometry
    # the fixture has no Sahara: South Africa's Karoo is its desert; the mid-Pacific is open sea
    assert _near(_at(img, 22.0, -31.0), land), _at(img, 22.0, -31.0)
    assert _near(_at(img, 45.0, 60.0), land)  # Russia west of the Urals
    assert _near(_at(img, -150.0, 0.0), sea), _at(img, -150.0, 0.0)
    assert _near(img[0, 1000], sea) and _near(img[-1, 1000], sea)  # the polar rows: no country in the fixture
    assert not (root / geodata.FILES["world"][0]).exists()  # no Blue Marble fetched


def test_flat_borders_are_drawn_between_neighbours(tmp_path):
    root = _countries(tmp_path / "geo")
    img = _pixels(basemap.world_layer(None, root, look="flat"))
    from reelmachine.recipes.mapreel.geometry import merc

    # along the Norway-Sweden border's latitude, a border-coloured pixel separates the two lands
    x0, y = (float(v) for v in merc(10.0, 63.0))
    x1, _ = (float(v) for v in merc(16.0, 63.0))
    row = img[int(y * img.shape[0]), int(x0 * img.shape[1]):int(x1 * img.shape[1])]
    border, land = _hex("#9a948a"), _hex("#f3efe4")
    assert (np.abs(row - border).max(axis=1) < 30).any()
    assert (np.abs(row - land).max(axis=1) < 12).sum() > len(row) // 2


def test_vector_layer_has_no_antimeridian_streak(tmp_path):
    root = _countries(tmp_path / "geo")
    img = _pixels(basemap.world_layer(None, root, look="flat"))
    from reelmachine.recipes.mapreel.geometry import merc

    sea = _hex("#a8d8f0")
    for lat in (-16.5, -17.0, -18.0):  # Fiji's latitude: its islands sit both sides of 180°
        _, y = merc(0.0, lat)
        row = img[int(float(y) * img.shape[0])]
        west, east = (int(float(merc(lon, 0)[0]) * img.shape[1]) for lon in (-175.0, 175.0))
        away = row[west:east]  # every column at least 5° from Fiji
        assert (np.abs(away - sea).max(axis=1) <= 12).all(), lat
        assert not (np.abs(row - sea).max(axis=1) <= 12).all()  # Fiji itself is drawn
    # Russia's far east (Chukotka) runs past 180°: land either side of the seam, with no border drawn down
    # the cut, and sea over the Atlantic on the same row
    _, y = merc(0.0, 67.0)
    row = img[int(float(y) * img.shape[0])]
    assert _near(_at(img, -177.0, 67.0), "#f3efe4") and _near(_at(img, 175.0, 67.0), "#f3efe4")
    assert _near(row[1], "#f3efe4") and _near(row[-2], "#f3efe4")
    assert _near(_at(img, -30.0, 67.0), "#a8d8f0")


def test_a_ring_that_jumps_the_antimeridian_is_drawn_both_sides_with_no_streak(tmp_path):
    import json

    root = tmp_path / "geo"
    root.mkdir()
    ring = [[170, -40], [-170, -40], [-170, -45], [170, -45], [170, -40]]  # 20° wide across 180°
    data = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"ADM0_A3": "XXX"},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}]}
    (root / geodata.FILES["countries"][0]).write_text(json.dumps(data))
    img = _pixels(basemap.world_layer(None, root, look="dark"))
    assert _near(_at(img, 175.0, -42.5), "#1a2233") and _near(_at(img, -175.0, -42.5), "#1a2233")
    assert _near(_at(img, 0.0, -42.5), "#070b14") and _near(_at(img, -100.0, -42.5), "#070b14")


def test_a_vector_detail_layer_has_the_satellite_geometry_and_wraps(tmp_path):
    root = _countries(tmp_path / "geo")
    bbox = (0.40, 0.30, 0.55, 0.45)
    path, actual = basemap.detail_layer(None, root, bbox, look="flat")
    assert actual == pytest.approx(bbox)
    img = _pixels(path)
    # what the satellite crop of the 21600-px texture would be: 0.15 × 21600 = 3240 px, capped at 2160
    assert img.shape == (basemap.DETAIL_MAX_PX, basemap.DETAIL_MAX_PX, 3)
    # Fiji straddles 180°: a crop across it has Fiji's land either side of the seam
    from reelmachine.recipes.mapreel.geometry import merc

    _, fy = merc(0.0, -17.8)
    box = (0.995, float(fy) - 0.004, 1.005, float(fy) + 0.004)
    path, _ = basemap.detail_layer(None, root, box, look="flat")
    img = _pixels(path)
    land = (np.abs(img - _hex("#f3efe4")).max(axis=2) <= 12)
    half = img.shape[1] // 2
    assert land[:, :half].any() and land[:, half:].any()


def test_look_is_part_of_the_layer_cache_key(tmp_path):
    root = _countries(tmp_path / "geo")
    bbox = (0.40, 0.30, 0.55, 0.45)
    flat, _ = basemap.detail_layer(None, root, bbox, look="flat")
    dark, _ = basemap.detail_layer(None, root, bbox, look="dark")
    assert flat != dark and flat.is_file() and dark.is_file()
    assert basemap.world_layer(None, root, look="flat") != basemap.world_layer(None, root, look="dark")
    # satellite names carry SATELLITE_REV (the seam fade moved them on: see the seam tests below)
    assert basemap.layer_name("satellite", None) == f"world-v{basemap.SATELLITE_REV}-{basemap.WORLD_PX}.jpg"
    assert basemap.layer_name("flat", None) != basemap.layer_name("satellite", None)
    assert basemap.layer_name("satellite", bbox) == basemap.layer_name("satellite", tuple(bbox))
    names = {look: basemap.layer_name(look, bbox) for look in ("satellite", "flat", "dark")}
    assert len(set(names.values())) == 3
    again, _ = basemap.detail_layer(None, root, bbox, look="flat")
    assert again == flat


# -- the Blue Marble seam: NASA's texture has a hard edge at 80° N; the reprojection cross-fades it ---------


def _fake_source(monkeypatch, src: np.ndarray):
    """`_mercator` over an in-memory equirectangular source: no ffmpeg."""
    sh, sw = src.shape[:2]
    monkeypatch.setattr(basemap.ffmpeg, "probe", lambda _path: type("Info", (), {"width": sw, "height": sh})())

    def raw(_settings, _src, *, x, y, w, h, out_w, out):
        crop = src[y:y + h, x:x + w]
        idx = np.linspace(0, crop.shape[1] - 1, out_w).round().astype(int)
        return crop[:, idx].copy()

    monkeypatch.setattr(basemap, "_raw", raw)


def _hard_edge(sw=64, sh=2700, north=200, south=40):
    rows = np.where(np.arange(sh) < round((90 - basemap.SEAM_LAT) / 180 * sh), north, south)
    return np.repeat(rows[:, None, None], sw, axis=1).repeat(3, axis=2).astype(np.uint8)


def test_the_seam_latitude_is_cross_faded_over_twelve_source_rows_each_side(monkeypatch, tmp_path):
    from reelmachine.recipes.mapreel.geometry import merc

    _fake_source(monkeypatch, _hard_edge())
    _, y0 = merc(0.0, 84.0)
    _, y1 = merc(0.0, 76.0)
    out = basemap._mercator(None, Path("bm.jpg"), -10.0, 10.0, float(y0), float(y1), 32, 800, tmp_path)
    col = out[:, 16, 0].astype(int)
    lats = basemap.lat_of(y0 + (np.arange(800) + 0.5) / 800 * (y1 - y0))
    band = 12 * 180 / 2700  # ±12 source rows, in degrees
    assert (col[lats > basemap.SEAM_LAT + band + 0.1] == 200).all()  # north of the band: untouched
    assert (col[lats < basemap.SEAM_LAT - band - 0.1] == 40).all()   # south of it: untouched
    assert np.abs(np.diff(col)).max() <= 12  # no hard step anywhere: 160 spread over the band
    mid = col[np.argmin(np.abs(lats - basemap.SEAM_LAT))]
    assert abs(mid - 120) <= 12  # half and half at the seam


def test_a_crop_that_ends_inside_the_band_still_fades(monkeypatch, tmp_path):
    """The crop's own rows stop at the seam; the fade reads the rows beyond it anyway."""
    from reelmachine.recipes.mapreel.geometry import merc

    _fake_source(monkeypatch, _hard_edge())
    _, y0 = merc(0.0, 84.0)
    _, y1 = merc(0.0, basemap.SEAM_LAT - 0.05)
    out = basemap._mercator(None, Path("bm.jpg"), -10.0, 10.0, float(y0), float(y1), 32, 400, tmp_path)
    col = out[:, 16, 0].astype(int)
    assert col[-1] > 100 and np.abs(np.diff(col)).max() <= 12


def test_the_seam_fade_keeps_the_texture_on_each_side(monkeypatch, tmp_path):
    """Mirrored, not smeared: stripes north of the seam still alternate inside the band."""
    from reelmachine.recipes.mapreel.geometry import merc

    src = _hard_edge(sh=2700, north=0, south=0)
    seam = round((90 - basemap.SEAM_LAT) / 180 * 2700)
    src[:seam, ::2] = 255  # vertical stripes in the north, flat black in the south
    _fake_source(monkeypatch, src)
    _, y0 = merc(0.0, 82.0)
    _, y1 = merc(0.0, 78.0)
    out = basemap._mercator(None, Path("bm.jpg"), -180.0, 180.0, float(y0), float(y1), 64, 600, tmp_path)
    lats = basemap.lat_of(y0 + (np.arange(600) + 0.5) / 600 * (y1 - y0))
    row = out[np.argmin(np.abs(lats - (basemap.SEAM_LAT + 0.2))), :, 0].astype(int)
    assert row[::2].mean() - row[1::2].mean() > 100  # the stripes survive just north of the seam


def test_satellite_layers_carry_a_revision_so_seamed_caches_are_not_reused():
    """Every satellite layer cached before the seam fade has the seam; the names moved on."""
    bbox = (0.40, 0.30, 0.55, 0.45)
    assert basemap.layer_name("satellite", None) == f"world-v{basemap.SATELLITE_REV}-{basemap.WORLD_PX}.jpg"
    assert basemap.layer_name("satellite", None) != f"world-{basemap.WORLD_PX}.jpg"
    import hashlib
    old = f"detail-{hashlib.sha256(repr(tuple(round(v, 5) for v in bbox)).encode()).hexdigest()[:16]}.jpg"
    assert basemap.layer_name("satellite", bbox) != old
