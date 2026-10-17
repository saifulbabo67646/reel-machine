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


def test_a_flight_takes_the_short_way_round(tmp_path, monkeypatch):
    from reelmachine.core.timeline import WordSpan
    from reelmachine.recipes.mapreel import compose
    from reelmachine.recipes.mapreel.geometry import shape_of
    from reelmachine.recipes.mapreel.models import NarrationOutput, Window
    from reelmachine.recipes.mapreel.places import Atlas

    atlas = Atlas.load(FIXTURE)
    monkeypatch.setattr(stages, "atlas_for", lambda settings: atlas)
    inputs = MapReelInputs.model_validate({"segments": [
        {"kind": "country", "country": "JPN", "narration": "Japan.", "items": [{"name": "Sony"}]},
        {"kind": "highlight", "countries": ["USA", "JPN"], "narration": "America and Japan."}]})
    segs = [ResolvedSegment(id="seg-1", index=0, kind="country", codes=["JPN"], names={"JPN": "Japan"},
                            colours={"JPN": "#8a2be2"}),
            ResolvedSegment(id="seg-2", index=1, kind="highlight", codes=["USA", "JPN"],
                            names={"USA": "United States of America", "JPN": "Japan"},
                            colours={"USA": "#e0201b", "JPN": "#8a2be2"})]
    frames = {s.id: compose.segment_frame([shape_of(atlas.places[c]) for c in s.codes], 1.0).to_dict()
              for s in segs}
    assert abs(frames["seg-2"]["x"] - frames["seg-1"]["x"]) > 0.5  # the long way, unless the stage wraps
    layer = tmp_path / "layer.jpg"
    layer.write_bytes(b"jpg")
    said = [[WordSpan(text="Japan.", start_ms=0, end_ms=300)],
            [WordSpan(text=w, start_ms=i * 400, end_ms=i * 400 + 300) for i, w in enumerate("America and Japan.".split())]]
    payload = NarrationOutput(
        inputs=inputs, segments=segs, world=str(layer), layers={s.id: str(layer) for s in segs},
        detail_bboxes={"seg-1": [0.8, 0.3, 0.95, 0.45], "seg-2": [-0.2, 0.3, 0.35, 0.45]}, frames=frames,
        images={"seg-1/0": None}, audio_asset="a.wav", duration_ms=10000,
        windows=[Window(start_ms=0, duration_ms=5000, speech_offset_ms=1000, speech_ms=2000),
                 Window(start_ms=5000, duration_ms=5000, speech_offset_ms=1000, speech_ms=2000)],
        words=said, hot=[[False] * len(w) for w in said], spoken=said, provider="fake")
    docs = {}
    write = stages.compose.write_project
    monkeypatch.setattr(stages.compose, "write_project",
                        lambda doc, dest, links: docs.setdefault(Path(dest).name, doc) and write(doc, dest, links))
    ctx = SimpleNamespace(workdir=tmp_path / "stage", config=None)
    stages.SegmentsStage().run(ctx, payload)
    resolved = json.loads((tmp_path / "stage" / "artifacts" / "script.resolved.json").read_text())
    camera = resolved["segments"][1]["camera"]
    assert camera["flyMs"] > 0
    assert abs(camera["to"]["x"] - camera["from"]["x"]) <= 0.5
    assert camera["to"] == frames["seg-2"]  # the landed frame stays in its own world
    doc = docs["seg-2"]
    # what the previous segment showed moved with the camera's start: one world west
    prev = next(layer for layer in doc["layers"] if layer["url"] == "layers/prev-detail.jpg")
    assert prev["bbox"] == pytest.approx([-0.2, 0.3, -0.05, 0.45])
    jpn = docs["seg-1"]["places"][0]["rings"][0][0]
    assert doc["previous"][0]["rings"][0][0] == pytest.approx([jpn[0] - 1, jpn[1]])


