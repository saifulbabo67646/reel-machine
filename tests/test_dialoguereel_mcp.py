"""dialoguereel over MCP: make_cast saves a reusable cast per caller and starts a preview; the guide."""

import copy
import dataclasses
import json
from contextlib import contextmanager

import pytest

from reelmachine.config import get_settings
from reelmachine.recipes.dialoguereel.recipe import EXAMPLE

pytest.importorskip("mcp")  # every test here drives the MCP tools

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

CHARACTERS = [
    {"id": "kid", "name": "Cupcake", "side": "left", "source": {"kind": "builtin", "name": "cupcake"}},
    {"id": "mom", "name": "Mug", "side": "right", "source": {"kind": "builtin", "name": "mug"}},
]


def with_svg(characters, index, svg):
    out = copy.deepcopy(characters)
    out[index]["source"] = {"kind": "svg", "svg": svg}
    return out


@contextmanager
def _tools(tmp_path, *, callers=("local",), run_jobs=False):
    """McpTools over one engine, one per caller. Unless `run_jobs`, submitted jobs are recorded, not run."""
    from reelmachine.core.job import Job
    from reelmachine.engine import Engine
    from reelmachine.mcp.tenants import TenantRegistry
    from reelmachine.mcp.tools import McpTools

    settings = dataclasses.replace(get_settings(), workdir=tmp_path / "w", outdir=tmp_path / "out",
                                   shared_cache=tmp_path / "shared", tts="fake")
    engine = Engine(settings, provider_overrides={"renderer": "explainer3d-fake"})
    submitted = []
    if not run_jobs:
        def record(request, *, caller="local"):
            submitted.append((request, caller))
            return Job(id=f"job_{len(submitted)}", recipe=request.recipe, caller=caller, request=request)

        engine.submit = record
    try:
        tools = [McpTools(engine=engine, tenants=TenantRegistry(default_caller=c)) for c in callers]
        for t in tools:
            t.submitted = submitted
        yield tools[0] if len(tools) == 1 else tools
    finally:
        engine.close()


def test_make_cast_returns_a_reusable_id_and_a_preview_job(tmp_path):
    with _tools(tmp_path) as tools:
        out = tools.make_cast(None, CHARACTERS)
        again = tools.make_cast(None, CHARACTERS, preview=False)
        submitted = tools.submitted
        saved = tools.engine.store.caller_dir("local") / "casts" / f"{out['castId']}.json"
    assert out["schemaVersion"] == 1 and out["castId"].startswith("cast_")
    assert out["castId"] == again["castId"] and out["previewJobId"] and again["previewJobId"] is None
    assert {c["id"] for c in out["characters"]} == {"kid", "mom"}
    kid = next(c for c in out["characters"] if c["id"] == "kid")
    assert kid == {"id": "kid", "name": "Cupcake", "side": "left", "voice": "fake:default", "parts": kid["parts"]}
    assert "arm-right" in kid["parts"]
    assert saved.is_file()
    assert len(submitted) == 1  # preview=False starts nothing
    request, caller = submitted[0]
    assert caller == "local" and request.recipe == "dialoguereel"
    assert request.inputs == {"mode": "cast", "cast": out["castId"], "lines": []}
    assert "castId" in out["hint"] and "cast.png" in out["hint"]


def test_make_cast_reports_bad_svg_with_its_path(tmp_path):
    with _tools(tmp_path) as tools, pytest.raises(ToolError) as err:
        tools.make_cast(None, with_svg(CHARACTERS, 0, '<svg viewBox="0 0 400 400"><script/></svg>'))
    payload = json.loads(str(err.value))
    assert payload["code"] == "INVALID_INPUT" and "characters[0]" in payload["details"]["path"]
    assert payload["details"]["path"] == "characters[0].source.svg"


def test_make_cast_reports_a_bad_cast_shape_as_invalid_input(tmp_path):
    with _tools(tmp_path) as tools, pytest.raises(ToolError) as err:
        tools.make_cast(None, CHARACTERS[:1])
    payload = json.loads(str(err.value))
    assert payload["code"] == "INVALID_INPUT" and "two" in payload["message"]
    with _tools(tmp_path / "b") as tools, pytest.raises(ToolError) as err:
        tools.make_cast(None, [CHARACTERS[0], {**CHARACTERS[1], "source": {"kind": "builtin", "name": "teapot"}}])
    payload = json.loads(str(err.value))
    assert payload["details"]["path"] == "characters[1].source.name" and "cupcake" in payload["message"]


def test_casts_are_private_to_their_caller(tmp_path):
    with _tools(tmp_path, callers=("a", "b")) as (alice, bob):
        cast = alice.make_cast(None, CHARACTERS, preview=False)["castId"]
        inputs = EXAMPLE | {"cast": cast}
        mine = alice.probe(None, "dialoguereel", inputs)
        theirs = bob.probe(None, "dialoguereel", inputs)
    assert next(c for c in mine["checks"] if c["name"] == "script")["ok"]
    script = next(c for c in theirs["checks"] if c["name"] == "script")
    assert not script["ok"] and "no saved cast" in script["detail"]


def test_the_guide_example_svg_passes_the_contract():
    from reelmachine.recipes.dialoguereel.guide import GUIDE
    from reelmachine.recipes.dialoguereel.svg import REQUIRED, check_svg

    example = GUIDE["characters"]["example"]
    parts = check_svg(example, path="x")
    assert set(parts) >= {"arm-left", "arm-right"}
    text = json.dumps(GUIDE["characters"])
    for name in REQUIRED:
        assert name in text
    assert "<style>" in text and "viewBox" in text and "60" in text  # the refusals and limits are spelled out


def test_the_recipe_is_described_with_its_guide(tmp_path):
    from reelmachine.recipes.dialoguereel.library import builtin_names

    with _tools(tmp_path) as tools:
        described = tools.describe_recipe(None, "dialoguereel")
    guide = described["guide"]
    assert {"workflow", "builtins", "characters", "lines", "voices"} <= set(guide)
    assert guide["builtins"] == builtin_names()
    assert "make_cast" in json.dumps(guide["workflow"])
    assert "{" in guide["lines"]["ruby"]["example"]


def test_make_cast_is_registered_on_the_server():
    from reelmachine.mcp.server import DESCRIPTIONS

    assert "make_cast" in DESCRIPTIONS and "cupcake" in DESCRIPTIONS["make_cast"]


@pytest.mark.slow
def test_the_preview_job_makes_a_cast_sheet(tmp_path):
    with _tools(tmp_path, run_jobs=True) as tools:
        out = tools.make_cast(None, CHARACTERS)
        job = tools.engine.wait(out["previewJobId"], caller="local", timeout_s=300)
        assert job.state.value == "succeeded", job.error
        art = tools.get_artifact(None, out["previewJobId"], "cast.png")
    assert art["bytes"] > 0
