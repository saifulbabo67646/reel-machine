import json

from reelmachine.core.timeline import WordSpan
from reelmachine.recipes.dialoguereel import compose
from reelmachine.recipes.dialoguereel.cast import resolve
from reelmachine.recipes.dialoguereel.compose import (FPS, HEIGHT, LOOKS, WIDTH, cast_document, project_key,
                                                      reel_document, write_project)
from reelmachine.recipes.dialoguereel.models import CastSpec, DialogueReelInputs
from reelmachine.recipes.dialoguereel.schedule import schedule

SPEC = CastSpec.model_validate({"characters": [
    {"id": "kid", "name": "Cupcake", "side": "left", "source": {"kind": "builtin", "name": "cupcake"}},
    {"id": "mom", "name": "Mug", "side": "right", "source": {"kind": "builtin", "name": "mug"}},
]})
CAST = resolve(SPEC)
INPUTS = DialogueReelInputs.model_validate({
    "title": "Helping Mom at home", "level": "N5", "scene": "🧺", "cast": SPEC.model_dump(),
    "lines": [
        {"speaker": "kid", "text": "手伝{てつだ}いましょうか。", "reading": "Tetsudai mashou ka.", "meaning": "Shall I help?"},
        {"speaker": "mom", "text": "なにをしますか？", "reading": "Nani o shimasu ka?", "meaning": "What should I do?"},
        {"speaker": "kid", "text": "</script>", "meaning": "a line that tries to close the data script"},
    ],
})
WORDS = [
    [WordSpan(text="てつだいましょうか。", start_ms=0, end_ms=1000)],
    [WordSpan(text="なにを", start_ms=0, end_ms=500), WordSpan(text="しますか？", start_ms=550, end_ms=1200)],
    [],
]
PLAN = schedule([1000, 1200, 900])


def test_the_constants():
    assert (WIDTH, HEIGHT, FPS) == (1080, 1920, 30)
    assert LOOKS["pop"]["bg"] == "#4b2bff"


def test_the_document_carries_reel_times_and_ruby_runs():
    doc = reel_document(INPUTS, CAST, PLAN, WORDS)
    assert doc["mode"] == "reel" and doc["duration"] == PLAN.duration_ms / 1000 and doc["look"] == LOOKS["pop"]
    assert doc["lines"][1]["startMs"] == 2850 and doc["lines"][0]["runs"][0] == {"t": "手伝", "r": "てつだ"}
    assert doc["lines"][0]["runs"][1] == {"t": "いましょうか。", "r": None}
    assert doc["lines"][0]["words"][0]["startMs"] >= 1500
    assert doc["lines"][1]["words"][1] == {"text": "しますか？", "startMs": 2850 + 550, "endMs": 2850 + 1200}
    assert doc["lines"][1]["meaningMs"] == PLAN.lines[1].meaning_ms and doc["lines"][2]["reading"] == ""
    assert doc["hookMs"] == 1500 and doc["characters"] == [
        {"id": "kid", "side": "left", "parts": CAST.characters[0].parts},
        {"id": "mom", "side": "right", "parts": CAST.characters[1].parts},
    ]
    assert (doc["title"], doc["level"], doc["scene"], doc["language"]) == ("Helping Mom at home", "N5", "🧺", "ja")


def test_the_cast_document_is_two_seconds_of_the_characters():
    doc = cast_document(CAST)
    assert doc["mode"] == "cast" and doc["duration"] == 2 and [c["id"] for c in doc["characters"]] == ["kid", "mom"]
    assert "lines" not in doc or doc["lines"] == []


def test_the_project_inlines_both_characters_with_their_own_ids(tmp_path):
    project = write_project(reel_document(INPUTS, CAST, PLAN, WORDS), CAST, tmp_path / "p")
    html = (project / "index.html").read_text(encoding="utf-8")
    assert 'id="kid-mouth-open"' in html and 'id="mom-mouth-open"' in html
    assert 'id="char-kid"' in html and 'class="char right"' in html
    assert "<\\/" not in html.split("reel-data")[0]
    assert 'data-composition-id=' in html and 'data-duration="' in html and "buildReady" in html
    data = html.split('<script id="reel-data" type="application/json">')[1].split("</script>")[0]
    assert json.loads(data)["lines"][2]["runs"][0]["t"] == "</script>"
    for name in ("main.mjs", "pure.mjs", "reel.css"):
        assert (project / "runtime" / name).is_file()


def test_project_key_changes_with_the_cast_and_the_runtime(tmp_path, monkeypatch):
    doc = reel_document(INPUTS, CAST, PLAN, WORDS)
    key = project_key(doc, CAST)
    assert key == project_key(doc, CAST)
    other = resolve(CastSpec.model_validate({"characters": [
        {**SPEC.characters[0].model_dump(), "source": {"kind": "builtin", "name": "cupcake", "colors": {"primary": "#000000"}}},
        SPEC.characters[1].model_dump(),
    ]}))
    assert project_key(doc, other) != key
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for path in compose.RUNTIME_DIR.iterdir():
        if path.is_file():
            (runtime / path.name).write_bytes(path.read_bytes())
    monkeypatch.setattr(compose, "RUNTIME_DIR", runtime)
    assert project_key(doc, CAST) == key
    (runtime / "reel.css").write_text((runtime / "reel.css").read_text() + "\n/* changed */")
    assert project_key(doc, CAST) != key
