import itertools
import json
import re
import shutil
import subprocess

import pytest

from reelmachine.recipes.techreel import compose
from reelmachine.recipes.techreel.compose import (
    LOOKS,
    look_tokens,
    project_key,
    reel_document,
    write_project,
)
from reelmachine.recipes.techreel.markup import Unit, merge_short, units
from reelmachine.recipes.techreel.models import HeroVisual, TechReelInputs
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
        {"atMs": 0, "state": "center", "text": None, "count": 3, "labels": []},
        {"atMs": line0 + 200, "state": "pulse", "text": None, "count": 3, "labels": []},
    ]
    assert all(v["kind"] != "hero" for v in doc["beats"][0]["visuals"])


def test_hero_states_without_hero_spec_and_hero_at_zero():
    inputs = _inputs(hero=None)
    plan = schedule(inputs.beats, [[1200, 600], [500]])
    assert reel_document(inputs, plan, _times(inputs))["heroStates"] == []
    inputs = _inputs()
    inputs.beats[0].visuals[0].at = None
    doc = reel_document(inputs, plan, _times(inputs))
    assert doc["heroStates"] == [{"atMs": 0, "state": "pulse", "text": None, "count": 3, "labels": []}]


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
    inputs = _inputs()
    plan = schedule(inputs.beats, [[1200, 600], [500]])
    doc = reel_document(inputs, plan, _times(inputs, step=200))      # 200 ms a word: no chunk flashes
    caps = doc["captions"]
    assert caps == sorted(caps, key=lambda c: c["startMs"])
    for a, b in itertools.pairwise(caps):
        assert a["endMs"] <= b["startMs"]
    first = caps[0]
    s0 = plan.beats[0].lines[0]
    assert first["startMs"] == s0.start_ms
    assert [w["t"] for w in first["words"]] == ["one", "two", "three", "four", "five", "six"]
    assert first["endMs"] == s0.start_ms + 1200                # the next chunk's first start
    assert first["words"][0] == {"t": "one", "a": False, "startMs": s0.start_ms, "endMs": s0.start_ms + 200}
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
    assert look_tokens(_inputs(accent="#ffd400"))["accent"] == "#ffd400"
    assert LOOKS["mint"]["accent"] == "#2ee6b8"               # the table is not mutated


def _luminance(hex_colour):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("look", ["mint", "ember", "violet", "mono"])
def test_look_tokens_accent_ink_is_the_higher_contrast_of_black_and_white(look):
    # every built-in accent is bright enough that black reads better on it than white (ember ~6.7:1 vs ~3.1:1)
    tokens = look_tokens(_inputs(look=look))
    assert tokens["accentInk"] == LOOKS[look]["accentInk"] == "#000000"
    assert _contrast(tokens["accent"], "#000000") > _contrast(tokens["accent"], "#ffffff")
    assert tokens["accent"] == LOOKS[look]["accent"]          # every built-in accent already reads on its bg


def test_look_tokens_custom_accent_ink():
    assert look_tokens(_inputs(accent="#ffd400"))["accentInk"] == "#000000"     # 14.7:1 vs 1.4:1
    assert look_tokens(_inputs(accent="#e0245e"))["accentInk"] == "#000000"     # 4.583:1 vs 4.582:1
    blue = look_tokens(_inputs(accent="#1d4ed8"))                               # lightened to 3:1, still dark
    assert blue["accentInk"] == "#ffffff"
    assert _contrast(blue["accent"], "#ffffff") > _contrast(blue["accent"], "#000000")


def test_look_tokens_lightens_a_dim_accent_to_three_to_one():
    tokens = look_tokens(_inputs(accent="#123456"))          # ~1.6:1 on mint's bg
    accent, bg = tokens["accent"], tokens["bg"]
    assert _contrast("#123456", bg) < 3
    assert 3 <= _contrast(accent, bg) < 3.15                  # lightened only as far as it must
    orig = [int("#123456"[i:i + 2], 16) for i in (1, 3, 5)]
    new = [int(accent[i:i + 2], 16) for i in (1, 3, 5)]
    assert all(n >= o for n, o in zip(new, orig))             # toward white, the hue kept
    assert new[2] > new[1] > new[0]
    ink = max(("#000000", "#ffffff"), key=lambda c: _contrast(accent, c))
    assert tokens["accentInk"] == ink
    assert look_tokens(_inputs(accent="#000000", look="mono"))["accent"] != "#000000"


