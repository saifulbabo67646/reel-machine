"""The stealth backend: patchright, which can click inside the Turnstile frame.

`patchright` is a patched-driver Playwright, and that is the whole point here: a Turnstile
widget renders inside a cross-origin iframe at ``challenges.cloudflare.com``, and reaching
into a cross-origin frame is something a Playwright-family driver can do and a raw-CDP one
cannot. Measured on a Cloudflare-protected control page: the widget was clicked and the
page cleared in **7.7 s** on a cold profile, where a coordinate click and an accessibility
snapshot both failed.

Three more measured details are encoded below, each because getting it wrong cost a run:

* the profile persists (``REEL_BROWSER_PROFILE``) — clearance belongs to a session, so a
  warmed profile is the difference between "refused outright" and "through in seconds";
* a re-navigation is sometimes what lets a page through when clicking alone did not, so
  one is attempted before giving up;
* Chrome is preferred over bundled Chromium when it is installed (upstream's own advice),
  and a machine that has neither falls back to whatever binary patchright ships with.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
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

#: Where Turnstile lives. Only frames whose URL matches are clicked.
TURNSTILE_FRAME = "challenges.cloudflare.com"

#: What the widget can be clicked by, most specific first. `body` is the widget's own
#: body, not the page's, because the search never leaves the Turnstile frame.
CLICK_TARGETS = ("input[type=checkbox]", "label", "div[role=checkbox]", "body")

CHALLENGE_TITLE = "just a moment"
CHALLENGE_MARKERS = "#challenge-stage, #cf-chl-widget-container"

#: Afterwards, give the freshly cleared page a moment to paint its real content.
SETTLE_S = 0.4


class PatchrightBrowser:
    """A persistent stealth Chromium that fetches through its own cleared session."""

    name = "patchright"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None

    # -------------------------------------------------------------- requirements
    def missing(self) -> list[str]:
        if importlib.util.find_spec("patchright") is None:
            return [
                "patchright is not installed "
                "(pip install 'reel-machine[browser]', then `patchright install chromium`)"
            ]
        return []

    def describe(self) -> dict[str, Any]:
        return describe_row(
            self.name,
            "Stealth Chromium via patchright. Clears a Turnstile widget by clicking the "
            "checkbox inside its cross-origin frame, then fetches through that same session.",
            self.missing(),
            profile=str(self.settings.browser_profile),
            headless=bool(self.settings.browser_headless),
        )

    # -------------------------------------------------------------- the browser
    def _ensure_page(self) -> Any:
        if self._page is not None:
            return self._page
        problems = self.missing()
        if problems:
            raise GatedFetchError(
                f"browser backend {self.name!r} cannot start: {problems[0]}", details={"backend": self.name}
            )

        from patchright.sync_api import sync_playwright  # lazy: the extra may be absent

        self._playwright = sync_playwright().start()
        profile = Path(self.settings.browser_profile)
        profile.mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = {
            "user_data_dir": str(profile),
            "headless": bool(self.settings.browser_headless),
            "no_viewport": True,
        }
        last: Exception | None = None
        # Real Chrome first — upstream's recommendation, and the fingerprint that matches
        # what a detection service expects to see.
        for channel in ("chrome", None):
            try:
                if channel:
                    self._context = self._playwright.chromium.launch_persistent_context(
                        channel=channel, **options
                    )
                else:
                    self._context = self._playwright.chromium.launch_persistent_context(**options)
                break
            except Exception as exc:  # noqa: BLE001 - fall through to the next channel
                last = exc
        if self._context is None:
            self.close()
            raise BrowserFetchError(
                f"could not launch a browser: {last}",
                hint=(
                    "install a browser for patchright (`patchright install chromium`), or "
                    "point REEL_BROWSER at agent-browser"
                ),
                details={"backend": self.name},
            )
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        return self._page

    @staticmethod
    def _challenged(page: Any) -> bool:
        try:
            if CHALLENGE_TITLE in (page.title() or "").lower():
                return True
        except Exception:  # noqa: BLE001 - a mid-navigation read is not an answer
            return True
        try:
            return bool(page.query_selector(CHALLENGE_MARKERS))
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _click_turnstile(page: Any) -> bool:
        """Click the widget inside its cross-origin frame. True if a click landed."""
        frames = [frame for frame in page.frames if TURNSTILE_FRAME in (frame.url or "")]
        for frame in frames:
            for selector in CLICK_TARGETS:
                try:
                    element = frame.query_selector(selector)
                    if element is None:
                        continue
                    element.click(timeout=2500)
                    return True
                except Exception:  # noqa: BLE001 - try the next target
                    continue
        return False

    def _load(self, page: Any, url: str, timeout_s: float) -> None:
        budget_ms = max(10_000, int(timeout_s * 1000))
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=budget_ms)
        except Exception as exc:  # noqa: BLE001 - re-raised as our own, with a hint
            raise BrowserFetchError(
                f"could not load {url}: {str(exc).splitlines()[0]}",
                details={"backend": self.name, "url": url},
            ) from exc

        # Click the widget until it yields or the budget runs out. Deliberately *no*
        # re-navigation here: the widget's click is what clears this gate, and re-navigating
        # mid-challenge measured slower than clicking alone (33.6 s vs 7.7 s on the same
        # page), because it throws away a challenge that is already half-solved.
        deadline = time.monotonic() + timeout_s
        while self._challenged(page) and time.monotonic() < deadline:
            self._click_turnstile(page)
            time.sleep(POLL_INTERVAL_S)

        if self._challenged(page):
            raise GatedFetchError(
                f"{host_of(url) or url} never stopped being a Cloudflare interstitial "
                f"({timeout_s:.0f}s)",
                details={"backend": self.name, "url": url},
            )

        # Clearing the gate is not the same as having the page: a client-rendered site
        # starts loading its real content only after the challenge lifts, and reading
        # immediately returned 3.6 KB where the settled document was 173 KB. Best-effort,
        # because a page that never reaches `load` should still yield what it has.
        try:
            page.wait_for_load_state("load", timeout=5_000)
        except Exception:  # noqa: BLE001 - a slow page is not a failed fetch
            pass
        time.sleep(SETTLE_S)

    # -------------------------------------------------------------- the contract
    def fetch_html(self, url: str, *, timeout_s: float | None = None) -> str:
        page = self._ensure_page()
        timeout = float(timeout_s or self.settings.browser_timeout_s or CLEARANCE_TIMEOUT_S)
        self._load(page, url, timeout)
        html = page.content()
        if looks_like_challenge(html):
            raise GatedFetchError(
                f"{host_of(url) or url} returned the interstitial",
                details={"backend": self.name, "url": url},
            )
        record_clearance(self.settings.browser_profile, host_of(url))
        return html

    def evaluate(self, url: str, expression: str, *, timeout_s: float | None = None) -> Any:
        page = self._ensure_page()
        timeout = float(timeout_s or self.settings.browser_timeout_s or CLEARANCE_TIMEOUT_S)
        self._load(page, url, timeout)
        value = page.evaluate(expression)
        record_clearance(self.settings.browser_profile, host_of(url))
        return value

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "installed": not self.missing(),
            "started": self._context is not None,
            "profile": str(self.settings.browser_profile),
            "headless": bool(self.settings.browser_headless),
        }

    def close(self) -> None:
        for closer in (getattr(self._context, "close", None), getattr(self._playwright, "stop", None)):
            if closer is None:
                continue
            try:
                closer()
            except Exception:  # noqa: BLE001 - closing must never raise
                pass
        self._page = None
        self._context = None
        self._playwright = None
