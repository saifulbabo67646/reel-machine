"""Fetching pages that a plain HTTP client cannot reach.

Cloudflare's Turnstile is not a header check, and this module exists because copying
headers does not work. Measured on getyarn.io, all of it refused:

* ``curl`` with the site's own ``cf_clearance`` cookie, its User-Agent, its client hints
  and its whole cookie jar — 403, on both the site and its media host;
* a ``cf_clearance`` minted seconds earlier inside a real Chrome, copied into a *new*
  browser session — also refused;
* a fresh browser session, headed or headless, left undisturbed for two minutes or
  re-navigated three times — parked on "Just a moment..." (and, once the IP had been
  hammered hard enough, dropped at the TCP level).

What does work is staying inside the browser that cleared the gate: one persistent
profile, one session, fetch through it. Two backends can do that, and they are not equal
in one respect that matters — a Turnstile widget lives in a cross-origin iframe, and only
a Playwright-family driver can reach into it. ``patchright`` clicks the checkbox in the
frame and the control page cleared in 7.7 s; ``agent-browser`` speaks raw CDP, so the same
widget is a picture it can see but not touch. ``agent-browser`` is kept because it needs
no Python dependency and works when the gate is a plain interstitial.

The profile is the durable part: clearance belongs to a session, so reusing
``REEL_BROWSER_PROFILE`` is what turns "solve it every run" into "solve it once" —
and a *fresh* profile is the case that can be refused outright while an established one
sails through, which is why `status()` reports the hosts a session has already cleared.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from ..config import Settings, get_settings
from ..core.errors import ProviderUnavailable

GROUP = "browser"

#: Ordered by capability, not preference: `patchright` can click inside a cross-origin
#: Turnstile frame and `agent-browser` cannot, so `auto` reaches for it first and falls
#: back to the incumbents that need nothing but a Node binary.
PREFERENCE = ("patchright", "agent-browser")

#: How often the gate is re-checked while a page loads, and how long to keep trying.
POLL_INTERVAL_S = 1.5
CLEARANCE_TIMEOUT_S = 45.0

#: Directories to keep out of the clearance state file.
_STATE_FILE = "state.json"


class GatedFetchError(ProviderUnavailable):
    """The page never stopped being a Cloudflare interstitial."""

    def __init__(self, message: str, *, hint: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            hint=hint
            or (
                "the challenge was not cleared: try again in a few minutes, run headed "
                "(REEL_BROWSER_HEADLESS=false) so the widget can be clicked, or switch "
                "backend with REEL_BROWSER"
            ),
            details=details or {},
        )


class BrowserFetchError(ProviderUnavailable):
    """The browser could not reach the URL at all — DNS, TCP, TLS, or a load timeout.

    Kept apart from `GatedFetchError` because the remedies are opposite: a gate wants a
    warm session or a human click, an unreachable host wants the URL, the network, or a
    block on this IP looked at. (Observed for real: after enough automated attempts,
    getyarn.io stopped accepting TCP connections from the probing machine entirely.)
    """

    def __init__(self, message: str, *, hint: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            hint=hint
            or (
                "check the URL and the network; if the site answers a normal browser but not "
                "this machine, the IP may be blocked — that is not something a different "
                "backend can fix"
            ),
            details=details or {},
        )


@runtime_checkable
class BrowserProvider(Protocol):
    """A browser that can fetch a gated URL and be asked questions about the page.

    `evaluate` is part of the contract because the two real backends both have it
    natively (`page.evaluate`, `agent-browser eval`) and a caller that needs structured
    data out of a page should not have to re-parse HTML itself.
    """

    name: str

    def missing(self) -> list[str]:
        """Unmet requirements — a missing package or binary — for this deployment."""

    def describe(self) -> dict[str, Any]:
        """A row for `reel doctor`: name, group, description, missing."""

    def fetch_html(self, url: str, *, timeout_s: float | None = None) -> str:
        """The page's HTML once any challenge in front of it has been cleared."""

    def evaluate(self, url: str, expression: str, *, timeout_s: float | None = None) -> Any:
        """Run one JavaScript expression on the page and return its value."""

    def status(self) -> dict[str, Any]:
        """What this backend is doing right now — installed, started, where its profile is."""

    def close(self) -> None:
        """Release the browser. Safe to call twice."""


