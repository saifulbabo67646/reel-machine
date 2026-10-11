# Pages behind Cloudflare Turnstile

Some of what a reel needs is served by sites that put a Cloudflare challenge in front of
it — a clip search page, a media host. Those pages cannot be fetched with an HTTP client,
and this document is about why, what the engine does instead, and what to do when it still
does not work.

Provider group: `reelmachine.browser`. Run `reel doctor` to see the backends' state.

## Why a browser, and why *this* browser

Every cheaper answer was tried and measured on getyarn.io, and each one failed:

| attempt | result |
|---|---|
| `curl` with the site's own `cf_clearance` cookie, its User-Agent, its client hints, its whole cookie jar | **403** — on the site *and* on the media host |
| a `cf_clearance` minted seconds earlier inside a real Chrome, copied into a **new** browser session | **403** — the gate is bound to the client, not to the cookie string |
| a fresh browser session, headless or headed, undisturbed for two minutes, re-navigated three times | parked on "Just a moment..." until it gave up |
| the same, once the IP had been probed hard enough | refused at the TCP level (`ERR_ADDRESS_UNREACHABLE`, `ping` 100 % loss) |

So the engine does not carry a cookie anywhere. It keeps **one browser session and fetches
inside it**, which is the only configuration that kept working: a session that had cleared
the gate went on answering — including for pages it had never visited — for hours.

The backend that clears a *widget* is the one that can reach inside it. A Turnstile widget
renders in a cross-origin iframe at `challenges.cloudflare.com`; clicking it needs a driver
that can address a cross-origin frame, which is a Playwright-family thing to do:

* **`patchright`** (preferred) — a patched-driver Playwright. Clicked the checkbox and
  cleared a control page in **7.7 s from a cold profile**, where coordinate clicks and
  accessibility snapshots had both failed.
* **`agent-browser`** — raw CDP through the CLI. Fetches happily once a session has cleared
  the gate, but a cross-origin widget is a picture it can see and not touch.

## Providers

| name | what it needs | notes |
|---|---|---|
| `patchright` | `pip install 'reel-machine[browser]'`, then `patchright install chromium` (or a real Chrome, which is preferred when present) | clicks inside the Turnstile frame; persistent profile |
| `agent-browser` | `npm i -g agent-browser && agent-browser install` | reuses a session's existing clearance; no Python dependency |
| `fake` | nothing | canned pages for tests and offline runs — never touches a browser |

`REEL_BROWSER=auto` (the default) picks `patchright` when its requirements are met and
falls back to `agent-browser`. `auto` names the primary backend even when nothing is
installed, so the failure says what patchright is missing rather than "nothing available".

## Settings

| variable | default | what it does |
|---|---|---|
| `REEL_BROWSER` | `auto` | `auto` \| `patchright` \| `agent-browser` |
| `REEL_BROWSER_PROFILE` | `.work/browser/profile` | the persistent profile, and the `state.json` recording which hosts it has cleared |
| `REEL_BROWSER_HEADLESS` | `false` | headed by default — see below |
| `REEL_BROWSER_TIMEOUT_S` | `45` | seconds allowed for the page to load and the challenge to clear |
| `REEL_BROWSER_SESSION` | `reelmachine` | the `agent-browser` session name; ignored by `patchright` |

**Headed is the default on purpose.** With a cold profile and a widget that needs the
click, headless sat on the interstitial for the full timeout twice, while the same page
cleared headed in 7.7 s. A server with no display sets `REEL_BROWSER_HEADLESS=true` — a
gate that clears itself still passes there; one that needs a click will not.

## Using it

```python
from reelmachine.browser import get_browser

browser = get_browser()                      # REEL_BROWSER picks, or pass a name
html = browser.fetch_html("https://example.com/gated")
value = browser.evaluate("https://example.com/gated", "document.title")
browser.close()
```