def test_write_project(tmp_path, monkeypatch):
    rt = tmp_path / "rt"
    rt.mkdir()
    (rt / "main.mjs").write_text("// m")
    (rt / "reel.css").write_text(".htitle { font-family: var(--display); } /* c */")
    monkeypatch.setattr(compose, "_runtime_paths", lambda: sorted(rt.iterdir()))
    _, _, doc = _doc()
    doc["beats"][0]["heading"][0]["t"] = "<!--<script>"
    out = write_project(doc, tmp_path / "proj")
    html = (out / "index.html").read_text()
    assert (out / "runtime" / "main.mjs").exists() and (out / "runtime" / "reel.css").exists()
    assert 'data-composition-id="techreel"' in html and "buildReady = { techreel:" in html
    # the look is inlined, not linked: HyperFrames' render compiler only sees font families in the page itself
    # (a linked sheet left the display font unfetched, so full renders fell back to a regular face)
    assert "<style>\n.htitle { font-family: var(--display); } /* c */" in html
    assert "stylesheet" not in html
    data = re.search(r'<script id="reel-data" type="application/json">(.*?)</script>', html, re.DOTALL).group(1)
    assert "<" not in data
    assert json.loads(data)["beats"][0]["heading"][0]["t"] == "<!--<script>"
    # rewriting replaces the directory
    write_project(doc, out)
    # a runtime without its look is a broken install, not a page with no styles
    (rt / "reel.css").unlink()
    with pytest.raises(FileNotFoundError, match="reel.css"):
        write_project(doc, tmp_path / "proj2")


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


