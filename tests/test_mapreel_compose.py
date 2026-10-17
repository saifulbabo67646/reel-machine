import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from reelmachine.recipes.mapreel import compose, geometry as g
from reelmachine.recipes.mapreel.places import Atlas

ATLAS = Atlas.load(Path(__file__).parent / "fixtures" / "mapreel" / "countries.geojson")


def _doc(tmp_path):
    shape = g.shape_of(ATLAS.resolve("ITA"))
    frame = compose.segment_frame([shape], 1.0)
    place = compose.place_entry("ITA", "Italy", "#3333ff", shape, frame, 700, (12.5, 42.5))
    items = compose.item_entries([{"name": "Gucci", "image": None, "emoji": None, "atMs": 1100},
                                  {"name": "Prada", "image": "images/prada.png", "emoji": None, "atMs": 1500}],
                                 shape, frame)
    return compose.segment_document(
        duration_ms=3000, camera={"from": frame.to_dict(), "to": frame.to_dict(), "flyMs": 0, "drift": 0.04},
        layers=[{"url": "layers/world.jpg", "bbox": [0, 0, 1, 1]}], places=[place], previous=[],
        items=items, words=[{"text": "Italy", "start": 0.25, "end": 0.6, "hot": False}], legend=None)


def test_a_document_has_what_the_runtime_reads(tmp_path):
    doc = _doc(tmp_path)
    assert doc["width"] == 1080 and doc["height"] == 1920 and doc["duration"] == pytest.approx(3.0)
    place = doc["places"][0]
    assert place["code"] == "ITA" and place["fillMs"] == 700 and len(place["rings"]) >= 1
    assert all(len(pt) == 2 for pt in place["rings"][0])
    assert [i["name"] for i in doc["items"]] == ["Gucci", "Prada"]
    assert all(0 < i["x"] < 1080 and 0 < i["y"] < 1920 and i["size"] > 0 for i in doc["items"])


def test_rings_are_simplified_for_the_screen(tmp_path):
    doc = _doc(tmp_path)
    raw = sum(len(r) for r in g.shape_of(ATLAS.resolve("ITA")).rings)
    assert sum(len(r) for r in doc["places"][0]["rings"]) <= raw


def test_the_project_has_html_runtime_and_linked_files(tmp_path):
    doc = _doc(tmp_path)
    layer = tmp_path / "world.jpg"
    layer.write_bytes(b"jpeg")
    project = compose.write_project(doc, tmp_path / "p", {"layers/world.jpg": layer})
    html = (project / "index.html").read_text()
    assert 'data-duration="3.000"' in html and 'id="segment-data"' in html and "__mapReady" in html
    assert {"main.mjs", "draw.mjs", "pure.mjs"} <= {p.name for p in (project / "runtime").iterdir()}
    assert (project / "layers" / "world.jpg").read_bytes() == b"jpeg"
    data = re.search(r'<script id="segment-data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert json.loads(data.replace("<\\/", "</"))["places"][0]["code"] == "ITA"


def test_the_project_key_changes_with_content(tmp_path):
    doc = _doc(tmp_path)
    layer = tmp_path / "world.jpg"
    layer.write_bytes(b"jpeg")
    a = compose.project_key(doc, {"layers/world.jpg": layer})
    doc["legend"] = "changed"
    assert compose.project_key(doc, {"layers/world.jpg": layer}) != a


def test_the_runtime_unit_tests_pass():
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    proc = subprocess.run(["node", "--test", str(compose.RUNTIME_DIR / "tests" / "pure.test.mjs")],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("fly_ms,duration_ms", [(0, 2500), (900, 2500), (900, 4800), (900, 9000), (0, 6000)])
def test_the_drifted_end_is_where_the_runtime_camera_ends(fly_ms, duration_ms):
    """drifted_end must match cameraAt in runtime/pure.mjs, or every cut snaps the zoom."""
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    camera = {"from": {"x": 0.2, "y": 0.4, "s": 1080.0}, "to": {"x": 0.55, "y": 0.35, "s": 20000.0},
              "flyMs": fly_ms, "drift": compose.DRIFT}
    script = (f"import {{ cameraAt }} from {json.dumps((compose.RUNTIME_DIR / 'pure.mjs').as_uri())};"
              f"console.log(JSON.stringify(cameraAt({json.dumps(camera)}, {duration_ms})));")
    proc = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    assert compose.drifted_end(camera, duration_ms) == pytest.approx(json.loads(proc.stdout), rel=1e-12)


def test_the_drift_grows_for_six_seconds_after_landing():
    camera = {"from": {"x": 0, "y": 0, "s": 100.0}, "to": {"x": 0.5, "y": 0.5, "s": 1000.0}, "flyMs": 900,
              "drift": 0.04}
    assert compose.drifted_end(camera, 900)["s"] == pytest.approx(1000)
    assert compose.drifted_end(camera, 900 + 3000)["s"] == pytest.approx(1020)
    assert compose.drifted_end(camera, 900 + 60000) == pytest.approx({"x": 0.5, "y": 0.5, "s": 1040})


def test_the_project_key_covers_the_html_template(tmp_path, monkeypatch):
    doc = _doc(tmp_path)
    layer = tmp_path / "world.jpg"
    layer.write_bytes(b"jpeg")
    a = compose.project_key(doc, {"layers/world.jpg": layer})
    monkeypatch.setattr(compose, "_CSS", compose._CSS + "body{color:red}")
    assert compose.project_key(doc, {"layers/world.jpg": layer}) != a
    monkeypatch.undo()
    monkeypatch.setattr(compose, "_TEMPLATE", compose._TEMPLATE + "<!-- x -->")
    assert compose.project_key(doc, {"layers/world.jpg": layer}) != a


def test_nan_fails_loudly(tmp_path):
    doc = _doc(tmp_path)
    doc["legend"] = float("nan")
    with pytest.raises(ValueError):
        compose.write_project(doc, tmp_path / "p", {})


@pytest.mark.parametrize("codes", [("USA", "RUS"), ("FJI", "JPN")])
def test_a_place_is_not_torn_across_world_copies(codes):
    shapes = [g.shape_of(ATLAS.resolve(c)) for c in codes]
    frame = compose.segment_frame(shapes, 1.0)
    for code, shape in zip(codes, shapes):
        place = ATLAS.resolve(code)
        entry = compose.place_entry(code, code, "#fff", shape, frame, 500, place.label)
        xs = [x for ring in entry["rings"] for x, _ in ring]
        assert max(xs) - min(xs) < 0.6, code
        assert min(xs) - 0.05 <= entry["label"]["x"] <= max(xs) + 0.05, code

