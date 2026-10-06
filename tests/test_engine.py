"""The engine: submit/get/wait/cancel, durability, limits, isolation, manifest."""

from __future__ import annotations

import dataclasses
import json
import time
from importlib.metadata import EntryPoint, entry_points as real_entry_points
from pathlib import Path

import pytest

from reelmachine.config import get_settings
from reelmachine.core.errors import (
    ConcurrencyLimit,
    DiskLimit,
    InvalidInput,
    NotFound,
    ProviderUnavailable,
    QuotaExceededError,
)
from reelmachine.core.job import CallerPolicy, JobRequest, JobState
from reelmachine.core.manifest import Manifest
from reelmachine.core.registry import Registry
from reelmachine.engine import Engine
from reelmachine.jobs.local import LocalJobStore

# imported first so the entry point below can be loaded from sys.modules
from engine_fixture_recipe import EngineFixtureRecipe  # noqa: F401

FIXTURE_ENTRY = EntryPoint(
    name="engine-fixture",
    value="engine_fixture_recipe:EngineFixtureRecipe",
    group="reelmachine.recipes",
)


def _registry(extra: bool = True) -> Registry:
    def provider(group: str):
        points = list(real_entry_points(group=group))
        if extra and group == "reelmachine.recipes":
            points.append(FIXTURE_ENTRY)
        return points

    return Registry(entry_points=provider)


def _settings(tmp_path: Path):
    return dataclasses.replace(
        get_settings(),
        workdir=tmp_path / "work",
        outdir=tmp_path / "out",
        mock_dir=tmp_path / "mock",
    )


@pytest.fixture
def make_engine(tmp_path):
    engines = []

    def build(*, policy_for=None, settings=None, store=None, **kwargs) -> Engine:
        engine = Engine(
            settings or _settings(tmp_path),
            registry=_registry(),
            policy_for=policy_for,
            store=store,
            **kwargs,
        )
        engines.append(engine)
        return engine

    yield build
    for engine in engines:
        engine.close()


def _request(**inputs) -> JobRequest:
    return JobRequest(recipe="engine-fixture", inputs=inputs)


def test_submit_returns_immediately_and_the_job_succeeds(make_engine) -> None:
    engine = make_engine()
    # warm the one-time schema compilation, then measure a normal submit
    engine.wait(engine.submit(_request(), caller="alice").id, caller="alice", timeout_s=15)

    started = time.monotonic()
    job = engine.submit(_request(value="hello", sleep_s=0.4), caller="alice")
    submit_ms = (time.monotonic() - started) * 1000
    assert submit_ms < 200, "start_job must not wait on the render"
    assert job.state is JobState.QUEUED

    finished = engine.wait(job.id, caller="alice", timeout_s=15)
    assert finished.state is JobState.SUCCEEDED
    assert finished.error is None
    assert finished.duration_ms > 0

    # the manifest records the job, its stages, environment and verification
    manifest = Manifest.model_validate_json(Path(finished.result.manifest).read_text())
    assert manifest.job_id == job.id and manifest.caller == "alice"
    assert manifest.inputs["value"] == "hello"
    assert [stage.id for stage in manifest.stages] == ["emit"]
    assert manifest.verification is not None and manifest.verification.ok
    assert manifest.environment["python"]
    assert any(artifact.name == "manifest.json" for artifact in finished.result.artifacts)


def test_progress_is_visible_while_running(make_engine) -> None:
    engine = make_engine()
    job = engine.submit(_request(sleep_s=0.5), caller="alice")
    seen = []
    finished = engine.wait(job.id, caller="alice", on_progress=seen.append, timeout_s=15)
    assert finished.state is JobState.SUCCEEDED
    assert any(snapshot.progress.stage == "emit" for snapshot in seen)


def test_failure_is_structured_not_a_traceback(make_engine) -> None:
    engine = make_engine()
    job = engine.submit(_request(fail=True), caller="alice")
    finished = engine.wait(job.id, caller="alice", timeout_s=15)
    assert finished.state is JobState.FAILED
    assert finished.error is not None
    assert finished.error.code == "PROVIDER_UNAVAILABLE"
    assert "fixture was asked to fail" in finished.error.message
    assert finished.error.hint == "set fail=false"
    assert finished.error.stage == "emit"


def test_cancel_stops_a_running_job(make_engine) -> None:
    engine = make_engine()
    job = engine.submit(_request(sleep_s=5.0), caller="alice")
    deadline = time.monotonic() + 5
    while engine.get(job.id, caller="alice").state is not JobState.RUNNING:
        time.sleep(0.02)
        assert time.monotonic() < deadline

    started = time.monotonic()
    cancelled = engine.cancel(job.id, caller="alice")
    assert cancelled.id == job.id
    finished = engine.wait(job.id, caller="alice", timeout_s=10)
    assert finished.state is JobState.CANCELLED
    assert finished.error is not None and finished.error.code == "CANCELLED"
    assert time.monotonic() - started < 4, "cancellation must not wait out the sleep"


