"""The MCP server: tool surface, both transports, tenancy, latency, secrets.

The acceptance walk is `test_in_memory_walk`: list recipes → describe → probe → start →
poll → artifact URL → cancel, driven by the SDK's real `Client`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import os
import sys
import threading
import time
from importlib.metadata import EntryPoint, entry_points as real_entry_points
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")  # the MCP extra is optional; without it these tests skip

import httpx2  # noqa: E402
import uvicorn  # noqa: E402
from mcp import Client, StdioServerParameters  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from reelmachine.config import get_settings
from reelmachine.core.errors import ProviderUnavailable
from reelmachine.core.job import CallerPolicy, JobRequest, JobState
from reelmachine.core.registry import Registry
from reelmachine.engine import Engine
from reelmachine.mcp.artifacts import ArtifactSigner
from reelmachine.mcp.server import build_server
from reelmachine.mcp.tenants import Tenant, TenantRegistry
from reelmachine.mcp.tools import McpTools
from reelmachine.observability import configure_logging

# imported first so the fixture entry point resolves from sys.modules
from engine_fixture_recipe import EngineFixtureRecipe  # noqa: F401

TOOL_NAMES = {
    "list_recipes",
    "describe_recipe",
    "probe",
    "start_job",
    "get_job",
    "list_jobs",
    "cancel_job",
    "get_artifact",
}

FIXTURE_ENTRY = EntryPoint(
    name="engine-fixture",
    value="engine_fixture_recipe:EngineFixtureRecipe",
    group="reelmachine.recipes",
)

ALICE = "alice-token-123456"
BOB = "bob-token-654321"


def _registry() -> Registry:
    def provider(group: str):
        points = list(real_entry_points(group=group))
        if group == "reelmachine.recipes":
            points.append(FIXTURE_ENTRY)
        return points

    return Registry(entry_points=provider)


def _tenants() -> TenantRegistry:
    return TenantRegistry(
        [
            Tenant(id="alice", token_sha256=TenantRegistry.hash_token(ALICE)),
            Tenant(id="bob", token_sha256=TenantRegistry.hash_token(BOB)),
        ]
    )


@pytest.fixture
def env(tmp_path):
    settings = dataclasses.replace(
        get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out", mock_dir=tmp_path / "mock"
    )
    tenants = _tenants()
    engine = Engine(settings, registry=_registry(), policy_for=tenants.policy)
    yield SimpleNamespace(settings=settings, tenants=tenants, engine=engine, tmp=tmp_path)
    engine.close()


def _run(coro):
    return asyncio.run(coro)


def _payload(result) -> dict:
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def _local_server(env):
    """The server for the in-memory transport, which carries no headers (so: no tokens)."""
    return build_server(engine=env.engine, tenants=TenantRegistry(), transport="stdio")


def _token_tools(env, token: str = ALICE):
    """The tools as an authenticated caller — for calls the in-memory transport can't carry."""
    tools = McpTools(engine=env.engine, tenants=env.tenants, transport="stdio")
    ctx = SimpleNamespace(headers={"authorization": f"Bearer {token}"})
    return tools, ctx


# ------------------------------------------------------------------- the surface


def test_tool_surface_is_exactly_the_eight(env) -> None:
    async def main():
        server = _local_server(env)
        async with Client(server) as client:
            listed = await client.list_tools()
            return listed.tools

    tools = _run(main())
    assert {tool.name for tool in tools} == TOOL_NAMES
    for tool in tools:
        assert tool.description and "Example:" in tool.description
        assert tool.input_schema.get("type") == "object"
    schema = next(tool for tool in tools if tool.name == "start_job").input_schema
    assert {"recipe", "inputs", "idempotency_key", "config"} <= set(schema["properties"])
    assert "ctx" not in json.dumps(schema)


