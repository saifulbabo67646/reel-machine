#!/usr/bin/env python3
"""Search getyarn.io for a phrase and list the clips it finds.

getyarn.io sits behind Cloudflare's interactive "verify you are human" gate, and a plain
HTTP client loses to it: curl — even with a freshly minted ``cf_clearance`` cookie straight
out of Chrome, matching User-Agent, client hints and the whole cookie jar — is answered 403
on both ``getyarn.io`` and the media host ``y.getyarn.io``. The gate is bound to the client,
not to the cookie string, so this tool does not carry a cookie anywhere: it fetches through
the engine's browser facility (``reelmachine.browser``), which keeps one session and fetches
inside it.

Which backend that facility picks matters, and it is why the facility exists:

* ``patchright`` (the default) can click the checkbox inside Turnstile's *cross-origin*
  frame. Measured on a Cloudflare-protected control page: clicked and cleared in 7.7 s from
  a cold profile, where coordinate clicks and accessibility snapshots had both failed.
* ``agent-browser`` fetches happily through a gate a session has already cleared, but it
  cannot reach into that frame, so it needs a session that got through some other way.

Two details are load-bearing, and both were found by getting them wrong first:

* headless mode did not clear a widget that needed the click — twice it sat on the
  interstitial for the full timeout, while the same page cleared headed (7.7 s, cold). The
  engine runs headed unless ``REEL_BROWSER_HEADLESS=true`` says otherwise.
* ``limit=`` in the URL is ignored server-side — Yarn still returns 20 cards — so depth is
  walked with ``p=`` and nothing here pretends otherwise.

What comes out is one row per clip: its id, the film or show, the line as spoken, its
duration, and the two URLs that matter —

    page:      https://getyarn.io/yarn-clip/<id>   the clip's own page
    clip_url:  https://y.getyarn.io/<id>.mp4       the raw clip

``--json`` writes those rows in the same manifest shape as ``tools/getyarn_clips.json``,
which is what ``tools/getyarn_reel.py`` mixes, so the pair is a full local flow:
search -> download -> reel.

usage:
  uv run python tools/getyarn_search.py "cats and dogs"
  uv run python tools/getyarn_search.py "cats and dogs" --pages 2
  uv run python tools/getyarn_search.py "cats and dogs" --all --json .work/getyarn/cats.json
  uv run python tools/getyarn_search.py "raining cats and dogs" \\
      --gloss "raining very heavily" --json tools/getyarn_clips.json
  uv run python tools/getyarn_search.py "cats and dogs" --browser agent-browser
  uv run python tools/getyarn_search.py "cats and dogs" --limit 6 \\
      --download .work/getyarn/clips --json .work/getyarn/cats.json

``--download`` fetches each clip's own ``.mp4`` (the URL its clip page downloads from)
into ``<clips-dir>/<clip-id>.mp4``, which is the name ``tools/getyarn_reel.py`` expects —
so search, download and mix are one chain. That step needs no browser: the media host
gates on the TLS fingerprint, and `reelmachine.impersonate` provides one. Run it through
the project environment (``uv run``): it imports the engine.

The browser session is deliberately left open — the clearance lives in it — so re-running
skips the wait. ``agent-browser --session <name> close`` shuts a CLI session down.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reelmachine import impersonate  # noqa: E402
from reelmachine.browser import BrowserProvider, get_browser  # noqa: E402
from reelmachine.config import Settings, get_settings  # noqa: E402
from reelmachine.core.errors import ReelError  # noqa: E402

FIND_URL = "https://getyarn.io/yarn-find"
CLIP_PAGE = "https://getyarn.io/yarn-clip/{id}"
CLIP_URL = "https://y.getyarn.io/{id}.mp4"
DOWNLOAD_HOST = "https://y.getyarn.io/<clip-id>.mp4"

#: What the media host expects to see on a clip request.
MEDIA_REFERER = "https://getyarn.io/"

#: How many times a page is re-asked for before the tool accepts that nothing rendered.
#: Re-asking re-navigates, which is the lever that was measured to help.
RESULT_TRIES = 3
RESULT_INTERVAL_S = 1.5

#: One expression. It returns a plain object rather than a JSON string on purpose: a
#: Playwright-family driver serialises an object to a dict natively, and the CLI backend
#: JSON-encodes whatever the page returns, so both hand back a dict — where a returned
#: *string* arrived as a string from one backend and a decoded dict from the other.
#: The extractor is otherwise unchanged from the version verified against the live page:
#: the ids it produced were diffed against a captured search, 20/20.
EXTRACT_JS = """
(() => {
  const clips = [];
  const seen = new Set();
  for (const card of document.querySelectorAll('div.card')) {
    const link = card.querySelector('a[href*="yarn-clip"]');
    if (!link) continue;
    const id = (link.getAttribute('href') || '').split('/').pop();
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id) || seen.has(id)) continue;
    seen.add(id);
    const title = card.querySelector('.title');
    const line = card.querySelector('.transcript');
    const time = card.querySelector('.play-time');
    const parsed = time ? (time.textContent.match(/([0-9.]+)\\s*s/) || [])[1] : undefined;
    const seconds = parsed ? parseFloat(parsed) : null;
    clips.push({
      id: id,
      title: title ? title.textContent.trim() : '',
      line: line ? line.textContent.trim() : '',
      seconds: Number.isFinite(seconds) ? seconds : null,
    });
  }
  const count = document.body.innerText.match(/About ([\\d,]+) results/);
  return {
    title: document.title,
    results: count ? parseInt(count[1].replace(/,/g, ''), 10) : null,
    clips: clips,
  };
})()
"""


class SearchError(RuntimeError):
    """The search ran but came back unusable."""


@dataclass(slots=True)
class Clip:
    id: str
    title: str = ""
    line: str = ""
    seconds: float | None = None

    @property
    def page(self) -> str:
        return CLIP_PAGE.format(id=self.id)

    @property
    def clip_url(self) -> str:
        return CLIP_URL.format(id=self.id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "line": self.line,
            "seconds": self.seconds,
            "page": self.page,
            "clip_url": self.clip_url,
        }


def search_url(phrase: str, page: int) -> str:
    return f"{FIND_URL}?text={urllib.parse.quote_plus(phrase)}&p={page}"


def page_payload(driver: BrowserProvider, url: str, *, verbose: bool = True) -> dict[str, Any]:
    """One results page, extracted by a browser that has already cleared the gate.

    The gate itself is the backend's job now. What is left here is the case a backend
    cannot see: the page loaded, but its grid has not rendered yet. A rendered-but-empty
    grid is an answer, not a timeout, and is recognised by the page's own title.
    """
    last: dict[str, Any] = {}
    for attempt in range(RESULT_TRIES):
        value = driver.evaluate(url, EXTRACT_JS)
        if isinstance(value, dict):
            last = value
            if value.get("clips") or "yarn" in str(value.get("title") or "").lower():
                return value
        if attempt < RESULT_TRIES - 1:
            if verbose:
                print(f"  page not rendered yet; re-asking ({attempt + 1}/{RESULT_TRIES - 1})",
                      file=sys.stderr)
            time.sleep(RESULT_INTERVAL_S)
    raise SearchError(f"nothing rendered for {url} (page title {str(last.get('title') or '?')!r})")


def search(
    phrase: str,
    *,
    pages: int = 1,
    driver: BrowserProvider | None = None,
    verbose: bool = True,
) -> tuple[list[Clip], int | None, int]:
    """Walk `pages` result pages of `phrase`, newest page last.

    Returns the clips, Yarn's own result count when it prints one, and how many pages
    were actually walked. Stops early when a page adds nothing new — Yarn pads the tail
    with repeats across page boundaries, and continuing would only re-print them.
    """
    browser = driver or get_browser()
    collected: list[Clip] = []
    seen: set[str] = set()
    total: int | None = None
    walked = 0

    for page in range(max(1, pages)):
        payload = page_payload(browser, search_url(phrase, page), verbose=verbose)
        walked = page + 1
        if total is None and payload.get("results") is not None:
            total = int(payload["results"])
        rows = payload.get("clips") or []
        fresh = [Clip(**{k: row.get(k) for k in ("id", "title", "line", "seconds")})
                 for row in rows if row.get("id") and row["id"] not in seen]
        for clip in fresh:
            seen.add(clip.id)
        collected.extend(fresh)
        if verbose:
            print(f"  page {page}: {len(fresh)} new clip(s)", file=sys.stderr)
        if not fresh and page > 0:
            if verbose:
                print(f"  page {page} added nothing new; stopping", file=sys.stderr)
            break
    return collected, total, walked


def download_clips(
    clips: list[Clip],
    clips_dir: Path,
    *,
    limit: int = 0,
    referer: str = MEDIA_REFERER,
    verbose: bool = True,
) -> dict[str, Path]:
    """Fetch each clip's own ``.mp4`` — the URL its clip page downloads from.

    This is the one step that does *not* need a browser: the media host gates on the TLS
    fingerprint, so a plain client is refused (403) and an impersonating one is served
    (200, no cookies at all) — measured, and why `reelmachine.impersonate` exists.
    """
    chosen = clips[:limit] if limit else clips
    saved: dict[str, Path] = {}
    for index, clip in enumerate(chosen, start=1):
        dest = clips_dir / f"{clip.id}.mp4"
        label = f"[{index}/{len(chosen)}]"
        if dest.is_file() and dest.stat().st_size > 0:
            saved[clip.id] = dest
            if verbose:
                print(f"  {label} have  {dest.name} ({dest.stat().st_size / 1e6:.2f} MB)",
                      file=sys.stderr)
            continue
        try:
            path = impersonate.download(clip.clip_url, dest, referer=referer)
        except ReelError as exc:
            print(f"  {label} FAILED {clip.id}: {exc}", file=sys.stderr)
            continue
        saved[clip.id] = path
        if verbose:
            print(f"  {label} got   {dest.name} ({path.stat().st_size / 1e6:.2f} MB)  "
                  f"{clip.title[:34]}", file=sys.stderr)
    return saved


def manifest(phrase: str, clips: list[Clip], *, gloss: str = "", total: int | None = None,
             pages: int = 1, files: dict[str, Path] | None = None) -> dict[str, Any]:
    """The `tools/getyarn_clips.json` shape, so the mixer already understands it."""
    rows = []
    for clip in clips:
        row = clip.to_dict()
        if files and clip.id in files:
            # The name, not the path: `tools/getyarn_reel.py --clips-dir` owns the directory,
            # and a manifest that carries a path gets joined onto it twice.
            row["file"] = files[clip.id].name
        rows.append(row)
    return {
        "phrase": phrase,
        "gloss": gloss,
        "search": {
            "site": "getyarn.io",
            "url": search_url(phrase, 0),
            "searched_for": phrase,
            "results": total,
            "pages": pages,
            "searched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "source": {
            "site": "getyarn.io",
            "download_host": DOWNLOAD_HOST,
            "note": (
                "Clips are third-party film and TV footage served by getyarn.io; the "
                "download is a raw clip with no burned-in captions. Check the rights "
                "before publishing anything built from them."
            ),
        },
        "clips": rows,
    }


def _settings(args: argparse.Namespace) -> Settings:
    """`get_settings()` with this run's overrides applied."""
    updates: dict[str, Any] = {}
    if args.browser:
        updates["browser"] = args.browser
    if args.session:
        updates["browser_session"] = args.session
    settings = get_settings()
    return dataclasses.replace(settings, **updates) if updates else settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phrase", help="what to search for, e.g. \"cats and dogs\"")
    parser.add_argument("--pages", type=int, default=1, help="result pages to walk (20 clips each)")
    parser.add_argument("--all", action="store_true",
                        help="keep paging until Yarn stops returning new clips (capped at --max-pages)")
    parser.add_argument("--max-pages", type=int, default=50,
                        help="the ceiling for --all; measure it as a page budget, not a courtesy — "
                             "six pages of \"cats and dogs\" were still all fresh clips")
    parser.add_argument("--gloss", default="", help="a meaning to record in the manifest")
    parser.add_argument("--json", type=Path, default=None, help="also write a manifest here")
    parser.add_argument("--download", type=Path, default=None,
                        help="fetch each clip's .mp4 into this directory, named <id>.mp4")
    parser.add_argument("--limit", type=int, default=0,
                        help="with --download, fetch only the first N clips")
    parser.add_argument("--browser", default=None, choices=["auto", "patchright", "agent-browser"],
                        help="override REEL_BROWSER for this run")
    parser.add_argument("--session", default=None,
                        help="agent-browser session to reuse (ignored by patchright)")
    parser.add_argument("--quiet", action="store_true", help="print only the clip URLs")
    args = parser.parse_args(argv)

    pages = args.max_pages if args.all else args.pages
    settings = _settings(args)
    try:
        driver = get_browser(settings=settings)
    except ReelError as exc:
        print(f"search failed: {exc}", file=sys.stderr)
        if exc.hint:
            print(f"  hint: {exc.hint}", file=sys.stderr)
        return 2
    if not args.quiet:
        print(f"  browser: {driver.name}", file=sys.stderr)

    try:
        clips, total, walked = search(args.phrase, pages=pages, driver=driver,
                                      verbose=not args.quiet)
    except (ReelError, SearchError) as exc:
        print(f"search failed: {exc}", file=sys.stderr)
        hint = getattr(exc, "hint", "")
        if hint:
            print(f"  hint: {hint}", file=sys.stderr)
        return 2

    if not clips:
        print(f"no clips for {args.phrase!r}" + (f" (Yarn says {total} results)" if total else ""),
              file=sys.stderr)
        return 1

    if not args.quiet:
        found = f" of about {total}" if total else ""
        print(f"{len(clips)} clip(s){found} for {args.phrase!r}\n")
    for clip in clips:
        trailer = ""
        if not args.quiet:
            bits = [clip.title or "?", clip.line] if clip.line else [clip.title or "?"]
            seconds = f"{clip.seconds:g}s" if clip.seconds is not None else ""
            trailer = "  ·  " + " — ".join(bits) + (f" ({seconds})" if seconds else "")
        print(f"{clip.page}{trailer}")

    files: dict[str, Path] = {}
    if args.download:
        problems = impersonate.requirement()
        if problems:
            print(f"download needs an impersonating client: {problems[0]}", file=sys.stderr)
            return 2
        args.download.mkdir(parents=True, exist_ok=True)
        want = min(args.limit, len(clips)) if args.limit else len(clips)
        if not args.quiet:
            print(f"\ndownloading {want} clip(s) into {args.download}", file=sys.stderr)
        files = download_clips(clips, args.download, limit=args.limit, verbose=not args.quiet)
        if not args.quiet:
            mb = sum(path.stat().st_size for path in files.values()) / 1e6
            print(f"{len(files)}/{want} clip(s) on disk ({mb:.1f} MB)", file=sys.stderr)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                manifest(args.phrase, clips, gloss=args.gloss, total=total, pages=walked,
                         files=files or None),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nmanifest: {args.json} ({len(clips)} clips)", file=sys.stderr)
        if args.download:
            print(f"next: uv run python tools/getyarn_reel.py --manifest {args.json} "
                  f"--clips-dir {args.download} --aspect vertical", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
