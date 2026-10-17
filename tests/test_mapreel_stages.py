"""mapreel's images stage: a bad image is a badge and a warning, never a failed job or a stray file."""

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from reelmachine.core.errors import JobCancelled
from reelmachine.recipes.mapreel import stages
from reelmachine.recipes.mapreel.models import GeoOutput, MapReelInputs, ResolvedSegment


class _Token:
    def __init__(self, cancelled=False):
        self.cancelled = cancelled

    def raise_if_cancelled(self):
        if self.cancelled:
            raise JobCancelled()


def _payload(images):
    inputs = MapReelInputs.model_validate({"segments": [{
        "kind": "country", "country": "ITA", "narration": "Italy.",
        "items": [{"name": f"item{i}", "image": ref} for i, ref in enumerate(images)]}]})
    seg = ResolvedSegment(id="seg-1", index=0, kind="country", codes=["ITA"], names={"ITA": "Italy"},
                          colours={"ITA": "#8a2be2"})
    return GeoOutput(inputs=inputs, segments=[seg], world="w.jpg", layers={"seg-1": "d.jpg"},
                     detail_bboxes={"seg-1": [0, 0, 1, 1]}, frames={"seg-1": {"x": 0.5, "y": 0.5, "s": 2000}})


def _run(tmp_path, images, cancelled=False):
    ctx = SimpleNamespace(workdir=tmp_path / "stage", cancel=_Token(cancelled))
    return stages.ImagesStage().run(ctx, _payload(images))


def _fake_stream(content_type, body):
    class Response:
        headers = {"content-type": content_type}

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            for i in range(0, len(body), 1024):
                yield body[i:i + 1024]

    @contextmanager
    def stream(method, url, **kwargs):
        yield Response()

    return stream


@pytest.mark.parametrize("ref", [
    "https://exa\x00mple.com/a.png",           # httpx.InvalidURL: not an HTTPError
    "https://" + "a" * 70 + ".com/a.png",      # an over-long host label: UnicodeError
    "~no-such-user-here/a.png",                # expanduser: RuntimeError
])
def test_an_unusable_ref_becomes_a_badge_with_a_warning(tmp_path, ref):
    out = _run(tmp_path, [ref])
    assert out.images == {"seg-1/0": None}
    assert len(out.warnings) == 1 and "item0" in out.warnings[0] and "badge" in out.warnings[0]


def test_a_hostile_content_type_never_picks_the_path(tmp_path, monkeypatch):
    monkeypatch.setattr(stages.httpx, "stream", _fake_stream("image/../../x", b"\x89PNG" * 10))
    out = _run(tmp_path, ["https://example.com/a"])
    assert out.images == {"seg-1/0": None} and "not an image" in out.warnings[0]
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == []


def test_a_download_is_named_by_content_inside_the_images_dir_with_a_stable_mtime(tmp_path, monkeypatch):
    monkeypatch.setattr(stages.httpx, "stream", _fake_stream("image/PNG; charset=binary", b"\x89PNG" * 10))
    first = Path(_run(tmp_path / "a", ["https://example.com/a"]).images["seg-1/0"])
    second = Path(_run(tmp_path / "b", ["https://example.com/a"]).images["seg-1/0"])
    assert first.parent == tmp_path / "a" / "stage" / "images" and first.suffix == ".png"
    assert first.name == second.name and first.stat().st_mtime_ns == second.stat().st_mtime_ns


def test_a_download_over_8_mb_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "IMAGE_MAX_BYTES", 4096)
    monkeypatch.setattr(stages.httpx, "stream", _fake_stream("image/png", b"x" * 5000))
    out = _run(tmp_path, ["https://example.com/a"])
    assert out.images == {"seg-1/0": None} and "8 MB" in out.warnings[0]


def test_cancellation_still_stops_the_stage(tmp_path):
    with pytest.raises(JobCancelled):
        _run(tmp_path, ["/nope.png"], cancelled=True)


FIXTURE = Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson"


def test_highlight_fills_on_an_alias(tmp_path, monkeypatch):
    from reelmachine.core.timeline import WordSpan
    from reelmachine.recipes.mapreel.models import NarrationOutput, Window
    from reelmachine.recipes.mapreel.places import Atlas

    monkeypatch.setattr(stages, "atlas_for", lambda settings: Atlas.load(FIXTURE))
    inputs = MapReelInputs.model_validate({"segments": [{
        "kind": "highlight", "countries": ["USA", "NLD"], "narration": "America and Holland lead."}]})
    seg = ResolvedSegment(id="seg-1", index=0, kind="highlight", codes=["USA", "NLD"],
                          names={"USA": "United States of America", "NLD": "Netherlands"},
                          colours={"USA": "#8a2be2", "NLD": "#e0201b"})
    layer = tmp_path / "layer.jpg"
    layer.write_bytes(b"jpg")
    spoken = [WordSpan(text=w, start_ms=i * 400, end_ms=i * 400 + 300)
              for i, w in enumerate("America and Holland lead.".split())]
    payload = NarrationOutput(
        inputs=inputs, segments=[seg], world=str(layer), layers={"seg-1": str(layer)},
        detail_bboxes={"seg-1": [0, 0, 1, 1]}, frames={"seg-1": {"x": 0.3, "y": 0.4, "s": 2000}},
        images={}, audio_asset="a.wav", duration_ms=5000,
        windows=[Window(start_ms=0, duration_ms=5000, speech_offset_ms=1000, speech_ms=2000)],
        words=[spoken], hot=[[False] * len(spoken)], spoken=[spoken], provider="fake")
    ctx = SimpleNamespace(workdir=tmp_path / "stage", config=None)
    stages.SegmentsStage().run(ctx, payload)
    resolved = json.loads((tmp_path / "stage" / "artifacts" / "script.resolved.json").read_text())
    fills = {p["code"]: p["fillMs"] for p in resolved["segments"][0]["places"]}
    assert fills == {"USA": 1000, "NLD": 1800}
