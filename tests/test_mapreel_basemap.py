# tests/test_mapreel_basemap.py
import dataclasses
import subprocess

import numpy as np
import pytest

from reelmachine.config import get_settings
from reelmachine.recipes.mapreel import basemap, geodata, palette
from reelmachine.recipes.mapreel.palette import PALETTE

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
        subprocess.run([settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", src, "-frames:v", "1", "-pix_fmt", "yuvj444p",
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
    green = img[:, 1000, 1] > 64  # (0,128,0) halfway into the red below it
    boundary = int(np.argmin(green))  # first non-green row; Mercator y of 80° × 2048 ≈ 230, linear would be ≈ 114
    assert abs(boundary - 230) <= 5


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


def test_palette_reserves_fixed_colours_for_neighbouring_groups():
    colours = palette.assign([["FRA"], ["ITA"]], fixed={"ITA": PALETTE[0]})
    assert colours["ITA"] == PALETTE[0] and colours["FRA"] != colours["ITA"]


def test_palette_reappearing_code_keeps_colour_and_is_avoided_by_neighbours():
    colours = palette.assign([["FRA"], ["DEU"], ["FRA", "ITA"]])
    assert colours["DEU"] != colours["FRA"] and colours["ITA"] != colours["FRA"]
    colours = palette.assign([["FRA", "DEU"], ["ITA"], ["FRA"]])
    assert colours["ITA"] not in (colours["FRA"], colours["DEU"])
