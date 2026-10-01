"""Corpus selection: anime vs J-Drama, and the caps that keep a reel mixed.

No network: `Segment`/`Media` are constructed directly and the planner's
selection step is exercised on its own.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from reelmachine.config import get_settings
from reelmachine.models import Media, Segment
from reelmachine.reel import _category_of, select_segments


def _segment(public_id: str, media_id: str, *, ms: int = 4000, pos: int = 0) -> Segment:
    return Segment(
        publicId=public_id,
        position=pos,
        startTimeMs=pos * 10_000,
        endTimeMs=pos * 10_000 + ms,
        episode=1,
        mediaPublicId=media_id,
        textJa={"content": "約束"},
        textEn={"content": "promise"},
        textEs={"content": "promesa"},
        urls={
            "imageUrl": "https://x/i.jpg",
            "audioUrl": "https://x/a.mp3",
            "videoUrl": "https://x/v.mp4",
        },
    )


def _media(public_id: str, category: str, name: str) -> Media:
    return Media(publicId=public_id, slug=name.lower().replace(" ", "-"),
                 nameEn=name, category=category)


def _settings(**overrides):
    base = dataclasses.replace(get_settings(), target_ms=0, per_category=0)
    return dataclasses.replace(base, **overrides)


# --------------------------------------------------------------- category


def test_category_of_reads_the_media_not_the_segment() -> None:
    media = {"a": _media("a", "JDRAMA", "Some Drama"), "b": _media("b", "ANIME", "Some Anime")}
    assert _category_of(_segment("s1", "a"), media) == "JDRAMA"
    assert _category_of(_segment("s2", "b"), media) == "ANIME"
    # A segment whose media is missing must not crash the planner.
    assert _category_of(_segment("s3", "zzz"), media) == "ANIME"


def test_per_category_cap_makes_a_mixed_reel_actually_mix() -> None:
    """Anime outnumbers J-Drama ~15:1, so ranking alone yields an anime reel."""
    settings = _settings()
    anime = [_segment(f"a{i}", f"am{i}", pos=i) for i in range(10)]
    drama = [_segment(f"d{i}", f"dm{i}", pos=i) for i in range(10)]
    media = {f"am{i}": _media(f"am{i}", "ANIME", f"Anime {i}") for i in range(10)}
    media |= {f"dm{i}": _media(f"dm{i}", "JDRAMA", f"Drama {i}") for i in range(10)}

    # Candidates in rank order, anime first and far more numerous — which is
    # what the index actually returns (measured: 47 anime to 3 drama).
    ordered = anime + drama

    uncapped = select_segments(ordered, media, settings, max_segments=4, per_media=9)
    assert all(_category_of(s, media) == "ANIME" for s in uncapped), "rank alone is not a mix"

    capped = select_segments(ordered, media, settings, max_segments=4, per_category=2, per_media=9)
    kinds = [_category_of(s, media) for s in capped]
    assert kinds.count("ANIME") == 2 and kinds.count("JDRAMA") == 2


def test_per_media_still_caps_within_a_category() -> None:
    settings = _settings()
    media = {"m1": _media("m1", "JDRAMA", "One Drama")}
    segs = [_segment(f"s{i}", "m1", pos=i) for i in range(5)]
    picked = select_segments(segs, media, settings, max_segments=4, per_media=1, per_category=0)
    assert len(picked) == 1


def test_settings_cap_is_used_when_no_override_is_given() -> None:
    settings = _settings(per_category=1)
    media = {
        "a": _media("a", "ANIME", "A"),
        "d": _media("d", "JDRAMA", "D"),
    }
    segs = [_segment("s1", "a"), _segment("s2", "d")]
    picked = select_segments(segs, media, settings, max_segments=4, per_media=1)
    assert len(picked) == 2  # one each, because the setting caps at 1 per corpus


def test_max_segments_is_a_hard_ceiling() -> None:
    settings = _settings()
    media = {f"m{i}": _media(f"m{i}", "ANIME", f"A{i}") for i in range(20)}
    segs = [_segment(f"s{i}", f"m{i}", pos=i) for i in range(20)]
    assert len(select_segments(segs, media, settings, max_segments=3, per_media=1)) == 3


# ------------------------------------------------------------------ config


def test_default_categories_include_drama() -> None:
    """Searching only ANIME is a real filter — drama never appears by accident."""
    assert "JDRAMA" in get_settings().categories
    assert "ANIME" in get_settings().categories


def test_cli_category_aliases() -> None:
    from reelmachine.cli import _parse_categories

    assert _parse_categories(None) is None
    assert _parse_categories("") is None
    assert _parse_categories("anime") == ["ANIME"]
    assert _parse_categories("jdrama") == ["JDRAMA"]
    assert _parse_categories("drama") == ["JDRAMA"]        # alias
    assert _parse_categories("anime,jdrama") == ["ANIME", "JDRAMA"]
    assert _parse_categories("Anime, JDRAMA") == ["ANIME", "JDRAMA"]
    assert _parse_categories("anime,anime") == ["ANIME"]   # de-duplicated


def test_cli_category_rejects_nonsense() -> None:
    from reelmachine.cli import _parse_categories

    with pytest.raises(SystemExit):
        _parse_categories("korean-drama")


# ------------------------------------------------- the slots dataclass bug


def test_match_media_handles_a_title_that_already_has_a_tmdb_id() -> None:
    """`TmdbMatch` is a slots dataclass and has no `__dict__` to splat.

    Every title carrying `externalIds.tmdb` took this path — which is most
    J-Drama, since the two indexes agree on it — and raised AttributeError
    instead of matching.
    """
    from reelmachine.tmdb import TmdbClient, TmdbMatch, _Cache

    client = TmdbClient.__new__(TmdbClient)          # bypass __init__ (no settings)
    client.settings = None
    client.api_key = "test"
    client.timeout = 5
    client._cache = _Cache(Path("/dev/null"))
    client._cache.load()
    client.search = lambda query, kind: []           # nothing should be searched
    client._describe = lambda tmdb_id, kind: TmdbMatch(
        tmdb_id=tmdb_id, media_type=kind, season=1, title="The Makanai",
        year=2023, score=9.9, query=str(tmdb_id), reason="",
    )

    media = _media("m1", "JDRAMA", "The Makanai")
    media.externalIds.tmdb = "154916"
    match = client.match_media(media)
    assert match is not None
    assert match.tmdb_id == 154916
    assert match.reason == "externalIds.tmdb"
