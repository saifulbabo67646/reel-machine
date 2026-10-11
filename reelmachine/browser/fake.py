"""The deterministic fake: canned pages, canned answers, no browser and no network.

Every provider group in this engine ships one real implementation alongside a fake, and
this is the browser group's. It is what lets the getyarn search be tested end to end —
search, extract, manifest — without a browser, a network, or a clearance, and what lets
the gate and unreachable paths be exercised at all: a real Cloudflare refusal is not
something a test suite can arrange on demand.
"""

from __future__ import annotations

from typing import Any

from ..config import Settings, get_settings
from . import BrowserFetchError, GatedFetchError, describe_row

#: What a canned page returns when no page was registered for the URL: enough structure to
#: prove a fetch happened, and deliberately not a challenge.
DEFAULT_PAGE = "<html><head><title>fake</title></head><body><p>fake page</p></body></html>"

CHALLENGE_PAGE = (
    "<html><head><title>Just a moment...</title></head>"
    "<body><div id='challenge-stage'>verifying</div></body></html>"
)


class FakeBrowser:
    """Canned HTML per URL, canned `evaluate` values per expression."""

    name = "fake"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        pages: dict[str, str] | None = None,
        values: dict[str, Any] | None = None,
        gated: set[str] | None = None,
        unreachable: set[str] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.pages = dict(pages or {})
        self.values = dict(values or {})
        #: URLs that answer with the interstitial, and URLs that do not answer at all.
        self.gated = set(gated or ())
        self.unreachable = set(unreachable or ())
        #: Every URL fetched and every expression evaluated, in order — what tests assert on.
        self.calls: list[str] = []
        self.expressions: list[str] = []

    def missing(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        return describe_row(
            self.name,
            "Canned pages for tests and offline runs: no browser, no network, no clearance.",
            self.missing(),
            pages=len(self.pages),
        )

    def _check(self, url: str) -> None:
        if url in self.unreachable:
            raise BrowserFetchError(f"fake: {url} is unreachable", details={"backend": self.name})
        if url in self.gated or (url in self.pages and self.pages[url] == CHALLENGE_PAGE):
            raise GatedFetchError(
                f"fake: {url} never stopped being a Cloudflare interstitial",
                details={"backend": self.name, "url": url},
            )

    def fetch_html(self, url: str, *, timeout_s: float | None = None) -> str:
        self.calls.append(url)
        self._check(url)
        return self.pages.get(url, DEFAULT_PAGE)

    def evaluate(self, url: str, expression: str, *, timeout_s: float | None = None) -> Any:
        self.calls.append(url)
        self.expressions.append(expression)
        self._check(url)
        return self.values.get(expression)

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "installed": True,
            "started": True,
            "pages": len(self.pages),
            "calls": len(self.calls),
        }

    def close(self) -> None:
        """Nothing to release."""