def test_the_runtime_unit_tests_pass():
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    proc = subprocess.run(["node", "--test", str(compose.RUNTIME_DIR / "tests" / "pure.test.mjs")],
                          check=False, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_new_overlays_and_nested_camel():
    beats = [{"keyword": "GUIDE", "dm": "sent", "next": "part two",
              "lines": [{"say": "one two three"}],
              "visuals": [
                  {"kind": "error", "title": "Oops", "body": "bad"},
                  {"kind": "flash", "text": "boom"},
                  {"kind": "dialog", "title": "t", "click": "approve", "click_at": {"line": 0}},
                  {"kind": "chat", "messages": [{"from": "me", "text": "hi", "bot": True}]},
              ]}, {"lines": [{"say": "x"}]}]
    inputs = TechReelInputs.model_validate({"title": "t", "creator": {"name": "N", "handle": "@h"}, "beats": beats})
    plan = schedule(inputs.beats, [[900], [300]])
    doc = reel_document(inputs, plan, _times(inputs))
    assert doc["creator"] == {"name": "N", "handle": "@h", "avatar": None}
    b0, b1 = doc["beats"]
    assert (b0["keyword"], b0["dm"], b0["next"]) == ("GUIDE", "sent", "part two")
    assert (b1["keyword"], b1["dm"], b1["next"]) == ("", "", "")
    err, flash, dialog, chat = b0["visuals"]
    assert err["class"] == "overlay" and flash["class"] == "overlay" and dialog["class"] == "block"
    assert dialog["clickAtMs"] == plan.beats[0].lines[0].start_ms and "clickAt" not in dialog
    assert chat["messages"] == [{"from": "me", "text": "hi", "bot": True}]
    json.dumps(doc, allow_nan=False)


def test_creator_absent_is_null():
    _, _, doc = _doc()
    assert doc["creator"] is None


def test_hero_states_carry_split_count_and_labels():
    inputs = _inputs()
    inputs.beats[0].visuals[0] = HeroVisual(kind="hero", state="split", count=4, labels=["a", "b"])
    plan = schedule(inputs.beats, [[1200, 600], [500]])
    doc = reel_document(inputs, plan, _times(inputs))
    assert doc["heroStates"] == [{"atMs": 0, "state": "split", "text": None, "count": 4, "labels": ["a", "b"]}]


def test_hero_art_reaches_the_document():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle id="c" cx="5" cy="5" r="4"/></svg>'
    inputs = _inputs(hero={"kind": "svg", "svg": svg})
    plan = schedule(inputs.beats, [[1200, 600], [500]])
    hero = reel_document(inputs, plan, _times(inputs))["hero"]
    assert hero["kind"] == "svg" and "<circle" in hero["svg"] and 'id="c_hero.c"' in hero["svg"]
    assert "file" not in hero
    inputs = _inputs(hero={"kind": "image", "src": "https://x.test/bot.png"})
    hero = reel_document(inputs, plan, _times(inputs), images={"https://x.test/bot.png": "img-1.png"})["hero"]
    assert hero == {"kind": "image", "text": None, "file": "assets/img-1.png"}


def _cta(beat, creator=True, title="Pocket: local AI"):
    beats = [{"lines": [{"say": "first"}]}, {"kind": "cta", **beat}]
    data = {"title": title, "hero": {"kind": "orb"}, "beats": beats}
    if creator:
        data["creator"] = {"name": "N", "handle": "@h"}
    inputs = TechReelInputs.model_validate(data)
    plan = schedule(inputs.beats, [[300], [400] * len(inputs.beats[1].lines)])
    return plan, reel_document(inputs, plan, _times(inputs))


def test_cta_beat_without_visuals_gets_the_standard_ending():
    lines = [{"say": "comment pocket now"}, {"say": "follow for more"}, {"say": "see you"}]
    plan, doc = _cta({"lines": lines, "keyword": "POCKET", "dm": "link DM kar dunga", "next": "part *two*"})
    comment, follow, title = doc["beats"][1]["visuals"]
    s = plan.beats[1].lines
    assert (comment["id"], comment["kind"], comment["keyword"], comment["reply"]) == ("b1v0", "comment", "POCKET", "link DM kar dunga")
    assert comment["enterMs"] == s[0].start_ms and comment["class"] == "block"
    assert (follow["id"], follow["kind"], follow["enterMs"]) == ("b1v1", "follow", s[1].start_ms)
    assert (title["id"], title["kind"], title["text"], title["enterMs"]) == ("b1v2", "title", "part *two*", s[2].start_ms)
    assert comment["exitMs"] == plan.duration_ms


def test_cta_defaults_without_creator_keyword_or_next():
    _, doc = _cta({"lines": [{"say": "comment now"}]}, creator=False)
    (comment,) = doc["beats"][1]["visuals"]
    assert (comment["keyword"], comment["reply"]) == ("POCKET", "")


def test_cta_one_line_puts_follow_part_way_in():
    plan, doc = _cta({"lines": [{"say": "comment and follow"}], "next": "more"})
    comment, follow, title = doc["beats"][1]["visuals"]
    s = plan.beats[1].lines[0]
    assert comment["enterMs"] == s.start_ms
    assert follow["enterMs"] == s.start_ms + round(0.6 * s.speech_ms)
    assert s.start_ms < title["enterMs"] and title["enterMs"] != follow["enterMs"]


def test_cta_with_its_own_visuals_is_left_alone_and_hero_moves_do_not_count():
    _, doc = _cta({"lines": [{"say": "x"}], "visuals": [{"kind": "title", "text": "mine"}]})
    assert [v["kind"] for v in doc["beats"][1]["visuals"]] == ["title"]
    _, doc = _cta({"lines": [{"say": "x"}], "visuals": [{"kind": "hero", "state": "small-top"}]})
    assert [(v["id"], v["kind"]) for v in doc["beats"][1]["visuals"]] == [("b1v1", "comment"), ("b1v2", "follow")]


def test_cta_keyword_from_a_title_strips_punctuation_and_fits():
    _, doc = _cta({"lines": [{"say": "x"}]}, creator=False, title="«Supercalifragilisticexpialidocious-ish» rocks")
    assert doc["beats"][1]["visuals"][0]["keyword"] == "SUPERCALIFRAGILISTICEXPI"


def test_cta_keyword_skips_all_digit_words():
    _, doc = _cta({"lines": [{"say": "x"}]}, creator=False, title="5 AI tools")
    assert doc["beats"][1]["visuals"][0]["keyword"] == "AI"


def test_merge_short_joins_a_flash_chunk_to_the_next():
    times = [(0, 400), (400, 520), (520, 900), (900, 1300)]
    assert merge_short([[0], [1], [2, 3]], times) == [[0], [1, 2, 3]]


def test_merge_short_last_chunk_joins_the_previous():
    times = [(0, 500), (500, 900), (900, 1000)]
    assert merge_short([[0, 1], [2]], times) == [[0, 1, 2]]


def test_merge_short_never_exceeds_the_cap():
    times = [(i * 100, i * 100 + 100) for i in range(12)]
    chunks_in = [[i] for i in range(12)]
    out = merge_short(chunks_in, times)
    assert [i for c in out for i in c] == list(range(12))
    assert all(len(c) <= 8 for c in out)
    # a short chunk whose merge would break the cap stays alone
    assert merge_short([[0, 1, 2, 3, 4, 5, 6], [7, 8, 9]], [(i * 10, i * 10 + 10) for i in range(10)]) == [
        [0, 1, 2, 3, 4, 5, 6], [7, 8, 9]]


def test_merge_short_unspaced_counts_characters():
    us = units("あいうえおかきくけこさしすせそたちつてと", "ja")
    times = [(i * 10, i * 10 + 10) for i in range(len(us))]
    out = merge_short([[i] for i in range(len(us))], times, us=us)
    assert [i for c in out for i in c] == list(range(len(us)))
    assert all(sum(len(us[i].text) for i in c) <= 18 for c in out)


def test_merge_short_measures_time_on_screen_not_speaking_time():
    # the first chunk is spoken in 100 ms but stays up through a 500 ms pause: it is not a flash
    times = [(0, 100), (600, 700), (700, 1200)]
    assert merge_short([[0], [1, 2]], times) == [[0], [1, 2]]
    # a last chunk is on screen to the line's end
    assert merge_short([[0, 1], [2]], [(0, 500), (500, 900), (900, 1300)]) == [[0, 1], [2]]


def test_merge_short_unspaced_two_character_units():
    us = [Unit("あい", False) for _ in range(10)]
    times = [(i * 10, i * 10 + 10) for i in range(10)]
    out = merge_short([[i] for i in range(10)], times, us=us)
    assert [i for c in out for i in c] == list(range(10))
    assert all(sum(len(us[i].text) for i in c) <= 18 for c in out) and len(out) == 2


def test_a_flash_chunk_in_the_document_is_merged():
    inputs = _inputs()
    plan = schedule(inputs.beats, [[1200, 600], [500]])
    doc = reel_document(inputs, plan, _times(inputs))     # 100 ms a word: "seven eight" would flash alone
    assert [w["t"] for w in doc["captions"][0]["words"]][-2:] == ["seven", "eight"]
    assert len(doc["captions"][0]["words"]) == 8


def test_partial_word_accents_reach_the_document_as_spans():
    inputs = TechReelInputs.model_validate({"title": "t", "beats": [
        {"heading": "*R*etrieval *A*ugmented", "lines": [{"say": "retrieval rocks", "text": "*R*etrieval rocks"}]}]})
    plan = schedule(inputs.beats, [[800]])
    doc = reel_document(inputs, plan, _times(inputs))
    assert doc["beats"][0]["heading"] == [{"t": "Retrieval", "a": False, "accentSpans": [[0, 1]]},
                                          {"t": "Augmented", "a": False, "accentSpans": [[0, 1]]}]
    words = doc["captions"][0]["words"]
    assert words[0]["accentSpans"] == [[0, 1]] and "accentSpans" not in words[1]
