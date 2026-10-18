"""Wikimedia Commons image search for mapreel: which files exist and which are free to use."""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal

from ...core.errors import InvalidInput, ProviderUnavailable

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "reel-machine/mapreel (https://github.com/saifulbabo67646/reel-machine)"
FREE = re.compile(
    r"^(cc0|cc[- ]by(-sa)?( \d\.\d)?|public domain|pd\b.*|attribution|gfdl|fal|copyrighted free use)",
    re.IGNORECASE,
)
THUMB = 320

Kind = Literal["any", "logo", "photo", "flag"]


@dataclass(frozen=True)
class ImageHit:
    id: str
    title: str
    thumb_url: str
    url: str
    width: int
    height: int
    mime: str
    licence: str
    licence_url: str | None
    attribution: str
    permitted: bool
    restrictions: str | None


def _http_json(params: dict[str, Any]) -> dict[str, Any]:
    url = f"{API}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last: Exception | None = None
    for _ in range(2):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - fixed https host
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code < 500:
                break
        except (TimeoutError, socket.timeout, urllib.error.URLError, json.JSONDecodeError) as exc:
            last = exc
    raise ProviderUnavailable(
        "Wikimedia Commons did not answer",
        hint="retry in a moment, or give the image as a local file or URL instead",
        details={"error": str(last)[:200]},
    )


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    parser.close()
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def _meta(info: dict[str, Any], key: str) -> str:
    value = (info.get("extmetadata") or {}).get(key) or {}
    return str(value.get("value", "")) if isinstance(value, dict) else ""


def _hit(page: dict[str, Any]) -> ImageHit | None:
    infos = page.get("imageinfo") or []
    if not infos:
        return None
    info = infos[0]
    title = str(page.get("title", ""))
    name = title.removeprefix("File:")
    licence = _plain(_meta(info, "LicenseShortName"))
    non_free = _meta(info, "NonFree").strip().lower() == "true"
    attribution = _plain(_meta(info, "Artist")) or _plain(_meta(info, "Credit")) or "Wikimedia Commons"
    return ImageHit(
        id=f"commons:File:{name}",
        title=name,
        thumb_url=str(info.get("thumburl") or info.get("url") or ""),
        url=str(info.get("url", "")),
        width=int(info.get("width") or 0),
        height=int(info.get("height") or 0),
        mime=str(info.get("mime", "")),
        licence=licence,
        licence_url=_meta(info, "LicenseUrl") or None,
        attribution=attribution[:200],
        permitted=bool(licence and FREE.match(licence) and not non_free),
        restrictions=_plain(_meta(info, "Restrictions")) or None,
    )


def search(
    query: str,
    *,
    kind: Kind = "any",
    limit: int = 12,
    include_nonfree: bool = False,
    fetch: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> list[ImageHit]:
    query = (query or "").strip()
    if not query:
        raise InvalidInput("query is empty", hint="say what to look for, e.g. 'Nike logo'", details={"path": "query"})
    if kind not in ("any", "logo", "photo", "flag"):
        raise InvalidInput(f"unknown kind {kind!r}", hint="one of any, logo, photo, flag", details={"path": "kind"})
    fetch = fetch or _http_json  # looked up at call time so tests can patch it
    limit = max(1, min(int(limit), 50))
    text = {"logo": f"{query} logo", "flag": f"{query} flag"}.get(kind, query)
    data = fetch({
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrlimit": min(limit * 2, 50), "gsrsearch": text, "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": THUMB,
    })
    pages = (data.get("query") or {}).get("pages") or {}
    ranked = sorted(pages.values(), key=lambda p: p.get("index", 0))
    hits = [h for h in (_hit(p) for p in ranked) if h and h.mime.startswith("image/")]
    if kind == "photo":
        hits = [h for h in hits if h.mime != "image/svg+xml"]
    if not include_nonfree:
        hits = [h for h in hits if h.permitted]
    if kind == "logo":  # stable: vector and png first, the API order otherwise
        hits.sort(key=lambda h: h.mime not in ("image/svg+xml", "image/png"))
    return hits[:limit]


def info(
    file_id: str,
    *,
    width: int = 1024,
    fetch: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> ImageHit:
    fetch = fetch or _http_json
    name = file_id.removeprefix("commons:").removeprefix("File:").strip()
    if not name:
        raise InvalidInput("no file name", hint="use an id from find_images", details={"path": "image"})
    data = fetch({
        "action": "query", "format": "json", "titles": f"File:{name}", "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": int(width),
    })
    for page in ((data.get("query") or {}).get("pages") or {}).values():
        if "missing" in page or "invalid" in page:
            continue
        hit = _hit(page)
        if hit:
            return hit
    raise InvalidInput(
        f"Commons has no file {name!r}", hint="search again with find_images and use an id it returns",
        details={"path": "image"},
    )
