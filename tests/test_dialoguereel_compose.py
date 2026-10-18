import json
import re
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


LOOK_KEYS = {"bg", "text", "reading", "meaning", "accent", "bubble", "ink", "shade"}


def test_every_look_has_every_token():
    assert set(LOOKS) == {"pop", "pastel", "night"}
    for look in LOOKS.values():
        assert set(look) == LOOK_KEYS
        assert re.fullmatch(r"\d{1,3},\d{1,3},\d{1,3}", look["shade"])


def test_reel_document_carries_the_look():
    doc = reel_document(INPUTS.model_copy(update={"look": "night"}), CAST, PLAN, WORDS)
    assert doc["look"] == LOOKS["night"]


def test_css_has_no_look_literals():
    css = (compose.RUNTIME_DIR / "reel.css").read_text()
    body = css.split("#root {", 1)[1].split("}", 1)[1]   # the defaults block on #root may name pop's values
    assert "rgba(20,0,90" not in body and "rgba(10,0,80" not in body and "rgba(16,0,80" not in body and "#241466" not in body


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
    assert 'id="c_kid.mouth-open"' in html and 'id="c_mom.mouth-open"' in html
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


# ------------------------------------------------- the page only ever gets SVG from the checked tree

POC = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400"><!--><img src="x" '
       'onerror="document.title=\'PWNED\'">--><g id="body"/><g id="eyes-open"/><g id="eyes-closed"/>'
       '<g id="mouth-closed"/><g id="mouth-open"/></svg>')
AGENT = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 400 400">'
         '<title>&lt;/title&gt;&lt;img src=x onerror=alert(1)&gt;</title>'
         '<defs><radialGradient id="g"><stop offset="0" stop-color="#fff"/></radialGradient></defs>'
         '<g id="body" data-note="&lt;/svg&gt;&lt;img src=x onerror=alert(1)&gt;" fill="url(&#35;g)">'
         '<circle cx="200" cy="200" r="120"/></g>'
         '<g id="eyes-open"><text>&lt;script&gt;alert(1)&lt;/script&gt;</text></g><g id="eyes-closed"/>'
         '<g id="mouth-closed"><use xlink:href="&#35;body"/></g><g id="mouth-open"/></svg>')


def _cast_with(svg: str):
    from reelmachine.recipes.dialoguereel.models import ResolvedCast, ResolvedCharacter
    from reelmachine.recipes.dialoguereel.svg import check_svg
    mug = CAST.characters[1]
    # built directly, past resolve(): the composer must re-check what it inlines
    kid = ResolvedCharacter(id="kid", name="K", voice="fake:default", side="left", svg=svg, parts=[])
    return ResolvedCast(id="cast_000000000000", characters=[kid, mug]), check_svg