def test_in_memory_walk_list_describe_probe_start_poll_artifact_cancel(env) -> None:
    async def main():
        server = _local_server(env)
        async with Client(server) as client:
            recipes = _payload(await client.call_tool("list_recipes", {}))
            described = _payload(
                await client.call_tool("describe_recipe", {"recipe_id": "nadeshiko-cut"})
            )
            probed = _payload(
                await client.call_tool(
                    "probe",
                    {"recipe_id": "engine-fixture", "inputs": {"value": "hi"}},
                )
            )
            started = _payload(
                await client.call_tool(
                    "start_job",
                    {"recipe": "engine-fixture", "inputs": {"value": "hi", "sleep_s": 0.3}},
                )
            )
            job_id = started["jobId"]
            for _ in range(200):
                job = _payload(await client.call_tool("get_job", {"job_id": job_id}))
                if job["state"] in ("succeeded", "failed", "cancelled"):
                    break
                await asyncio.sleep(0.05)
            artifact = _payload(
                await client.call_tool(
                    "get_artifact", {"job_id": job_id, "name": "manifest.json"}
                )
            )
            slow = _payload(
                await client.call_tool(
                    "start_job",
                    {"recipe": "engine-fixture", "inputs": {"sleep_s": 5.0}},
                )
            )
            await client.call_tool("cancel_job", {"job_id": slow["jobId"]})
            for _ in range(200):
                cancelled = _payload(
                    await client.call_tool("get_job", {"job_id": slow["jobId"]})
                )
                if cancelled["state"] in ("cancelled", "failed"):
                    break
                await asyncio.sleep(0.05)
            listed = _payload(await client.call_tool("list_jobs", {"limit": 10}))
            return recipes, described, probed, started, job, artifact, cancelled, listed

    recipes, described, probed, started, job, artifact, cancelled, listed = _run(main())

    assert {row["id"] for row in recipes["recipes"]} >= {"nadeshiko-cut", "engine-fixture"}
    assert recipes["schemaVersion"] == 1
    assert described["inputSchema"]["properties"]["word"]
    assert described["providers"]["corpus"]["group"] == "corpora"
    assert described["cost"]["unit"] == "nadeshiko_requests"
    assert probed["ok"] is True and probed["quota_free"] is True

    assert started["state"] in ("queued", "running")
    assert job["state"] == "succeeded"
    assert job["progress"]["stage"] == "done"
    assert any(a["name"] == "manifest.json" for a in job["artifacts"])

    assert artifact["name"] == "manifest.json"
    assert artifact["bytes"] > 0 and artifact["sha256"]
    assert artifact["url"].startswith("file://")  # stdio has no listener

    assert cancelled["state"] == "cancelled"
    assert cancelled["error"]["code"] == "CANCELLED"
    assert len(listed["jobs"]) == 2


def test_duplicate_idempotency_key_yields_one_job_and_one_artifact(env) -> None:
    async def main():
        server = _local_server(env)
        async with Client(server) as client:
            first = _payload(
                await client.call_tool(
                    "start_job",
                    {
                        "recipe": "engine-fixture",
                        "inputs": {"value": "x"},
                        "idempotency_key": "same-key",
                    },
                )
            )
            second = _payload(
                await client.call_tool(
                    "start_job",
                    {
                        "recipe": "engine-fixture",
                        "inputs": {"value": "x"},
                        "idempotency_key": "same-key",
                    },
                )
            )
            for _ in range(200):
                job = _payload(await client.call_tool("get_job", {"job_id": first["jobId"]}))
                if job["state"] in ("succeeded", "failed"):
                    break
                await asyncio.sleep(0.05)
            listed = _payload(await client.call_tool("list_jobs", {}))
            artifact = _payload(
                await client.call_tool(
                    "get_artifact", {"job_id": first["jobId"], "name": "manifest.json"}
                )
            )
            return first, second, job, listed, artifact

    first, second, job, listed, artifact = _run(main())
    assert first["jobId"] == second["jobId"]
    assert len(listed["jobs"]) == 1
    assert job["state"] == "succeeded"
    assert artifact["sha256"]


