import itertools
import json
import re

from reelmachine.recipes.techreel import compose
from reelmachine.recipes.techreel.compose import (
    LOOKS,
    look_tokens,
    project_key,
    reel_document,
    write_project,
)
from reelmachine.recipes.techreel.markup import units
from reelmachine.recipes.techreel.models import TechReelInputs
from reelmachine.recipes.techreel.schedule import schedule


def _inputs(**kw):
    beats = [
        {"id": "hook", "kind": "hook", "heading": "bhai *stop*", "kicker": "wait",
         "lines": [{"say": "one two three four five six seven eight"}, {"say": "nine ten"}],
         "visuals": [
             {"kind": "hero", "state": "pulse", "at": {"line": 0, "word": "three"}},
             {"kind": "counter", "from": 0, "to": 9, "at": {"line": 0, "word": "four"},
              "prefix": "$"},
             {"kind": "stamp", "text": "NOPE", "at": {"line": 1}},
             {"kind": "terminal", "lines": [{"cmd": "ls"}, {"out": "a", "tone": "ok"}]},
             {"kind": "window", "title": "w", "cursor": [{"to": [0.5, 0.5], "at": {"line": 1, "frac": 0.5}}]},
         ]},
        {"lines": [{"say": "last words *here*"}], "transition": "slide"},
    ]
    return TechReelInputs.model_validate({"title": "t", "hero": {"kind": "orb"}, "beats": beats, **kw})


def _times(inputs, step=100):
    return [[[(k * step, (k + 1) * step) for k in range(len(units(ln.caption, inputs.language)))]
             for ln in b.lines] for b in inputs.beats]


def _doc(**kw):
    inputs = _inputs(**kw)
    plan = schedule(inputs.beats, [[1200, 600], [500]])
    return inputs, plan, reel_document(inputs, plan, _times(inputs))


def test_top_level_shape():
    inputs, plan, doc = _doc()
    assert (doc["mode"], doc["width"], doc["height"], doc["language"]) == ("reel", 1080, 1920, "en")
    assert doc["duration"] == plan.duration_ms / 1000
    assert doc["hero"] == {"kind": "orb", "text": None}
    assert doc["sfx"] is True
    assert doc["look"] == look_tokens(inputs)
    assert [b["id"] for b in doc["beats"]] == ["hook", "b1"]
    b0 = doc["beats"][0]
    assert (b0["startMs"], b0["endMs"], b0["transition"], b0["kicker"]) == (0, plan.beats[0].end_ms, "cut", "wait")
    assert b0["heading"] == [{"t": "bhai", "a": False}, {"t": "stop", "a": True}]
    assert doc["beats"][1]["heading"] == [] and doc["beats"][1]["kicker"] == ""
    json.dumps(doc, allow_nan=False)


def test_hero_states():
    _, plan, doc = _doc()
    line0 = plan.beats[0].lines[0].start_ms
    assert doc["heroStates"] == [
        {"atMs": 0, "state": "center", "text": None},
        {"atMs": line0 + 200, "state": "pulse", "text": None},
    ]
    assert all(v["kind"] != "hero" for v in doc["beats"][0]["visuals"])


def test_hero_states_without_hero_spec_and_hero_at_zero():
    inputs = _inputs(hero=None)
    plan = schedule(inputs.beats, [[1200, 600], [500]])
    assert reel_document(inputs, plan, _times(inputs))["heroStates"] == []
    inputs = _inputs()
    inputs.beats[0].visuals[0].at = None
    doc = reel_document(inputs, plan, _times(inputs))
    assert doc["heroStates"] == [{"atMs": 0, "state": "pulse", "text": None}]


def test_visual_times_ids_and_fields():
    _, plan, doc = _doc()
    vs = {v["id"]: v for v in doc["beats"][0]["visuals"]}
    assert list(vs) == ["b0v1", "b0v2", "b0v3", "b0v4"]      # the hero keeps its slot, v0
    c = vs["b0v1"]
    assert c["enterMs"] == plan.beats[0].lines[0].start_ms + 300
    assert c["exitMs"] == plan.beats[1].start_ms
    assert (c["from"], c["to"], c["prefix"], c["class"], c["place"]) == (0, 9, "$", "block", "stack")
    assert not {"at", "exit", "from_"} & set(c)
    assert vs["b0v2"]["class"] == "overlay"
    assert vs["b0v2"]["enterMs"] == plan.beats[0].lines[1].start_ms
    assert vs["b0v3"]["lines"] == [{"cmd": "ls", "tone": "dim"}, {"out": "a", "tone": "ok"}]
    cur = vs["b0v4"]["cursor"]
    assert cur == [{"to": [0.5, 0.5], "atMs": plan.beats[0].lines[1].start_ms + 300}]
    # the last beat's visuals would leave at the outro's end
    assert doc["beats"][1]["visuals"] == []


