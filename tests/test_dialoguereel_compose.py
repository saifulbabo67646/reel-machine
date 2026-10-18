import json
from html.parser import HTMLParser

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


class _Scripts(HTMLParser):
    """The page's <script> elements as the HTML parser sees them: (attrs, text)."""

    def __init__(self):
        super().__init__()
        self.scripts, self._open = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._open = (dict(attrs), [])

    def handle_data(self, data):
        if self._open is not None:
            self._open[1].append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._open is not None:
            self.scripts.append((self._open[0], "".join(self._open[1])))
            self._open = None


def _with_text(text):
    data = INPUTS.model_dump()
    data["lines"][2]["text"] = text
    return DialogueReelInputs.model_validate(data)


def test_markup_in_a_line_cannot_swallow_the_runtime_script(tmp_path):
    for text in ("<!--<script>", "<!--<script></script>-->", "</script><script>alert(1)</script>"):
        project = write_project(reel_document(_with_text(text), CAST, PLAN, WORDS), CAST, tmp_path / "p")
        html = (project / "index.html").read_text(encoding="utf-8")
        data_at = html.index('<script id="reel-data"')
        assert "<" not in html[data_at + len('<script id="reel-data" type="application/json">'):].split("</script>")[0]
        parser = _Scripts()
        parser.feed(html)
        by_id = {attrs.get("id") or attrs.get("src"): body for attrs, body in parser.scripts}
        assert json.loads(by_id["reel-data"])["lines"][2]["runs"][0]["t"] == text
        assert "./runtime/main.mjs" in by_id and by_id["./runtime/main.mjs"] == ""
        # the module script comes after the data script closes, in the raw text too
        assert html.index('<script type="module" src="./runtime/main.mjs">') > html.index("</script>", data_at)


def test_the_hook_prompt_follows_the_meaning_language():
    for lang, prompt in (("en", "Can you understand this?"), ("ja", "わかるかな？"), ("ko", "이해할 수 있나요?"),
                         ("es-MX", "¿Lo entiendes?"), ("zh", "你能听懂吗？")):
        inputs = INPUTS.model_copy(update={"meaning_language": lang})
        assert reel_document(inputs, CAST, PLAN, WORDS)["prompt"] == prompt


def test_other_meaning_languages_have_no_prompt():
    inputs = INPUTS.model_copy(update={"meaning_language": "fr"})
    assert reel_document(inputs, CAST, PLAN, WORDS)["prompt"] is None
