"""Providers are discovered through entry points; first-party is not special."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from reelmachine.config import get_settings
from reelmachine.core.registry import Registry
from reelmachine.core.style import load_styles
from reelmachine.engine.providers import provider_health, resolve_recipe_providers
from reelmachine.sources import SourceError, get_provider
from reelmachine.sources.base import resolve_source_name
from reelmachine.sources.mock import MockProvider
from reelmachine.storage import InMemoryStorage, normalise_key
from reelmachine.storage.local import LocalStorage
from reelmachine.storage.s3 import S3Storage


def _settings(**overrides):
    return dataclasses.replace(get_settings(), **overrides)


# ------------------------------------------------------------------ discovery


def test_builtins_are_discoverable_through_entry_points() -> None:
    registry = Registry()
    assert {"local", "hls", "vidvault", "mock"} <= set(registry.names("sources"))
    assert {"nadeshiko", "nadeshiko-fake"} <= set(registry.names("corpora"))
    assert "nadeshiko-cut" in registry.names("recipes")
    assert {"local", "memory", "s3"} <= set(registry.names("storage"))


def test_get_provider_resolves_through_the_registry() -> None:
    provider = get_provider("mock")
    assert isinstance(provider, MockProvider)
    assert provider.name == "mock"


def test_source_aliases_and_unknown_names() -> None:
    assert resolve_source_name("m3u8") == "hls"
    assert resolve_source_name("stream") == "hls"
    assert resolve_source_name("vv") == "vidvault"
    assert resolve_source_name("vid") == "vidvault"
    with pytest.raises(SourceError):
        get_provider("definitely-not-a-source")


def test_provider_health_reports_missing_requirements(tmp_path) -> None:
    settings = _settings(local_root=None, hls_template="", tmdb_api_key="")
    rows = provider_health(Registry(), settings)
    by_name = {(row["group"], row["name"]): row for row in rows}

    assert by_name[("sources", "local")]["status"] == "missing"
    assert "REEL_LOCAL_ROOT" in by_name[("sources", "local")]["detail"]
    assert by_name[("sources", "hls")]["status"] == "missing"
    assert by_name[("sources", "vidvault")]["status"] == "missing"
    assert by_name[("sources", "mock")]["status"] == "ok"
    assert by_name[("recipes", "nadeshiko-cut")]["status"] == "ok"


def test_disabled_providers_are_reported_not_hidden(monkeypatch) -> None:
    monkeypatch.setenv("REEL_PROVIDERS_DISABLED", "hls")
    rows = provider_health(Registry(), _settings())
    hls = next(row for row in rows if row["name"] == "hls")
    assert hls["status"] == "disabled"


def test_recipe_roles_resolve_to_live_providers(tmp_path) -> None:
    from reelmachine.recipes.nadeshiko_cut.recipe import NadeshikoCutRecipe

    settings = _settings(source="mock", workdir=tmp_path / "w")
    with resolve_recipe_providers(NadeshikoCutRecipe().spec, settings) as providers:
        assert providers.pin("source") == "mock"
        # a provider's version is part of the pin, so a parsing fix invalidates the
        # stage outputs it produced instead of serving them from the cache
        assert providers.pin("corpus") == "nadeshiko:1"
        assert providers["corpus"].name == "nadeshiko"
        assert providers["source"].name == "mock"
        assert "source" in providers


# ------------------------------------------------------------------ fake corpus


def test_fake_corpus_matches_any_word(tmp_path) -> None:
    from reelmachine.matching import is_word_match
    from reelmachine.recipes.nadeshiko_cut.fake import FakeNadeshikoCorpus

    corpus = FakeNadeshikoCorpus(_settings(mock_dir=tmp_path / "mock"))
    page = corpus.search("約束")
    assert page.segments and all(is_word_match(s, "約束") for s in page.segments)
    assert page.media["MOCKMEDIA001"].nameEn == "Mock Show"


# ------------------------------------------------------------------- storage


def test_local_storage_round_trip(tmp_path) -> None:
    storage = LocalStorage(tmp_path / "store")
    key = storage.put_bytes("jobs/a/artifact.json", b"{}")
    assert key == "jobs/a/artifact.json"
    assert storage.get_bytes(key) == b"{}"
    assert storage.exists(key)
    assert storage.local_path(key) == tmp_path / "store" / key
    assert storage.list("jobs/") == [key]
    storage.delete(key)
    assert not storage.exists(key)


def test_memory_storage_round_trip() -> None:
    storage = InMemoryStorage()
    storage.put_bytes("a/b.bin", b"x")
    assert storage.get_bytes("a/b.bin") == b"x"
    assert storage.list("a/") == ["a/b.bin"]
    assert storage.local_path("a/b.bin") is None
    storage.delete("a/b.bin")
    assert not storage.exists("a/b.bin")


def test_storage_keys_cannot_escape_the_root() -> None:
    with pytest.raises(ValueError):
        normalise_key("../etc/passwd")
    with pytest.raises(ValueError):
        normalise_key("/absolute")
    with pytest.raises(ValueError):
        normalise_key("")
    storage = LocalStorage(Path("/tmp/does-not-matter"))
    with pytest.raises(ValueError):
        storage.put_bytes("../oops", b"x")


class _StubS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put_object(self, *, Bucket, Key, Body):  # noqa: N803 - boto3's spelling
        self.objects[f"{Bucket}/{Key}"] = bytes(Body)

    class _Body:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

    def get_object(self, *, Bucket, Key):  # noqa: N803
        return {"Body": self._Body(self.objects[f"{Bucket}/{Key}"])}

    def head_object(self, *, Bucket, Key):  # noqa: N803
        if f"{Bucket}/{Key}" not in self.objects:
            raise FileNotFoundError(Key)
        return {}

    def delete_object(self, *, Bucket, Key):  # noqa: N803
        self.deleted.append(Key)

    def list_objects_v2(self, *, Bucket, Prefix="", ContinuationToken=None):  # noqa: N803
        contents = [
            {"Key": key[len(f"{Bucket}/") :]}
            for key in sorted(self.objects)
            if key.startswith(f"{Bucket}/{Prefix}")
        ]
        return {"Contents": contents, "IsTruncated": False}


def test_s3_storage_uses_an_injected_client(tmp_path) -> None:
    client = _StubS3()
    storage = S3Storage("bucket", prefix="reels", client=client, cache_dir=tmp_path / "cache")
    storage.put_bytes("job/out.mp4", b"video")
    assert client.objects["bucket/reels/job/out.mp4"] == b"video"
    assert storage.get_bytes("job/out.mp4") == b"video"
    assert storage.exists("job/out.mp4")
    assert storage.list() == ["job/out.mp4"]
    local = storage.local_path("job/out.mp4")
    assert local is not None and local.read_bytes() == b"video"
    assert storage.missing() == []
    assert S3Storage("").missing()


# -------------------------------------------------------------------- styles


def test_styles_load_through_entry_points() -> None:
    packs = load_styles(Registry())
    assert {"quranic.parchment", "doodle.default"} <= set(packs)


def test_styles_load_falls_back_when_nothing_is_registered() -> None:
    from reelmachine.core.style import bundled_styles
    from reelmachine.core.registry import registry_from_entry_points

    packs = load_styles(registry_from_entry_points([]))
    assert packs == {}
    assert "doodle.default" in bundled_styles()
