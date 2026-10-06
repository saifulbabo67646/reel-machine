"""Content-addressed asset store."""

from __future__ import annotations

import hashlib

import pytest

from reelmachine.core.assets import AssetKind, AssetNotFound, AssetStore, Licence, Provenance


def test_put_is_content_addressed_and_idempotent(tmp_path):
    store = AssetStore(tmp_path / "assets")
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"pretend video")

    first = store.put(
        source,
        kind=AssetKind.SOURCE_CUT,
        provenance=Provenance(provider="mock", source_id="ep1"),
        licence=Licence(name="CC0-1.0", attribution="test"),
    )
    second = store.put(source, kind=AssetKind.SOURCE_CUT)

    assert first.id == second.id
    assert first.sha256 == hashlib.sha256(b"pretend video").hexdigest()
    assert len(first.id) == 32
    assert store.get(first.id).read_bytes() == b"pretend video"
    assert store.get(first.id) == store.get(second.id)
    # one copy of the bytes regardless of how often it is stored
    files = [p for p in (tmp_path / "assets").rglob("*") if p.is_file()]
    assert len(files) == 2  # the blob and its sidecar


def test_metadata_round_trips_provenance_and_licence(tmp_path):
    store = AssetStore(tmp_path / "assets")
    source = tmp_path / "line.png"
    source.write_bytes(b"\x89PNG")
    asset = store.put(
        source,
        kind=AssetKind.LINE_ART,
        provenance=Provenance(provider="tenant", source="upload", upstream="https://example.test"),
        licence=Licence(name="MIT", url="https://example.test/licence", attribution="someone"),
    )
    loaded = store.metadata(asset.id)
    assert loaded is not None
    assert loaded.provenance.provider == "tenant"
    assert loaded.licence is not None and loaded.licence.name == "MIT"
    assert loaded.kind is AssetKind.LINE_ART


def test_write_bytes_for_generated_artifacts(tmp_path):
    store = AssetStore(tmp_path / "assets")
    asset = store.write_bytes(b'{"a": 1}', kind=AssetKind.REPORT, ext=".json")
    assert store.get(asset.id).read_text() == '{"a": 1}'
    assert asset.mime == "application/json"


def test_missing_asset_raises(tmp_path):
    store = AssetStore(tmp_path / "assets")
    with pytest.raises(AssetNotFound):
        store.get("0" * 32)


def test_all_lists_every_asset(tmp_path):
    store = AssetStore(tmp_path / "assets")
    for index in range(3):
        source = tmp_path / f"f{index}.bin"
        source.write_bytes(bytes([index]))
        store.put(source, kind=AssetKind.OTHER)
    assert len(store.all()) == 3