def test_quota_refusal_is_a_clean_structured_error(tmp_path) -> None:
    settings = dataclasses.replace(get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out")
    tenants = _tenants()

    def policy_for(caller: str) -> CallerPolicy:
        base = tenants.policy(caller)
        if caller == "alice":
            return base.model_copy(update={"quotas": {"nadeshiko": 10}})
        return base

    engine = Engine(settings, registry=_registry(), policy_for=policy_for)
    try:
        ledger = engine.store.root() / "alice" / "quota" / "nadeshiko.json"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(json.dumps({"2026-10": 10}), encoding="utf-8")
        env = SimpleNamespace(engine=engine, tenants=tenants, tmp=tmp_path, settings=settings)
        tools, ctx = _token_tools(env)
        from mcp.server.mcpserver.exceptions import ToolError

        with pytest.raises(ToolError) as excinfo:
            tools.start_job(ctx, "engine-fixture", {"value": "x"})
        payload = json.loads(str(excinfo.value))
        assert payload["code"] == "QUOTA_EXCEEDED"
        assert payload["hint"]
    finally:
        engine.close()


def test_every_tool_call_returns_in_milliseconds(env) -> None:
    async def main():
        server = _local_server(env)
        async with Client(server) as client:
            started = _payload(
                await client.call_tool(
                    "start_job", {"recipe": "engine-fixture", "inputs": {"sleep_s": 2.0}}
                )
            )
            timings = {}
            for name, arguments in (
                ("list_recipes", {}),
                ("describe_recipe", {"recipe_id": "engine-fixture"}),
                ("get_job", {"job_id": started["jobId"]}),
                ("list_jobs", {}),
                ("start_job", {"recipe": "engine-fixture", "inputs": {}}),
            ):
                begin = time.monotonic()
                await client.call_tool(name, arguments)
                timings[name] = (time.monotonic() - begin) * 1000
            return timings, started

    timings, started = _run(main())
    assert started["state"] in ("queued", "running")
    for name, ms in timings.items():
        assert ms < 500, f"{name} took {ms:.0f} ms; no tool call may wait on a render"


# --------------------------------------------------------------- http transport


class _HttpServer:
    def __init__(self, server) -> None:
        app = server.streamable_http_app(host="127.0.0.1")
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        self.uvicorn = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.uvicorn.run, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.uvicorn.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert self.uvicorn.started, "the HTTP server did not start"
        port = self.uvicorn.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def __exit__(self, *exc) -> None:
        self.uvicorn.should_exit = True
        self.thread.join(timeout=10)


def _client_for(base: str, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    http_client = httpx2.AsyncClient(headers=headers)
    return Client(streamable_http_client(f"{base}/mcp", http_client=http_client))


def test_http_transport_isolates_callers_and_signs_artifacts(env) -> None:
    signer = ArtifactSigner(key="test-signing-key", ttl_s=60)
    server = build_server(
        engine=env.engine,
        tenants=env.tenants,
        signer=signer,
        transport="streamable-http",
    )

    async def main(base: str):
        async with _client_for(base, ALICE) as alice:
            started = _payload(
                await alice.call_tool(
                    "start_job", {"recipe": "engine-fixture", "inputs": {"value": "alice"}}
                )
            )
            job_id = started["jobId"]
            for _ in range(200):
                job = _payload(await alice.call_tool("get_job", {"job_id": job_id}))
                if job["state"] == "succeeded":
                    break
                await asyncio.sleep(0.05)
            assert job["caller"] == "alice"
            artifact = _payload(
                await alice.call_tool("get_artifact", {"job_id": job_id, "name": "manifest.json"})
            )
            url = artifact["url"]
            assert url.startswith(base + "/artifacts/")
            assert artifact["expiresAt"] > time.time()

            tampered_url, _ = ArtifactSigner(key="wrong-key", ttl_s=60).url(
                base, "alice", job_id, "manifest.json"
            )
            expired_url, _ = signer.url(
                base, "alice", job_id, "manifest.json", now=int(time.time()) - 10_000
            )
            async with httpx2.AsyncClient() as plain:
                ok = await plain.get(url)
                tampered = await plain.get(tampered_url)
                expired = await plain.get(expired_url)

        async with _client_for(base, BOB) as bob:
            foreign = await bob.call_tool("get_job", {"job_id": job_id})
            bob_jobs = _payload(await bob.call_tool("list_jobs", {}))
            foreign_artifact = await bob.call_tool(
                "get_artifact", {"job_id": job_id, "name": "manifest.json"}
            )

        async with _client_for(base, None) as anonymous:
            unauthenticated = await anonymous.call_tool("list_recipes", {})

        return ok, tampered, expired, foreign, bob_jobs, foreign_artifact, unauthenticated

    with _HttpServer(server) as base:
        ok, tampered, expired, foreign, bob_jobs, foreign_artifact, unauthenticated = _run(main(base))

    assert ok.status_code == 200 and ok.content
    assert tampered.status_code == 403
    assert expired.status_code == 403
    assert foreign.is_error and "NOT_FOUND" in foreign.content[0].text
    assert foreign_artifact.is_error and "NOT_FOUND" in foreign_artifact.content[0].text
    assert bob_jobs["jobs"] == []
    assert unauthenticated.is_error and "UNAUTHORIZED" in unauthenticated.content[0].text


# -------------------------------------------------------------- stdio transport


def test_stdio_transport_drives_a_real_client(env) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "reelmachine.mcp.server", "--transport", "stdio"],
        env={
            **os.environ,
            "REEL_WORKDIR": str(env.tmp / "stdio-work"),
            "REEL_MCP_CALLER": "alice",
        },
    )

    async def main():
        async with Client(parameters) as client:
            return _payload(await client.call_tool("list_recipes", {}))

    payload = _run(main())
    assert any(row["id"] == "nadeshiko-cut" for row in payload["recipes"])


# ------------------------------------------------------------------- secrets


def test_no_configured_secret_reaches_store_logs_or_tool_results(env, monkeypatch) -> None:
    sentinel = "sentinel-secret-987654"
    monkeypatch.setenv("NADESHIKO_API_KEY", sentinel)
    stream = io.StringIO()
    configure_logging(stream=stream, level="DEBUG")

    import engine_fixture_recipe as fixture

    def leaky(self, ctx, payload):
        raise ProviderUnavailable(
            f"upstream rejected {sentinel}",
            hint=f"rotate {sentinel}",
            stage="emit",
            details={"key": sentinel},
        )

    monkeypatch.setattr(fixture.EmitStage, "run", leaky)

    job = env.engine.submit(JobRequest(recipe="engine-fixture", inputs={}), caller="alice")
    finished = env.engine.wait(job.id, caller="alice", timeout_s=15)
    assert finished.state is JobState.FAILED

    tools, ctx = _token_tools(env)
    payloads = [
        tools.list_recipes(ctx),
        tools.get_job(ctx, job.id),
        tools.list_jobs(ctx),
    ]
    tool_blob = json.dumps(payloads, ensure_ascii=False)

    store_blob = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (env.settings.workdir / "jobs").rglob("*")
        if path.is_file()
    )

    assert sentinel not in store_blob, "a secret reached the job store"
    assert sentinel not in stream.getvalue(), "a secret reached the logs"
    assert sentinel not in tool_blob, "a secret reached a tool result"
    assert "***" in finished.error.message
