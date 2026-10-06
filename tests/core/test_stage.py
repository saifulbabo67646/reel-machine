"""Stage cache keys and resumable outputs."""

from __future__ import annotations

from reelmachine.core.stage import StageCache


def test_cache_key_is_stable_for_identical_inputs() -> None:
    payload = {"word": "彼女", "count": 5}
    first = StageCache.make_key("select", 1, payload, config_subset={"aspect": "vertical"})
    second = StageCache.make_key("select", 1, dict(payload), config_subset={"aspect": "vertical"})
    assert first == second


def test_cache_key_changes_with_payload_revision_config_and_providers() -> None:
    base = StageCache.make_key("select", 1, {"word": "x"})
    assert StageCache.make_key("select", 1, {"word": "y"}) != base
    assert StageCache.make_key("select", 2, {"word": "x"}) != base
    assert StageCache.make_key("select", 1, {"word": "x"}, config_subset={"crf": 18}) != base
    assert StageCache.make_key("select", 1, {"word": "x"}, provider_pins={"corpus": "fake"}) != base


def test_cache_round_trips_outputs(tmp_path) -> None:
    cache = StageCache(tmp_path / "cache")
    key = StageCache.make_key("compose", 1, {"a": 1})
    assert cache.load(key) is None
    digest = cache.save(key, {"timeline": "…", "count": 2})
    assert cache.load(key) == {"timeline": "…", "count": 2}
    assert len(digest) == 64


def test_cache_key_is_json_safe_for_non_json_payloads() -> None:
    key = StageCache.make_key("compose", 1, {"path": __import__("pathlib").Path("/tmp/x")})
    assert len(key) == 64