class _Characters(HTMLParser):
    """Every element (tag, attrs) the HTML parser puts inside a `div.char`."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.found = 0, []

    def handle_starttag(self, tag, attrs):
        if self.depth:
            self.found.append((tag, attrs))
            self.depth += 1
        elif tag == "div" and "char" in (dict(attrs).get("class") or "").split():
            self.depth = 1

    def handle_startendtag(self, tag, attrs):
        if self.depth:
            self.found.append((tag, attrs))

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1

    def handle_comment(self, data):
        if self.depth:
            self.found.append(("#comment", [("data", data)]))


def _inside_characters(project):
    parser = _Characters()
    parser.feed((project / "index.html").read_text(encoding="utf-8"))
    return parser.found


def test_the_mxss_proof_of_concept_never_reaches_the_page(tmp_path):
    import pytest

    from reelmachine.core.errors import InvalidInput
    cast, check_svg = _cast_with(POC)
    with pytest.raises(InvalidInput):
        check_svg(POC, path="x")
    with pytest.raises(InvalidInput) as err:
        write_project(cast_document(cast), cast, tmp_path / "p")
    assert err.value.details["path"] == "cast.characters[0].source.svg"
    with pytest.raises(InvalidInput):
        resolve(CastSpec.model_validate({"characters": [
            {"id": "kid", "name": "K", "side": "left", "source": {"kind": "svg", "svg": POC}},
            SPEC.characters[1].model_dump()]}))


def test_an_agent_svg_reaches_the_page_as_svg_elements_only(tmp_path):
    from reelmachine.recipes.dialoguereel.svg import _ALLOWED
    cast, check_svg = _cast_with(AGENT)
    check_svg(AGENT, path="x")
    project = write_project(cast_document(cast), cast, tmp_path / "p")
    found = _inside_characters(project)
    svg_tags = {name.lower() for name in _ALLOWED}
    assert found and {tag for tag, _ in found} <= svg_tags, {tag for tag, _ in found} - svg_tags
    assert not [(tag, a) for tag, attrs in found for a, _ in attrs if a.lower().startswith("on")]
    html = (project / "index.html").read_text(encoding="utf-8")
    assert 'href="#c_kid.body"' in html and 'fill="url(#c_kid.g)"' in html and "xlink" not in html.split("reel-data")[0]
    # the built-ins too
    builtins = _inside_characters(write_project(cast_document(CAST), CAST, tmp_path / "b"))
    assert {tag for tag, _ in builtins} <= svg_tags


def test_newlines_in_a_line_cannot_forge_srt_cues(tmp_path):
    from reelmachine.core.timeline import Caption
    from reelmachine.recipes.dialoguereel.stages import caption_text
    from reelmachine.render.captions import write_srt
    forged = "ok\n\n2\n00:00:00,000 --> 00:00:09,000\nFORGED"
    text = caption_text("手伝{てつだ}い\r\nます", forged)
    assert text == "手伝い ます\nok 2 00:00:00,000 --> 00:00:09,000 FORGED"
    srt = write_srt([Caption(start_ms=0, end_ms=1000, text=text, lang="ja")], tmp_path / "c.srt").read_text()
    assert srt.count("-->") == 2 and "\n\n2\n" not in srt  # one cue: its timing line and the text's arrow


# ------------------------------------------------- key words, emotions and slow replays in the document

def _learning(language="ja"):
    data = INPUTS.model_dump()
    data["language"] = language
    data["lines"][0].update(emotion="happy", slow=True, keys=[
        {"text": "手伝う", "reading": "てつだう", "meaning": "to help"},
        {"text": "ねこ", "meaning": "cat"},                          # not in the line: left out
        {"text": "ましょう", "meaning": "let's"},
    ])
    data["lines"][1]["emotion"] = "thinking"
    return DialogueReelInputs.model_validate(data)


def test_the_document_carries_the_matched_keys_with_their_offsets_and_levels():
    plan = schedule([1000, 1200, 900], slow=[True, False, False])
    lines = reel_document(_learning(), CAST, plan, WORDS)["lines"]
    # 手伝いましょうか。: 手伝 (its stem) at 0..2, ましょう at 3..7
    assert lines[0]["keys"] == [
        {"text": "手伝う", "runs": [{"t": "手伝う", "r": None}], "reading": "てつだう", "meaning": "to help", "level": 4,
         "start": 0, "end": 2},
        {"text": "ましょう", "runs": [{"t": "ましょう", "r": None}], "reading": None, "meaning": "let's", "level": None,
         "start": 3, "end": 7},
    ]
    assert lines[1]["keys"] == [] and lines[2]["keys"] == []
    assert [line["emotion"] for line in lines] == ["happy", "thinking", "neutral"]


def test_levels_are_only_looked_up_for_japanese():
    plan = schedule([1000, 1200, 900])
    keys = reel_document(_learning("ko"), CAST, plan, WORDS)["lines"][0]["keys"]
    assert [k["level"] for k in keys] == [None, None] and keys[0]["start"] == 0


def test_the_slow_replay_times_pass_through():
    plan = schedule([1000, 1200, 900], slow=[True, False, False])
    lines = reel_document(_learning(), CAST, plan, WORDS)["lines"]
    w = plan.lines[0]
    assert (lines[0]["slowStartMs"], lines[0]["slowEndMs"]) == (w.slow_start_ms, w.slow_start_ms + w.slow_ms)
    assert lines[0]["slowStartMs"] == lines[0]["endMs"] + 350 and lines[0]["slowEndMs"] - lines[0]["slowStartMs"] == 1333
    assert (lines[1]["slowStartMs"], lines[1]["slowEndMs"]) == (None, None)


# ------------------------------------------------- listen-first, bridge, recap and quiz in the document

def test_the_section_fixture_the_runtime_tests_use_is_what_compose_writes():
    from pathlib import Path
    fixture = json.loads((Path(compose.RUNTIME_DIR) / "tests" / "sections.json").read_text(encoding="utf-8"))
    two = INPUTS.model_copy(update={"lines": INPUTS.lines[:2]})
    doc = reel_document(two, CAST, schedule([1000, 1000], listen_first=True, recap=True, quiz_line=0), WORDS[:2])
    assert doc["duration"] == fixture["duration"] and doc["hookMs"] == fixture["hookMs"]
    assert doc["listen"] == fixture["listen"] and doc["bridgeMs"] == fixture["bridgeMs"]
    assert doc["recapMs"] == fixture["recapMs"] and doc["quiz"] == fixture["quiz"]
    assert [{k: line[k] for k in ("startMs", "endMs", "slowStartMs", "slowEndMs")} for line in doc["lines"]] == fixture["lines"]


def test_a_plain_reel_has_no_sections():
    doc = reel_document(INPUTS, CAST, PLAN, WORDS)
    assert (doc["listen"], doc["bridgeMs"], doc["recapMs"], doc["recap"], doc["quiz"]) == ([], None, None, [], None)


def test_without_a_quiz_the_document_has_none_and_the_reveal_gets_no_ding():
    from reelmachine.recipes.dialoguereel.schedule import estimate
    inputs = _learning().model_copy(update={"quiz": False})
    plan = estimate(inputs)
    doc = reel_document(inputs, CAST, plan, WORDS)
    assert doc["quiz"] is None and plan.quiz is None
    # the render's ding sits on the quiz reveal, and every voice cue is a dialogue line
    assert sorted(c.at_ms for c in plan.cues) == sorted([w.start_ms for w in plan.lines] + [plan.lines[0].slow_start_ms])
    asked = estimate(_learning())
    assert reel_document(_learning(), CAST, asked, WORDS)["quiz"] == {
        "line": 0, "startMs": asked.quiz.start_ms, "revealMs": asked.quiz.reveal_ms, "endMs": asked.quiz.end_ms}


def test_the_section_strings_follow_the_meaning_language():
    from reelmachine.recipes.dialoguereel.strings import strings_for
    en = reel_document(INPUTS, CAST, PLAN, WORDS)["strings"]
    assert en == strings_for("en", "N5") and en["follow"] == "Follow for daily N5" and en["quiz"] == "How do you say:"
    ja = reel_document(INPUTS.model_copy(update={"meaning_language": "ja-JP"}), CAST, PLAN, WORDS)["strings"]
    assert ja["recap"] == "この動画のことば"
    fr = reel_document(INPUTS.model_copy(update={"meaning_language": "fr", "level": ""}), CAST, PLAN, WORDS)["strings"]
    assert fr["bridge"] == "Now with subtitles" and fr["follow"] == "Follow for more"


def test_the_recap_lists_every_key_in_reel_order_each_once_at_most_six():
    data = INPUTS.model_dump()
    data["lines"][0]["keys"] = [{"text": "手伝う", "reading": "てつだう", "meaning": "to help"},
                                {"text": "ねこ", "meaning": "cat"},             # not in the line: still in the recap
                                {"text": "ましょう", "meaning": "let's"}]
    data["lines"][1]["text"] = "なにをしますか？いつですか？"
    data["lines"][1]["keys"] = [{"text": "なに", "meaning": "what"}, {"text": "します", "meaning": "to do"},
                                {"text": "ましょう", "meaning": "let's"},       # already in the recap
                                {"text": "いつ", "meaning": "when"}]
    data["lines"][2]["text"] = "あとでですね"
    data["lines"][2]["keys"] = [{"text": "です", "meaning": "is"}, {"text": "あとで", "meaning": "later"}]
    inputs = DialogueReelInputs.model_validate(data)
    assert reel_document(inputs, CAST, PLAN, WORDS)["recap"] == [], "no recap section, no cards"
    recap = reel_document(inputs, CAST, schedule([1000, 1200, 900], recap=True), WORDS)["recap"]
    assert [card["text"] for card in recap] == ["手伝う", "ねこ", "ましょう", "なに", "します", "いつ"]
    assert recap[0] == {"text": "手伝う", "runs": [{"t": "手伝う", "r": None}], "reading": "てつだう",
                        "meaning": "to help", "level": 4}
    assert recap[1] == {"text": "ねこ", "runs": [{"t": "ねこ", "r": None}], "reading": None, "meaning": "cat",
                        "level": recap[1]["level"]}


def test_a_key_with_ruby_keeps_its_runs_in_the_chip_and_the_recap():
    from reelmachine.recipes.dialoguereel.levels import jlpt_level
    data = INPUTS.model_dump()
    data["lines"][0]["keys"] = [{"text": "手伝{てつだ}う", "meaning": "to help"}]
    data["lines"][1]["keys"] = [{"text": "手伝う", "meaning": "to help"}]   # the same word: one card
    doc = reel_document(DialogueReelInputs.model_validate(data), CAST, schedule([1000, 1200, 900], recap=True), WORDS)
    runs = [{"t": "手伝", "r": "てつだ"}, {"t": "う", "r": None}]
    level = jlpt_level("手伝う", None)
    assert level is not None
    assert doc["lines"][0]["keys"] == [{"text": "手伝う", "runs": runs, "reading": None, "meaning": "to help",
                                        "level": level, "start": 0, "end": 2}]
    assert doc["recap"] == [{"text": "手伝う", "runs": runs, "reading": None, "meaning": "to help", "level": level}]