def test_idempotency_key_yields_one_job(make_engine) -> None:
    engine = make_engine()
    request = JobRequest(recipe="engine-fixture", inputs={"value": "x"}, idempotency_key="same-key")
    first = engine.submit(request, caller="alice")
    second = engine.submit(request, caller="alice")
    assert first.id == second.id
    assert len(engine.list(caller="alice")) == 1
    engine.wait(first.id, caller="alice", timeout_s=15)
    # and after completion it still resolves to the same job
    third = engine.submit(request, caller="alice")
    assert third.id == first.id


def test_callers_are_isolated(make_engine) -> None:
    engine = make_engine()
    job = engine.submit(_request(), caller="alice")
    engine.wait(job.id, caller="alice", timeout_s=15)

    with pytest.raises(NotFound):
        engine.get(job.id, caller="bob")
    with pytest.raises(NotFound):
        engine.artifact(job.id, "manifest.json", caller="bob")
    assert engine.list(caller="bob") == []


def test_concurrency_limit_is_a_clean_error(make_engine) -> None:
    engine = make_engine(policy_for=lambda caller: CallerPolicy(max_concurrent_jobs=1))
    engine.submit(_request(sleep_s=1.0), caller="alice")
    with pytest.raises(ConcurrencyLimit):
        engine.submit(_request(sleep_s=1.0), caller="alice")
    # another caller is unaffected
    engine.submit(_request(sleep_s=0.1), caller="bob")


def test_disk_limit_is_a_clean_error(make_engine, tmp_path) -> None:
    engine = make_engine(policy_for=lambda caller: CallerPolicy(max_disk_mb=1))
    big = engine.store.root() / "alice" / "big.bin"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_bytes(b"0" * (2 * 1024 * 1024))
    with pytest.raises(DiskLimit):
        engine.submit(_request(), caller="alice")


def test_provider_quota_is_enforced_per_caller(make_engine) -> None:
    engine = make_engine(policy_for=lambda caller: CallerPolicy(quotas={"nadeshiko": 5}))
    ledger = engine.store.root() / "alice" / "quota" / "nadeshiko.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"2026-10": 5}), encoding="utf-8")
    with pytest.raises(QuotaExceededError):
        engine.submit(_request(), caller="alice")
    # bob has no spend and is unaffected
    engine.submit(_request(), caller="bob")


def test_unknown_recipe_and_invalid_inputs_are_actionable(make_engine) -> None:
    engine = make_engine()
    with pytest.raises(NotFound):
        engine.submit(JobRequest(recipe="nope", inputs={}), caller="alice")
    with pytest.raises(InvalidInput) as excinfo:
        engine.submit(_request(unknown_field=1), caller="alice")
    assert "schema" in excinfo.value.message


def test_catalogue_and_description(make_engine) -> None:
    engine = make_engine()
    ids = {row["id"] for row in engine.list_recipes()}
    assert {"nadeshiko-cut", "engine-fixture"} <= ids

    described = engine.describe("nadeshiko-cut")
    assert described["inputSchema"]["properties"]["word"]
    assert described["example"]["word"]
    assert described["providers"]["corpus"]["group"] == "corpora"
    assert described["cost"]["unit"] == "nadeshiko_requests"
    assert described["schemaVersion"] == 1

    with pytest.raises(NotFound):
        engine.describe("nope")


def test_probe_is_quota_free_and_structural(make_engine) -> None:
    engine = make_engine()
    report = engine.probe("engine-fixture", {"value": "hi"}, caller="alice")
    assert report.ok and report.quota_free
    assert report.details["value"] == "hi"
    with pytest.raises(InvalidInput):
        engine.probe("engine-fixture", {"nope": 1}, caller="alice")


def test_interrupted_jobs_are_recovered(tmp_path) -> None:
    from reelmachine.core.job import Job, now_ms

    store = LocalJobStore(tmp_path / "jobs")
    stale = Job(
        id="stale1",
        recipe="engine-fixture",
        caller="alice",
        state=JobState.RUNNING,
        request=JobRequest(recipe="engine-fixture"),
        heartbeat_ms=now_ms() - 60 * 60 * 1000,
    )
    store.create(stale)
    recovered = store.mark_interrupted(stale_after_ms=60_000)
    assert recovered == ["stale1"]
    job = store.get("stale1", caller="alice")
    assert job is not None and job.state is JobState.FAILED
    assert job.error is not None and job.error.code == "INTERRUPTED"
