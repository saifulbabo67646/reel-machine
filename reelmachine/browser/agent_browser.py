"""The incumbent backend: the `agent-browser` CLI, driven as a subprocess.

This is the path that was already working before the module existed, lifted out of
``tools/getyarn_search.py`` unchanged in behaviour: a named session, the
``--disable-blink-features=AutomationControlled`` launch argument (Turnstile answers a
browser that reports ``navigator.webdriver = true`` with a checkbox that cannot be
clicked away), and re-navigation while the interstitial is up.

Its one structural limit is why ``patchright`` is preferred: `agent-browser` speaks raw
CDP, so a Turnstile widget inside a cross-origin iframe shows up in a snapshot as nothing
clickable at all, and coordinate clicks do not land. It fetches happily once a session has
cleared the gate by other means — which is exactly what a persistent session is for.

The session is deliberately left running when a caller is done: clearance belongs to the
session, and the profile is what makes the next run cheap. `close()` says so instead of
shutting a working session down; ``agent-browser --session <name> close`` is the manual
door.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any

from ..config import Settings, get_settings
from ..core.errors import ProviderUnavailable
from . import (
    CLEARANCE_TIMEOUT_S,
    POLL_INTERVAL_S,
    BrowserFetchError,
    GatedFetchError,
    describe_row,
    host_of,
    looks_like_challenge,
    record_clearance,
)

STEALTH_ARGS = "--disable-blink-features=AutomationControlled"

#: How many times a still-challenged page is re-navigated before giving up. Measured: a
#: session that sat on "Just a moment..." for forty seconds loaded on the very next
#: navigation, so one retry is worth more than a longer wait.
CLEARANCE_TRIES = 3

#: The challenge is read from the DOM, not guessed from the HTML.
PROBE_JS = (
    "JSON.stringify({"
    "title: document.title,"
    "challenge: !!document.querySelector('#challenge-stage, #cf-chl-widget-container'),"
    "html: document.documentElement.outerHTML"
    "})"
)


class AgentBrowserBackend:
    """Fetches through a persistent `agent-browser` session."""

    name = "agent-browser"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # -------------------------------------------------------------- requirements
    def missing(self) -> list[str]:
        if shutil.which("agent-browser") is None:
            return ["agent-browser is not on PATH (npm i -g agent-browser && agent-browser install)"]
        return []

    def describe(self) -> dict[str, Any]:
        return describe_row(
            self.name,
            "The agent-browser CLI in a persistent session. Fetches through a gate a "
            "session has already cleared; it cannot click inside a cross-origin Turnstile "
            "frame, so patchright is preferred when the widget needs a click.",
            self.missing(),
            session=str(self.settings.browser_session),
        )

    # -------------------------------------------------------------- the CLI
    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        args = env.get("AGENT_BROWSER_ARGS", "")
        if "AutomationControlled" not in args:
            env["AGENT_BROWSER_ARGS"] = f"{args},{STEALTH_ARGS}".lstrip(",")
        return env

    def run(self, *args: str, timeout: float = 180.0) -> str:
        if self.missing():
            raise ProviderUnavailable(
                f"browser backend {self.name!r} is unavailable: {self.missing()[0]}",
                hint="install it with `npm i -g agent-browser && agent-browser install`, "
                     "or switch backend with REEL_BROWSER",
                details={"backend": self.name},
            )
        command = ["agent-browser", "--session", str(self.settings.browser_session), *args]
        try:
            done = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout, env=self._env()
            )
        except subprocess.TimeoutExpired as exc:
            raise BrowserFetchError(
                f"agent-browser timed out after {timeout:.0f}s", details={"backend": self.name}
            ) from exc
        if done.returncode != 0:
            detail = ((done.stderr or done.stdout or "").strip().splitlines() or [""])[-1]
            raise BrowserFetchError(
                f"agent-browser failed: {detail}",
                details={"backend": self.name, "command": " ".join(command[:3])},
            )
        return done.stdout.strip()

    def evaluate_raw(self, expression: str) -> Any:
        """Evaluate on the page the session is *already* showing (no navigation)."""
        raw = self.run("eval", expression)
        try:
            value = json.loads(raw)
        except ValueError:
            return raw
        # `eval` JSON-encodes whatever the page returned; an expression that already
        # returns JSON therefore arrives encoded twice.
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return value
        return value

    def _probe(self) -> dict[str, Any]:
        value = self.evaluate_raw(PROBE_JS)
        return value if isinstance(value, dict) else {}

    def _load(self, url: str, timeout_s: float) -> str:
        """Navigate until the page is not the interstitial, then return its HTML."""
        deadline = time.monotonic() + timeout_s
        html = ""
        for attempt in range(CLEARANCE_TRIES):
            self.run("open", url, timeout=max(60.0, timeout_s))
            while time.monotonic() < deadline:
                probe = self._probe()
                html = str(probe.get("html") or "")
                if html and not probe.get("challenge") and not looks_like_challenge(html):
                    return html
                time.sleep(POLL_INTERVAL_S)
            if time.monotonic() >= deadline:
                break
        raise GatedFetchError(
            f"{host_of(url) or url} never stopped being a Cloudflare interstitial "
            f"({timeout_s:.0f}s over {CLEARANCE_TRIES} navigation(s))",
            details={"backend": self.name, "url": url, "session": str(self.settings.browser_session)},
        )

    # -------------------------------------------------------------- the contract
    def fetch_html(self, url: str, *, timeout_s: float | None = None) -> str:
        timeout = float(timeout_s or self.settings.browser_timeout_s or CLEARANCE_TIMEOUT_S)
        html = self._load(url, timeout)
        record_clearance(self.settings.browser_profile, host_of(url))
        return html

    def evaluate(self, url: str, expression: str, *, timeout_s: float | None = None) -> Any:
        timeout = float(timeout_s or self.settings.browser_timeout_s or CLEARANCE_TIMEOUT_S)
        self._load(url, timeout)
        value = self.evaluate_raw(expression)
        record_clearance(self.settings.browser_profile, host_of(url))
        return value

    def status(self) -> dict[str, Any]:
        binary = shutil.which("agent-browser") or ""
        return {
            "backend": self.name,
            "installed": bool(binary),
            "binary": binary,
            "session": str(self.settings.browser_session),
            "profile": str(self.settings.browser_profile),
        }

    def close(self) -> None:
        """No-op by design: the session carries the clearance, so it stays up.

        ``agent-browser --session <name> close`` is how a session is shut down for real.
        """
