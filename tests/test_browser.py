"""The browser group: discovery, health rows, the fake, and the gated-fetch contract.

Everything here is offline. The real backends are exercised by their `missing()` and by
the shape of what they report — launching a browser in a unit test would make the suite
depend on a display, a ~150 MB binary and a Cloudflare challenge, which is exactly what the
fake exists to avoid.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys

import pytest

from reelmachine.browser import (
    BrowserProvider,
    GatedFetchError,
    clearance_state,
    get_browser,
    host_of,
    looks_like_challenge,
    preferred_name,
    record_clearance,
)
from reelmachine.browser.fake import FakeBrowser
from reelmachine.config import get_settings
from reelmachine.core.registry import Registry
from reelmachine.engine.providers import provider_health

GATED_HTML = "<html><head><title>Just a moment...</title></head><body></body></html>"


class _Waiting:
    """A backend that is installed and never gets through the gate."""

    name = "patchright"

    def __init__(self, settings=None) -> None:
        self.settings = settings

    def missing(self) -> list[str]:
        return []

    def describe(self) -> dict:
        return {"name": self.name, "group": "browser", "description": "", "missing": []}

    def fetch_html(self, url, *, timeout_s=None):
        raise GatedFetchError(f"{url} never stopped being a Cloudflare interstitial")

    def evaluate(self, url, expression, *, timeout_s=None):
        return None

    def status(self) -> dict:
        return {"backend": self.name}

    def close(self) -> None:
        return None


class _NotInstalled(_Waiting):
    name = "agent-browser"

    def missing(self) -> list[str]:
        return ["agent-browser is not on PATH"]


class _StubRegistry:
    def __init__(self, providers: dict, *, broken: tuple = ()) -> None:
        self._providers = providers
        self._broken = set(broken)

    def names(self, group: str) -> list[str]:
        assert group == "browser"
        return list(self._providers)

    def load(self, group: str, name: str):
        assert group == "browser"
        if name in self._broken:
            raise RuntimeError(f"{name} is disabled by REEL_PROVIDERS_DISABLED")
        return self._providers[name]


# ------------------------------------------------------------------ discovery


def test_the_browser_backends_register_through_entry_points() -> None:
    assert {"patchright", "agent-browser", "fake"} <= set(Registry().names("browser"))


def test_provider_health_reports_the_group(settings) -> None:
    rows = {row["name"]: row for row in provider_health(settings=settings) if row["group"] == "browser"}
    assert {"patchright", "agent-browser", "fake"} <= set(rows)
    assert all(row["status"] in ("ok", "missing") for row in rows.values())
    # fake is the one backend that can never be missing: it needs nothing.
    assert rows["fake"]["status"] == "ok"


def test_a_backend_satisfies_the_protocol() -> None:
    assert isinstance(FakeBrowser(), BrowserProvider)


# ------------------------------------------------------------------ resolution


def test_auto_prefers_patchright_then_falls_back(settings) -> None:
    both = {"patchright": _Waiting, "agent-browser": _NotInstalled}
    assert preferred_name(settings, registry=_StubRegistry(both)) == "patchright"

    only_fallback = {"patchright": _NotInstalled, "agent-browser": _Waiting}
    assert preferred_name(settings, registry=_StubRegistry(only_fallback)) == "agent-browser"

    neither = {"patchright": _NotInstalled, "agent-browser": _NotInstalled}
    # Nothing is satisfied, so `auto` names the primary anyway — the caller's error then
    # says *what* patchright is missing instead of "no backend".
    assert preferred_name(settings, registry=_StubRegistry(neither)) == "patchright"


def test_get_browser_honours_the_requested_name(settings) -> None:
    registry = _StubRegistry({"patchright": _Waiting, "agent-browser": _NotInstalled})
    assert get_browser("agent-browser", settings, registry=registry).name == "agent-browser"
    with pytest.raises(Exception) as excinfo:
        get_browser("nope", settings, registry=registry)
    assert "available browser backends" in excinfo.value.hint


def test_a_disabled_backend_is_never_selected(settings) -> None:
    """`REEL_PROVIDERS_DISABLED=patchright` makes auto fall to the other one — and if both
    are disabled, `auto` refuses instead of naming a backend that cannot load."""
    registry = _StubRegistry(
        {"patchright": _Waiting, "agent-browser": _Waiting}, broken=("patchright",)
    )
    assert preferred_name(settings, registry=registry) == "agent-browser"

    both = _StubRegistry(
        {"patchright": _Waiting, "agent-browser": _Waiting},
        broken=("patchright", "agent-browser"),
    )
    assert preferred_name(settings, registry=both) == ""
    with pytest.raises(Exception) as excinfo:
        get_browser("auto", settings, registry=both)
    assert "REEL_PROVIDERS_DISABLED" in excinfo.value.hint


def test_an_unset_backend_names_its_requirement(settings) -> None:
    provider = get_browser("agent-browser", settings)
    if provider.missing():  # only meaningful where the CLI is absent
        assert "agent-browser" in provider.missing()[0]


# ------------------------------------------------------------------ the contract


def test_a_gated_fetch_raises_with_a_remedy() -> None:
    fake = FakeBrowser(gated={"https://example.com/gated"})
    with pytest.raises(GatedFetchError) as excinfo:
        fake.fetch_html("https://example.com/gated")
    assert excinfo.value.hint, "a gate must say what to do about it"
    assert "REEL_BROWSER" in excinfo.value.hint


def test_an_unreachable_host_is_a_different_error_from_a_gate() -> None:
    from reelmachine.browser import BrowserFetchError

    fake = FakeBrowser(unreachable={"https://example.com/gone"})
    with pytest.raises(BrowserFetchError):
        fake.fetch_html("https://example.com/gone")


def test_the_fake_records_what_was_asked_of_it() -> None:
    fake = FakeBrowser(pages={"https://example.com/": "<html><title>hi</title></html>"},
                       values={"document.title": "hi"})
    assert "hi" in fake.fetch_html("https://example.com/")
    assert fake.evaluate("https://example.com/", "document.title") == "hi"
    assert fake.calls == ["https://example.com/", "https://example.com/"]
    assert fake.status()["backend"] == "fake"


# ------------------------------------------------------------------ clearance state


def test_clearance_state_survives_in_the_profile(tmp_path) -> None:
    profile = tmp_path / "profile"
    assert clearance_state(profile) == {}
    record_clearance(profile, "getyarn.io", now=1791690000)
    record_clearance(profile, "nopecha.com", now=1791690100)
    assert clearance_state(profile) == {"getyarn.io": 1791690000, "nopecha.com": 1791690100}


def test_clearance_state_ignores_a_corrupt_file(tmp_path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "state.json").write_text("{not json", encoding="utf-8")
    assert clearance_state(profile) == {}
    record_clearance(profile, "getyarn.io", now=1)  # and repairs it
    assert clearance_state(profile) == {"getyarn.io": 1}


# ------------------------------------------------------------------ helpers


def test_host_and_challenge_helpers() -> None:
    assert host_of("https://getyarn.io/yarn-find?text=cry") == "getyarn.io"
    assert host_of("not a url") == ""
    assert looks_like_challenge(GATED_HTML)
    assert not looks_like_challenge("<html><title>cry - YARN search</title></html>")


# ------------------------------------------------------------------ the consumer


def test_the_getyarn_search_runs_entirely_on_the_fake(settings, tmp_path) -> None:
    """The real consumer, offline: search -> clips -> manifest, no browser involved."""
    root = str(settings.workdir.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    tools_dir = f"{root}/tools"
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import getyarn_search as gs

    payload = {
        "title": "cats and dogs - YARN search",
        "results": 1234,
        "clips": [
            {"id": "11111111-1111-1111-1111-111111111111", "title": "A Show",
             "line": "cats and dogs", "seconds": 1.5},
            {"id": "22222222-2222-2222-2222-222222222222", "title": "B Film",
             "line": "dogs and cats", "seconds": None},
        ],
    }
    fake = FakeBrowser(values={gs.EXTRACT_JS: payload})

    clips, total, walked = gs.search("cats and dogs", pages=2, driver=fake, verbose=False)
    assert [clip.id for clip in clips] == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]
    assert total == 1234
    assert walked == 2, "the second page repeated itself, so paging stops there"
    assert clips[0].page == "https://getyarn.io/yarn-clip/11111111-1111-1111-1111-111111111111"
    assert clips[0].clip_url == "https://y.getyarn.io/11111111-1111-1111-1111-111111111111.mp4"

    written = gs.manifest("cats and dogs", clips, gloss="idiom", total=total, pages=walked)
    assert written["search"]["results"] == 1234
    assert written["clips"][0]["line"] == "cats and dogs"
    assert json.loads(json.dumps(written))["clips"][1]["seconds"] is None


def test_the_getyarn_search_reports_a_gate_rather_than_an_empty_list() -> None:
    root = str(get_settings().workdir.parent)
    for path in (root, f"{root}/tools"):
        if path not in sys.path:
            sys.path.insert(0, path)
    import getyarn_search as gs

    fake = FakeBrowser(gated={"https://getyarn.io/yarn-find?text=cry&p=0"})
    with pytest.raises(GatedFetchError):
        gs.search("cry", pages=1, driver=fake, verbose=False)


# ------------------------------------------------------------------ the real backend


def test_patchright_reports_missing_instead_of_raising(monkeypatch) -> None:
    """The extra may be absent; that is a `missing()` row, never an ImportError."""
    from reelmachine.browser.patchright import PatchrightBrowser

    provider = PatchrightBrowser(get_settings())
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *args, **kwargs: None
        if name == "patchright"
        else real_find_spec(name, *args, **kwargs),
    )
    problems = provider.missing()
    assert problems and "reel-machine[browser]" in problems[0]
    with pytest.raises(GatedFetchError) as excinfo:
        provider.fetch_html("https://example.com/")
    assert "patchright" in str(excinfo.value)


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(
        get_settings(),
        browser_profile=tmp_path / "browser" / "profile",
        browser_headless=True,
    )