`evaluate` is part of the contract because both real backends have it natively, and a
caller that wants structured data out of a page should not have to re-parse HTML. Return an
**object** from the expression, not a JSON string: a Playwright-family driver serialises an
object to a dict, and the CLI backend JSON-encodes whatever comes back — a returned string
arrives as a string from one and a decoded dict from the other.

Failures are two different things, and the remedies are opposites:

* `GatedFetchError` — the page never stopped being the interstitial. Try again in a few
  minutes, run headed so the checkbox can be clicked, or switch backend.
* `BrowserFetchError` — the host could not be reached at all. Check the URL and the
  network; if a normal browser reaches the site from this machine and the engine does not,
  the IP is blocked, and **no backend change fixes that**. `getyarn.io` was blocked at the
  TCP level for a while during this feature's own development, from probing it too hard.

Agents discover this through MCP: `browser_status` reports the selected backend, whether it
is installed, the profile, and the hosts that profile has already cleared. It is read-only
and never launches a browser, because a solve takes seconds and no tool call may wait.

The first-party consumer is `tools/getyarn_search.py`, which searches getyarn.io through
this facility and writes a manifest `tools/getyarn_reel.py` can mix;
`uv run python tools/getyarn_search.py "cats and dogs" --browser agent-browser` is how the
two backends are compared on a real page.

## Downloading the bytes

A file host is a different problem from a page, and a cheaper one to solve. The clip URL
getyarn's own download link points at — `https://y.getyarn.io/<clip-id>.mp4` — answers 403
to anything whose *connection* does not look like a browser's, and headers do not matter:

| client | result |
|---|---|
| `curl` / `urllib`, with the site's cookies, User-Agent and client hints | 403, HTML |
| Playwright's request context (the driver's own HTTP stack) | 403, HTML |
| the same, re-issued through `route.fetch()` | 403, HTML |
| a Chrome-impersonating client (`curl_cffi`, `impersonate="chrome"`), **no cookies** | **200, `video/mp4`** |

`reelmachine/impersonate.py` is that last row: `fetch(url)` returns bytes,
`download(url, dest)` writes a file (skipping one already there, `.part` while in flight),
and `REEL_IMPERSONATE` chooses which browser to look like. It needs
`pip install 'reel-machine[impersonate]'` and no browser at all — which is exactly why it is
a module next to this group rather than a backend inside it: the *page* needs a browser, the
*file* needs a fingerprint.

Agents see both in `browser_status`: `backends` for the page, `downloads` for the file, plus
a hint that says which gate each one is for.

The getyarn chain is the worked example — search, fetch the clips, mix:

```bash
uv run python tools/getyarn_search.py "piece of cake" --limit 6 \
    --download .work/getyarn/clips --json .work/getyarn/piece-of-cake.json
uv run python tools/getyarn_reel.py --manifest .work/getyarn/piece-of-cake.json \
    --clips-dir .work/getyarn/clips --aspect vertical
```

Six clips in, a 1080x1920 / 12.9 s reel out: the phrase pinned for the whole run, its
meaning under it, each spoken line highlighted as it is said, and the source attributed
bottom-left. Downloading is also the one step that needs no clearance, so it is the fastest
part of the chain.

## Adding a backend

Implement the protocol in `reelmachine/browser/__init__.py`:

```python
name: str
def missing(self) -> list[str]                     # unmet requirements, for `reel doctor`
def describe(self) -> dict                         # a describe_row(...)
def fetch_html(self, url, *, timeout_s=None) -> str
def evaluate(self, url, expression, *, timeout_s=None) -> Any
def status(self) -> dict                           # for `browser_status`
def close(self) -> None
```

Then register it in `pyproject.toml` under `[project.entry-points."reelmachine.browser"]`.
Two rules apply to every backend here: import heavy dependencies **lazily** (a missing
package is a `missing()` row, never an ImportError — the default install stays browser-free
and Node-free), and record a cleared host with `record_clearance(profile, host)` so status
can report it. A deterministic fake alongside the real implementation is the house style;
`fake.py` is the model to copy.