def describe_row(name: str, description: str, missing: list[str], **extra: Any) -> dict[str, Any]:
    """The `describe()` shape every provider group in this engine shares."""
    row: dict[str, Any] = {
        "name": name,
        "group": GROUP,
        "description": description,
        "missing": list(missing),
    }
    row.update(extra)
    return row


def resolve_name(name: str | None = None, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return (name or settings.browser or "auto").strip().lower()


def preferred_name(settings: Settings | None = None, *, registry: Any = None) -> str:
    """The backend `auto` would pick — resolved without launching anything.

    Walks `PREFERENCE` and takes the first backend whose requirements are met. A backend
    that cannot even be *loaded* — disabled by `REEL_PROVIDERS_DISABLED`, or broken — is
    never named; one that loads but reports unmet requirements still is, so a caller's
    error says *what* the primary backend is missing instead of "nothing available".
    Returns "" when nothing can be loaded at all.
    """
    from ..core.registry import Registry

    settings = settings or get_settings()
    registry = registry or Registry()
    known = {entry.lower() for entry in registry.names(GROUP)}
    loaded: list[str] = []
    for candidate in PREFERENCE:
        if candidate not in known:
            continue
        try:
            provider = registry.load(GROUP, candidate)(settings)
            problems = list(provider.missing())
        except Exception:  # noqa: BLE001 - broken or disabled is not a candidate
            continue
        loaded.append(candidate)
        if not problems:
            return candidate
    return loaded[0] if loaded else ""


def get_browser(
    name: str | None = None,
    settings: Settings | None = None,
    *,
    registry: Any = None,
) -> BrowserProvider:
    """Resolve a browser through the registry (first-party is not special)."""
    from ..core.registry import Registry

    settings = settings or get_settings()
    registry = registry or Registry()
    key = resolve_name(name, settings)
    known = {entry.lower() for entry in registry.names(GROUP)}

    if key == "auto":
        key = preferred_name(settings, registry=registry)
        if not key:
            raise ProviderUnavailable(
                "no browser backend is available",
                hint=(
                    "install one — `pip install 'reel-machine[browser]'` for patchright, or "
                    "`npm i -g agent-browser` — or stop disabling it with "
                    "REEL_PROVIDERS_DISABLED"
                ),
                details={"group": GROUP},
            )

    if key not in known:
        available = ", ".join(sorted(known)) or "none"
        raise ProviderUnavailable(
            f"unknown browser backend {key!r}",
            hint=f"available browser backends: {available}",
            details={"group": GROUP, "name": key},
        )
    return registry.load(GROUP, key)(settings)


# ------------------------------------------------------------------ clearance state


def state_path(profile: Path | str) -> Path:
    return Path(profile) / _STATE_FILE


def record_clearance(profile: Path | str, host: str, *, now: float | None = None) -> None:
    """Remember that this profile has been through a host's gate.

    Best-effort on purpose: a lost note costs a re-solve, never correctness.
    """
    if not host:
        return
    path = state_path(profile)
    try:
        state = json.loads(path.read_text()) if path.is_file() else {}
    except (OSError, ValueError):
        state = {}
    state[host] = int(now if now is not None else time.time())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    except OSError:
        pass


def clearance_state(profile: Path | str) -> dict[str, int]:
    path = state_path(profile)
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {str(host): int(at) for host, at in state.items() if isinstance(at, (int, float))}


def host_of(url: str) -> str:
    return urlparse(url).hostname or ""


def looks_like_challenge(html: str) -> bool:
    """Cheap offline check for the interstitial, for callers that only have the HTML."""
    lowered = html.lower()
    return (
        "just a moment" in lowered
        or "challenges.cloudflare.com" in lowered
        or "cf-chl-widget" in lowered
        or "cf_chl_opt" in lowered
    )
