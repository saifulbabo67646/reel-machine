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
    assert min(gaps) >= 2 * g.BADGE_HALF_H * g.BADGE_MIN  # at least a badge's height apart


def test_item_anchors_fall_back_to_a_column_for_a_sliver():
    sliver = [np.array([[500.0, 900.0], [520.0, 900.0], [520.0, 920.0], [500.0, 920.0]])]
    anchors = g.item_anchors(sliver, 3)
    assert len(anchors) == 3 and len({round(y) for _, y in anchors}) == 3


@pytest.mark.parametrize("code,n", [("FRA", 4), ("FRA", 3), ("USA", 4), ("ITA", 1)])
def test_every_badge_box_lies_inside_the_place_and_no_two_overlap(code, n):
    """A badge is up to size*1.1 wide (draw.mjs) and about size*0.6 tall: every corner and edge
    midpoint of that box is inside the place, and the boxes are pairwise disjoint."""
    shape = g.shape_of(ATLAS.resolve(code))
    frame = g.frame_for(shape.main)
    rings = [g.to_screen(frame, r) for r in shape.rings]
    anchors, size = g.item_layout(rings, n)
    assert len(anchors) == n and g.BADGE_MIN <= size <= g.BADGE_MAX
    hw, hh = g.BADGE_HALF_W * size, g.BADGE_HALF_H * size
    for x, y in anchors:
        for fx in (-1, 0, 1):
            for fy in (-1, 0, 1):
                assert g.inside(rings, x + fx * hw, y + fy * hh), (code, x, y, fx, fy)
    for i, a in enumerate(anchors):
        for b in anchors[i + 1:]:
            assert abs(a[0] - b[0]) >= 2 * hw or abs(a[1] - b[1]) >= 2 * hh, (code, a, b)


def test_a_badge_shrinks_before_it_overhangs():
    """A place too narrow for the largest badge gets a smaller one, not an overhanging one."""
    strip = [np.array([[300.0, 300.0], [560.0, 300.0], [560.0, 1500.0], [300.0, 1500.0]])]  # 260 px wide
    anchors, size = g.item_layout(strip, 3)
    assert size * 2 * g.BADGE_HALF_W <= 260 and size >= g.BADGE_MIN
    assert all(300 <= x - g.BADGE_HALF_W * size and x + g.BADGE_HALF_W * size <= 560 for x, _ in anchors)


@pytest.mark.parametrize("code", ["ITA", "JPN"])
def test_a_place_too_narrow_for_whole_badges_gets_disjoint_badges_centred_on_it(code):
    """Italy's boot cannot hold two whole 180-px badges at its framing: badges stay at the floor
    size, each anchored on the place, still never overlapping one another."""
    shape = g.shape_of(ATLAS.resolve(code))
    frame = g.frame_for(shape.main)
    rings = [g.to_screen(frame, r) for r in shape.rings]
    anchors, size = g.item_layout(rings, 4)
    assert size == g.BADGE_MIN and len(anchors) == 4
    assert all(g.inside(rings, x, y) for x, y in anchors)
    for i, a in enumerate(anchors):
        for b in anchors[i + 1:]:
            assert not g._boxes_overlap(a, b, size)


def test_a_world_frame_never_shows_the_top_edge():
    frame = g.frame_for((0.1, 0.1, 0.3, 0.2))
    assert frame.s >= g.HEIGHT
    vb = g.visible_bbox(frame)
    assert vb[1] >= 0 and vb[3] <= 1


def test_a_country_frame_is_unchanged():
    from reelmachine.recipes.mapreel import compose
    frame = compose.segment_frame([g.shape_of(ATLAS.resolve("ITA"))], 1.0)
    # the Plan 1 frame, pinned before world frames learned to fill the height
    assert (frame.x, frame.y, frame.s) == pytest.approx((0.5348872944444445, 0.3744050904251254, 27413.84572459445))


def test_frames_too_wide_for_the_height_still_fit():
    bbox = (0.15, 0.4, 0.85, 0.45)
    frame = g.frame_for(bbox)
    assert g.WIDTH <= frame.s < g.HEIGHT
    vb = g.visible_bbox(frame)
    assert vb[0] <= bbox[0] and vb[2] >= bbox[2]


def test_clamp_frame_centres_a_world_shorter_than_the_view():
    frame = g.clamp_frame(g.Frame(0.3, 0.2, 1200.0))
    assert frame == g.Frame(0.3, 0.5, 1200.0)


def test_frame_for_frames_inside_a_panel_view():
    frame = g.frame_for((0.5, 0.3, 0.52, 0.32), view=(1080, 960), safe=(0.0, 0.0, 1.0, 1.0))
    assert frame.s == pytest.approx(min(1080 / 0.02, 960 / 0.02))
    assert (frame.x, frame.y) == pytest.approx((0.51, 0.31))


@pytest.mark.parametrize("code,n", [("GBR", 4), ("FRA", 4), ("USA", 4), ("NOR", 3), ("ITA", 4)])
def test_every_badge_as_drawn_stays_off_the_caption_band(code, n):
    """Captions sit at 71 % of the height: a badge's emoji (above its anchor) and its text (below) never
    reach the band. Britain's four food badges put "Love it" on the caption word before."""
    from reelmachine.recipes.mapreel.kinds import RANK_SAFE

    shape = g.shape_of(ATLAS.resolve(code))
    for safe in (g.SAFE, RANK_SAFE):
        frame = g.frame_for(shape.main, safe=safe)
        rings = [g.to_screen(frame, r) for r in shape.rings]
        anchors, size = g.item_layout(rings, n, safe=safe)
        y0, y1 = g.CAPTION_BAND
        assert y0 == pytest.approx(0.71 * 1920 - 43) and y1 == pytest.approx(0.71 * 1920 + 43)
        for _, y in anchors:
            assert y + g.BADGE_DOWN * size <= y0 or y - g.BADGE_UP * size >= y1, (code, safe, y, size)
