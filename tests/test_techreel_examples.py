"""The shipped techreel examples lint spotless and together show the whole catalogue; the guide reaches an agent."""

import dataclasses
import json
import re
from pathlib import Path
from typing import get_args

import pytest

from reelmachine.config import get_settings
from reelmachine.recipes import techreel
from reelmachine.recipes.techreel import models
from reelmachine.recipes.techreel.guide import GUIDE
from reelmachine.recipes.techreel.lint import LENGTH_MS, check
from reelmachine.recipes.techreel.models import TechReelInputs
from reelmachine.recipes.techreel.schedule import estimate

EXAMPLES = sorted((Path(techreel.__file__).parent / "examples").glob("*.json"))
NAMES = {"hi-pocket-llm.json", "en-what-is-rag.json", "ja-tiny-models.json"}


def load(path: Path) -> TechReelInputs:
    return TechReelInputs.model_validate_json(path.read_text(encoding="utf-8"))


def every_kind() -> set[str]:
    union = get_args(models.Visual)[0]
    return {get_args(cls.model_fields["kind"].annotation)[0] for cls in get_args(union)}


def test_three_examples_ship_and_the_guide_names_them():
    assert {p.name for p in EXAMPLES} == NAMES
    assert set(GUIDE["examples"]) == NAMES


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_lints_spotless(path):
    inputs = load(path)
    # as a remote caller would build it: full mode, no local files
    found = check(inputs.model_copy(update={"mode": "full"}), "fake", allow_local=False)
    assert [(f.rule, f.path, f.message) for f in found] == []


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_says_no_sentence_end_inside_a_line(path):
    # Supertonic pauses a second or more there, which fails verify's silence check
    assert [f.path for f in check(load(path)) if f.rule == "say-pause"] == []


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_runs_45_to_90_seconds(path):
    ms = estimate(load(path)).duration_ms
    assert 45_000 <= ms <= 90_000 and LENGTH_MS[0] <= ms <= LENGTH_MS[1]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_creator_is_a_placeholder(path):
    inputs = load(path)
    assert inputs.creator is not None and (inputs.creator.name, inputs.creator.handle) == ("Your Name", "@yourhandle")
    assert inputs.sources


def test_the_examples_together_use_every_visual_kind():
    used = {v.kind for p in EXAMPLES for b in load(p).beats for v in b.visuals}
    assert used == every_kind()


def test_the_examples_show_three_heroes_and_every_hero_state():
    heroes = {load(p).hero.kind for p in EXAMPLES}
    assert len(heroes) == len(EXAMPLES)        # one hero per reel, a different one in each
    states = {v.state for p in EXAMPLES for b in load(p).beats for v in b.visuals if v.kind == "hero"}
    assert states == set(get_args(models.HeroState))


def test_the_recipe_example_is_the_hindi_file():
    from reelmachine.recipes.techreel.recipe import EXAMPLE

    assert EXAMPLE == json.loads((EXAMPLES[0].parent / "hi-pocket-llm.json").read_text(encoding="utf-8"))


def test_the_guide_lists_every_component_with_a_valid_example():
    components = GUIDE["components"]
    assert set(components) == every_kind()
    for kind, entry in components.items():
        assert entry["what"] and entry["example"]["kind"] == kind
        assert set(entry) == {"what", "required", "optional", "example"}
    assert components["counter"]["required"] == {"to": "number"}
    assert "from" in components["counter"]["optional"]           # aliases, as an agent writes them
    assert "from" in components["chat"]["required"]["messages"]
    assert "place" in components["emoji"]["optional"] and "place" not in components["title"]["optional"]


def test_the_guide_has_every_section():
    assert {"workflow", "research", "formula", "humour", "visuals", "language", "components", "heroes", "cta",
            "examples"} <= set(GUIDE)
    assert len(GUIDE["humour"]["examples"]) == 12
    for lang in ("hi:", "en:", "ja:"):
        assert sum(j.startswith(lang) for j in GUIDE["humour"]["examples"]) >= 3
    assert set(GUIDE["language"]["numbers"]) == {"en", "hi", "ja"}
    text = json.dumps(GUIDE["language"], ensure_ascii=False)
    for needle in ("एआई", "एल एल एम", "supertonic:F1", "supertonic:M5", "REEL_TTS=supertonic", "GPT-4o", "H100",
                   "Llama 3.1"):
        assert needle in text
    assert set(GUIDE["cta"]["patterns"]) == {"en", "hi", "ja"}
    json.dumps(GUIDE)                                             # plain data an MCP reply can carry


def test_the_humour_examples_quote_the_example_files():
    lines = {t for p in EXAMPLES for b in load(p).beats for ln in b.lines for t in (ln.say, ln.text) if t}
    for joke in GUIDE["humour"]["examples"]:
        quotes = re.findall(r'"([^"]+)"', joke)
        assert quotes and all(q in lines for q in quotes), joke


# ---------------------------------------------------------------- over MCP

def _tools(tmp_path):
    pytest.importorskip("mcp")
    from reelmachine.engine import Engine
    from reelmachine.mcp.tenants import TenantRegistry
    from reelmachine.mcp.tools import McpTools

    settings = dataclasses.replace(get_settings(), workdir=tmp_path / "w", outdir=tmp_path / "out",
                                   shared_cache=tmp_path / "shared", tts="fake")
    engine = Engine(settings)
    return engine, McpTools(engine=engine, tenants=TenantRegistry(default_caller="local"))


def test_the_recipe_is_described_with_its_guide(tmp_path):
    engine, tools = _tools(tmp_path)
    try:
        described = tools.describe_recipe(None, "techreel")
    finally:
        engine.close()
    guide = described["guide"]
    assert set(guide["components"]) == every_kind()
    assert "storyboard" in json.dumps(guide["workflow"])
    assert described["example"]["language"] == "hi"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_probe_passes_the_lint_of_every_example(tmp_path, path):
    engine, tools = _tools(tmp_path)
    try:
        report = tools.probe(None, "techreel", json.loads(path.read_text(encoding="utf-8")))
    finally:
        engine.close()
    checks = {c["name"]: c for c in report["checks"]}
    assert checks["lint"]["ok"] and checks["lint"]["detail"] == "0 warnings"
    assert checks["assets"]["ok"] and checks["voice"]["ok"]
    assert 45_000 <= report["details"]["estimatedMs"] <= 90_000
