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
            assert not g._boxes_overlap(a, b, *g._legacy_extent(size))


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


FOOD = [{"name": "Live maggots", "emoji": "🪱"}, {"name": "Casu martzu", "emoji": "🧀"},
        {"name": "Dangerous", "emoji": "⚠️"}, {"name": "Love it", "emoji": "❤️"}]


@pytest.mark.parametrize("code,n", [("GBR", 4), ("FRA", 4), ("USA", 4), ("NOR", 3), ("ITA", 4)])
def test_every_badge_as_drawn_stays_off_the_caption_band(code, n):
    """Captions sit at 71 % of the height: no badge, emoji over plate and risen as it holds, reaches the band.
    Britain's four food badges put "Love it" on the caption word before."""
    from reelmachine.recipes.mapreel.kinds import RANK_SAFE

    items = FOOD[:n]
    shape = g.shape_of(ATLAS.resolve(code))
    y0, y1 = g.CAPTION_BAND
    assert y0 == pytest.approx(0.71 * 1920 - 43) and y1 == pytest.approx(0.71 * 1920 + 43)
    for safe in (g.SAFE, RANK_SAFE):
        frame = g.frame_for(shape.main, safe=safe)
        rings = [g.to_screen(frame, r) for r in shape.rings]
        anchors, size = g.item_layout(rings, n, safe=safe, extent=lambda px: g.badge_extent(items, px))
        _, hh = g.badge_extent(items, size)
        for _, y in anchors:
            assert y + hh <= y0 or y - hh - g.BADGE_RISE >= y1, (code, safe, y, size)


def test_a_badge_extent_is_its_emoji_over_its_plate():
    """runtime/pure.mjs badgeLayout pins the same numbers: size 200, a 48 px font."""
    two = {"name": "Dried fish", "emoji": "🐟"}
    assert g.badge_lines("Dried fish") == ["Dried", "fish"] and g.badge_lines("Natto") == ["Natto"]
    assert g.badge_extent([two], 200)[1] == pytest.approx(99.8)
    assert g.badge_extent([{"name": "Natto", "emoji": "🫘"}], 200)[1] == pytest.approx(74.6)
    assert g.badge_extent([{"name": "Natto"}], 200)[1] == pytest.approx(37.2)
    assert g.badge_extent([{"name": "x", "image": "a.png"}], 200) == pytest.approx((100, 62))
    # the widest name sets the width; a long name is capped where the runtime shrinks its font
    hw, _ = g.badge_extent([{"name": "Dried fish"}, {"name": "Natto"}], 200)  # "Dried" over "fish"
    assert hw == pytest.approx((0.8 * 48 * 5 + 0.9 * 48) / 2)
    assert g.badge_extent([{"name": "Supercalifragilistic"}], 200)[0] == pytest.approx((1.1 * 200 + 0.9 * 48) / 2)


@pytest.mark.parametrize("code,items", [("NOR", FOOD[:3]), ("GBR", FOOD), ("FRA", FOOD), ("JPN", FOOD[:2]),
                                        ("ITA", FOOD)])
def test_whole_badges_never_touch(code, items):
    """Items are spaced by the whole badge, emoji and plate: a lower badge's emoji never touches the plate
    above it (Norway's Lye under Dried fish, before)."""
    from reelmachine.recipes.mapreel.kinds import RANK_SAFE

    shape = g.shape_of(ATLAS.resolve(code))
    frame = g.frame_for(shape.main, safe=RANK_SAFE)
    rings = [g.to_screen(frame, r) for r in shape.rings]
    anchors, size = g.item_layout(rings, len(items), safe=RANK_SAFE, extent=lambda px: g.badge_extent(items, px))
    hw, hh = g.badge_extent(items, size)
    hw, hh = hw + g.BADGE_AIR, hh + g.BADGE_AIR  # with air between: plates never touch edge to edge
    for i, a in enumerate(anchors):
        for b in anchors[i + 1:]:
            assert abs(a[0] - b[0]) >= 2 * hw or abs(a[1] - b[1]) >= 2 * hh, (code, a, b, size)


# -- main-landmass framing per country: the camera frames the body a viewer knows as the country -------------

#: each country's main body (lon0, lat0, lon1, lat1): what its frame must hold whole
MAIN_BODY = {
    "IDN": (95.0, -11.0, 141.0, 6.0),     # Sumatra to New Guinea's west half, the archipelago as one
    "PHL": (117.0, 5.0, 127.0, 19.0),     # Luzon to Mindanao, Palawan included
    "GRC": (19.5, 36.4, 26.6, 41.7),      # the mainland and the Peloponnese
    "NOR": (4.6, 58.0, 31.0, 71.1),       # the mainland: Svalbard (77-80° N) is not the body
}


@pytest.mark.parametrize("code", sorted(MAIN_BODY))
def test_a_country_frame_holds_its_main_body_and_fills_the_view(code):
    from reelmachine.recipes.mapreel import compose

    frame = compose.segment_frame([g.shape_of(ATLAS.resolve(code))], 1.0)
    lon0, lat0, lon1, lat1 = MAIN_BODY[code]
    x, y = g.merc(np.array([lon0, lon1]), np.array([lat1, lat0]))  # top left, bottom right
    (left, top), (right, bottom) = g.to_screen(frame, np.column_stack([x, y]))
    assert left >= 0 and top >= 0 and right <= g.WIDTH and bottom <= g.HEIGHT, (left, top, right, bottom)
    short = min(g.WIDTH, g.HEIGHT)
    assert max(right - left, bottom - top) >= 0.35 * short, (right - left, bottom - top)