def test_segments_after_a_versus_fly_from_its_union_frame(tmp_path, monkeypatch):
    from reelmachine.core.timeline import WordSpan
    from reelmachine.recipes.mapreel import compose, kinds
    from reelmachine.recipes.mapreel.geometry import frame_for, shape_of
    from reelmachine.recipes.mapreel.models import NarrationOutput, Window
    from reelmachine.recipes.mapreel.places import Atlas

    atlas = Atlas.load(FIXTURE)
    monkeypatch.setattr(stages, "atlas_for", lambda settings: atlas)
    inputs = MapReelInputs.model_validate({"segments": [
        {"kind": "versus", "countries": ["JPN", "USA"], "narration": "Japan has 125M, America 335M.",
         "stats": [{"value": "125M"}, {"value": "335M"}]},
        {"kind": "country", "country": "ITA", "narration": "Italy.", "items": [{"name": "Gucci"}]}]})
    segs = [ResolvedSegment(id="seg-1", index=0, kind="versus", codes=["JPN", "USA"],
                            names={"JPN": "Japan", "USA": "United States of America"},
                            colours={"JPN": "#8a2be2", "USA": "#e0201b"}),
            ResolvedSegment(id="seg-2", index=1, kind="country", codes=["ITA"], names={"ITA": "Italy"},
                            colours={"ITA": "#1e90ff"})]
    frames = {s.id: compose.segment_frame([shape_of(atlas.places[c]) for c in s.codes], 1.0).to_dict()
              for s in segs}
    layer = tmp_path / "layer.jpg"
    layer.write_bytes(b"jpg")
    panels = {"seg-1": [{"frame": frame_for(shape_of(atlas.places[c]).main, view=(r[2], r[3]),
                                            safe=kinds.PANEL_SAFE).to_dict(),
                         "layer": str(layer), "bbox": [0, 0, 1, 1], "rect": list(r)}
                        for c, r in zip(["JPN", "USA"], kinds.panel_rects(2))]}
    said = [[WordSpan(text=w, start_ms=i * 400, end_ms=i * 400 + 300)
             for i, w in enumerate("Japan has 125M, America 335M.".split())],
            [WordSpan(text="Italy.", start_ms=0, end_ms=300)]]
    payload = NarrationOutput(
        inputs=inputs, segments=segs, world=str(layer), layers={"seg-2": str(layer)},
        detail_bboxes={"seg-2": [0.5, 0.3, 0.6, 0.4]}, frames=frames, panels=panels,
        images={"seg-2/0": None}, audio_asset="a.wav", duration_ms=10000,
        windows=[Window(start_ms=0, duration_ms=5000, speech_offset_ms=1000, speech_ms=2000),
                 Window(start_ms=5000, duration_ms=5000, speech_offset_ms=1000, speech_ms=2000)],
        words=said, hot=[[False] * len(w) for w in said], spoken=said, provider="fake")
    docs = {}
    write = stages.compose.write_project
    monkeypatch.setattr(stages.compose, "write_project",
                        lambda doc, dest, links: docs.setdefault(Path(dest).name, doc) and write(doc, dest, links))
    ctx = SimpleNamespace(workdir=tmp_path / "stage", config=None)
    out = stages.SegmentsStage().run(ctx, payload)
    resolved = json.loads((tmp_path / "stage" / "artifacts" / "script.resolved.json").read_text())
    versus, after = resolved["segments"]
    # the versus cuts in: no flight, no previous fade, only the world layer under its panels
    assert versus["camera"]["flyMs"] == 0
    assert docs["seg-1"]["previous"] == [] and docs["seg-1"]["places"] == [] and docs["seg-1"]["items"] == []
    assert [layer["url"] for layer in docs["seg-1"]["layers"]] == ["layers/world.jpg"]
    assert [p["layers"][0]["url"] for p in docs["seg-1"]["panels"]] == ["layers/panel-0.jpg", "layers/panel-1.jpg"]
    assert (tmp_path / "stage" / "projects" / "seg-1" / "layers" / "panel-1.jpg").is_file()
    assert [c["sfx"] for c in out.cues if c["at_ms"] < 5000].count("whoosh") == 1
    # the next segment flies from the union frame, undrifted, starting on the world layer
    assert after["camera"]["flyMs"] > 0
    assert after["camera"]["from"] == pytest.approx(frames["seg-1"])
    assert "layers/prev-detail.jpg" not in [layer["url"] for layer in docs["seg-2"]["layers"]]
    assert docs["seg-2"]["previous"] == []
