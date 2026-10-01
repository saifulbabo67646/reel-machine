"""The manifest records everything that affected an output."""

from __future__ import annotations

from reelmachine.core.assets import AssetKind, AssetStore, Licence, Provenance
from reelmachine.core.manifest import (
    CheckResult,
    ConfigValue,
    Manifest,
    QuotaSpend,
    TimelineRef,
    VerificationReport,
)


def _manifest(tmp_path) -> Manifest:
    store = AssetStore(tmp_path / "assets")
    source = tmp_path / "narration.wav"
    source.write_bytes(b"RIFF")
    asset = store.put(
        source,
        kind=AssetKind.NARRATION,
        provenance=Provenance(provider="fake", provider_version="1", source_id="beat-1"),
        licence=Licence(name="CC0-1.0", attribution="fake voice"),
    )
    manifest = Manifest(
        job_id="j1",
        recipe="doodle",
        caller="alice",
        inputs={"mode": "stroke"},
        config={
            "aspect": ConfigValue(value="vertical", source="job"),
            "crf": ConfigValue(value=20, source="env"),
        },
        render_mode="stroke",
        timeline=TimelineRef(digest="d", artifact="timeline.json", duration_ms=1000, render_mode="stroke"),
        environment={"reelmachine": "0.2.0", "python": "3.13"},
    )
    manifest.add_asset(asset, name="narration.wav")
    manifest.pin("narration", "fake", version="1", voice="fake:default", models=["fake-tts"])
    manifest.quota["tts"] = QuotaSpend(provider="tts", unit="characters", units=42, caller="alice")
    manifest.verification = VerificationReport(
        ok=True,
        render_mode="stroke",
        checks=[CheckResult(name="streams", ok=True, detail="audio+video present")],
    )
    return manifest


def test_manifest_round_trips_with_assets_pins_and_quota(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    loaded = Manifest.model_validate_json(manifest.model_dump_json())
    assert loaded.render_mode == "stroke"
    assert loaded.assets[0].asset.provenance.provider == "fake"
    assert loaded.assets[0].asset.licence.name == "CC0-1.0"
    assert loaded.providers["narration"].voice == "fake:default"
    assert loaded.quota["tts"].units == 42
    assert loaded.verification is not None and loaded.verification.ok
    assert loaded.config["aspect"].source == "job"


def test_provenance_lookup_by_asset_id(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    asset_id = manifest.assets[0].asset.id
    provenance = manifest.provenance_of(asset_id)
    assert provenance is not None and provenance.source_id == "beat-1"
    assert manifest.provenance_of("nope") is None
