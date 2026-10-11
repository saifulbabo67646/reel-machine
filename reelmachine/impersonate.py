"""Fetching bytes from hosts that judge the TLS fingerprint, not the headers.

Video and image hosts are the usual case: they serve the file to anything that *looks
like Chrome at the connection level*, and refuse everything else. Measured on
``y.getyarn.io/<clip-id>.mp4`` — the download link getyarn's own clip page points at:

| client | result |
|---|---|
| `curl` / `urllib`, with the site's cookies, User-Agent and client hints | **403**, HTML |
| Playwright's request context (the driver's own HTTP stack) | **403**, HTML |
| the same, re-issued through ``route.fetch()`` | **403**, HTML |
| ``curl_cffi`` with ``impersonate="chrome"``, no cookies at all | **200**, `video/mp4`, 159 060 bytes |

Header copying does not help; the connection has to look like a browser's. That is what
``curl_cffi`` does — it reproduces Chrome's TLS handshake, HTTP/2 settings and header
order — and it is why this module exists next to the browser one rather than inside it:
the *page* still needs a real browser (getyarn's search page answers 403 and "Just a
moment..." to an impersonating client too), while the *file* needs only the fingerprint.

The engine keeps the two apart for that reason. A host that gates on the fingerprint is
cheap to read; a host that holds an interactive challenge is not.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .core.errors import ProviderUnavailable


class FetchRefused(ProviderUnavailable):
    """The host answered, but not with the file — a block page or an HTTP error."""

    def __init__(self, message: str, *, hint: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            hint=hint
            or (
                "the host refused this client: try another impersonation target "
                "(REEL_IMPERSONATE=chrome|firefox|safari), and note that a host behind an "
                "interactive challenge needs the browser backends instead"
            ),
            details=details or {},
        )


@dataclass(slots=True)
class Fetched:
    """One response, whole, in memory. Callers here fetch clips, not episodes."""

    url: str
    status: int
    content_type: str
    content: bytes

    @property
    def is_media(self) -> bool:
        return not self.content_type.startswith("text/html") and len(self.content) > 0


def requirement() -> list[str]:
    """Unmet requirements, in the shape every other capability reports them."""
    if importlib.util.find_spec("curl_cffi") is None:
        return ["curl-cffi is not installed (pip install 'reel-machine[impersonate]')"]
    return []


def describe(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    return {
        "name": "curl-cffi",
        "group": "downloads",
        "description": (
            "Fetch bytes from hosts that gate on the TLS fingerprint (a clip's .mp4, an "
            "image host). Reproduces Chrome's handshake, HTTP/2 settings and header order; "
            "a plain client is refused."
        ),
        "missing": requirement(),
        "impersonate": settings.impersonate,
    }


def fetch(
    url: str,
    *,
    referer: str = "",
    impersonate: str = "",
    timeout_s: float = 120.0,
) -> Fetched:
    """One GET, impersonating a browser's connection. Raises rather than returning a block."""
    problems = requirement()
    if problems:
        raise FetchRefused(problems[0], details={"url": url})

    from curl_cffi import requests  # lazy: the extra may be absent

    settings = get_settings()
    target = (impersonate or settings.impersonate or "chrome").strip()
    headers = {"referer": referer} if referer else {}
    try:
        response = requests.get(url, impersonate=target, headers=headers, timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 - curl_cffi raises a family of its own
        raise FetchRefused(
            f"{url} could not be fetched: {type(exc).__name__}: {str(exc)[:120]}",
            details={"url": url, "impersonate": target},
        ) from exc

    content = response.content or b""
    content_type = str(response.headers.get("content-type") or "")
    if response.status_code >= 400 or content_type.startswith("text/html"):
        raise FetchRefused(
            f"{url} answered {response.status_code} {content_type or '?'} ({len(content)} bytes), "
            "not a file",
            details={
                "url": url,
                "status": response.status_code,
                "contentType": content_type,
                "impersonate": target,
            },
        )
    return Fetched(url=url, status=response.status_code, content_type=content_type, content=content)


def download(
    url: str,
    dest: Path | str,
    *,
    referer: str = "",
    impersonate: str = "",
    refresh: bool = False,
    timeout_s: float = 120.0,
) -> Path:
    """Fetch `url` to `dest` — skipped when it is already there, `.part` while in flight."""
    target = Path(dest)
    if target.is_file() and target.stat().st_size > 0 and not refresh:
        return target

    fetched = fetch(url, referer=referer, impersonate=impersonate, timeout_s=timeout_s)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    part.write_bytes(fetched.content)
    part.replace(target)
    return target