def test_last_beat_exit_is_end_plus_outro():
    inputs = _inputs()
    inputs.beats[0].visuals = []
    from reelmachine.recipes.techreel.models import TitleVisual
    inputs.beats[1].visuals = [TitleVisual(kind="title", text="x")]
    plan = schedule(inputs.beats, [[1200, 600], [500]])
    doc = reel_document(inputs, plan, _times(inputs))
    assert doc["beats"][1]["visuals"][0]["exitMs"] == plan.duration_ms


def test_captions_cover_lines_without_overlap():
    _, plan, doc = _doc()
    caps = doc["captions"]
    assert caps == sorted(caps, key=lambda c: c["startMs"])
    for a, b in itertools.pairwise(caps):
        assert a["endMs"] <= b["startMs"]
    first = caps[0]
    s0 = plan.beats[0].lines[0]
    assert first["startMs"] == s0.start_ms
    assert [w["t"] for w in first["words"]] == ["one", "two", "three", "four", "five", "six"]
    assert first["endMs"] == s0.start_ms + 600                # the next chunk's first start
    assert first["words"][0] == {"t": "one", "a": False, "startMs": s0.start_ms, "endMs": s0.start_ms + 100}
    second = caps[1]
    assert second["endMs"] == s0.end_ms                       # a line's last chunk ends with the line
    assert caps[-1]["words"][-1]["a"] is True and caps[-1]["endMs"] == plan.beats[1].lines[0].end_ms


def test_look_tokens():
    keys = {"bg", "glow1", "glow2", "glow3", "text", "muted", "accent", "accentInk", "card", "cardEdge", "red", "green"}
    assert set(LOOKS) == {"mint", "ember", "violet", "mono"}
    assert all(set(v) == keys for v in LOOKS.values())
    assert LOOKS["mint"]["bg"] == "#06100f" and LOOKS["mint"]["accent"] == "#2ee6b8"
    assert LOOKS["ember"]["bg"] == "#141110" and LOOKS["ember"]["accent"] == "#ff5a2a"
    assert LOOKS["violet"]["accent"] == "#9b7bff" and LOOKS["mono"]["accent"] == "#f4f4f5"
    assert look_tokens(_inputs())["accent"] == "#2ee6b8"
    assert look_tokens(_inputs(accent="#123456"))["accent"] == "#123456"
    assert LOOKS["mint"]["accent"] == "#2ee6b8"               # the table is not mutated


def test_write_project(tmp_path, monkeypatch):
    rt = tmp_path / "rt"
    rt.mkdir()
    (rt / "main.mjs").write_text("// m")
    (rt / "reel.css").write_text("/* c */")
    monkeypatch.setattr(compose, "_runtime_paths", lambda: sorted(rt.iterdir()))
    _, _, doc = _doc()
    doc["beats"][0]["heading"][0]["t"] = "<!--<script>"
    out = write_project(doc, tmp_path / "proj")
    html = (out / "index.html").read_text()
    assert (out / "runtime" / "main.mjs").exists() and (out / "runtime" / "reel.css").exists()
    assert 'data-composition-id="techreel"' in html and "buildReady = { techreel:" in html
    data = re.search(r'<script id="reel-data" type="application/json">(.*?)</script>', html, re.DOTALL).group(1)
    assert "<" not in data
    assert json.loads(data)["beats"][0]["heading"][0]["t"] == "<!--<script>"
    # rewriting replaces the directory
    write_project(doc, out)


def test_project_key_tracks_runtime_bytes(tmp_path, monkeypatch):
    f = tmp_path / "main.mjs"
    f.write_text("a")
    monkeypatch.setattr(compose, "_runtime_paths", lambda: [f])
    _, _, doc = _doc()
    k1 = project_key(doc)
    assert project_key(doc) == k1
    f.write_text("b")
    assert project_key(doc) != k1
    f.write_text("a")
    doc["sfx"] = False
    assert project_key(doc) != k1
