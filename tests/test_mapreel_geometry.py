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
