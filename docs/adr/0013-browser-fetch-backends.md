# 0013 — Browser fetch backends for Cloudflare-gated pages

Status: accepted

## Context

Some sources a reel needs sit behind Cloudflare Turnstile, and the engine had no answer
for them. Everything cheap was measured against getyarn.io and refused: `curl` with the
site's `cf_clearance` cookie plus its User-Agent, client hints and whole cookie jar (403);
a clearance minted inside a real Chrome and copied into a *new* browser session (403); a
fresh browser session headless or headed, undisturbed or re-navigated (stuck on the
interstitial), and eventually the IP itself (TCP-level refusal).

Two things were learned from that, and both are load-bearing:

1. **Clearance belongs to the client, not to the cookie string.** Anything that "solves"
   the challenge and hands back a cookie is useless to a different client — which rules out
   the shape of the off-the-shelf solvers, including the Node service this work started
   from (`Iruko233/Turnstile-Slip`, which also ships no licence and depends on a library
   that has stopped receiving updates).
2. **A widget needs a driver that can click inside it.** The Turnstile widget is a
   cross-origin iframe. A Playwright-family driver clicks it (measured: 7.7 s cold, on a
   control page); a raw-CDP driver cannot address the frame at all. Headless did not clear
   the widget while headed did, twice over.

## Decision

Add a first-party provider group, `reelmachine.browser`, whose providers fetch a URL
through a browser session they own:

- **`patchright` is the default backend** (Apache-2.0, actively maintained): a patched
  Playwright driver, run as a persistent headed context in `REEL_BROWSER_PROFILE`, clearing
  the challenge by clicking the widget inside its frame.
- **`agent-browser` stays as a fallback backend** — it is the path that already worked when
  a session had cleared the gate, it needs no Python dependency, and it keeps the
  capability usable on machines that have Node but no patchright.
- **`fake`** is the deterministic backend, so the group is testable without a browser.
- The contract is `fetch_html` + `evaluate` (both backends have the latter natively), with
  `status()` for discovery. Failures separate `GatedFetchError` from `BrowserFetchError`
  because a gate and an unreachable host have opposite remedies.
- Discovery is exposed over MCP as `browser_status` — cheap, read-only, and never
  launching a browser, because the surface's rule is that no tool call waits on a solve.
- The getyarn search tool becomes a consumer of the facility rather than its own
  subprocess wrapper.

## Amendment — the file is a different gate from the page

Building the download step (getyarn's own clip link, `https://y.getyarn.io/<clip-id>.mp4`)
found that a *file host* gates on something else entirely. 403 to `curl`/`urllib` with the
site's cookies, UA and client hints; 403 to Playwright's own request context and to
`route.fetch()`; and **200 `video/mp4` with curl_cffi impersonating Chrome, no cookies at
all**. Header copying never mattered — the connection did.

So there are two mechanisms, deliberately not merged:

- the **browser group** keeps the *page*, because an interactive challenge is a real
  challenge (getyarn's search page answers an impersonating client with 403 and "Just a
  moment..." as well);
- **`reelmachine.impersonate`** takes the *file*, where looking like a browser at the TLS
  layer is sufficient and no browser is involved at all.

`browser_status` reports both, so an agent can tell which gate it is facing instead of
reaching for the wrong tool. The lesson carried into the docs is the general one: when a
fetch is refused, the first question is *which layer* is refusing — a challenge wants a
browser, a fingerprint wants an impersonating client, and an IP block wants neither.

## Consequences

- A gated page is fetched fetch-through: the same client that cleared the gate does the
  request, which is the only thing measured to keep working.
- The default install is unchanged — `patchright` lives behind the `browser` extra and is
  imported lazily, so `pip install reel-machine` still needs no browser and no Node, and a
  missing backend is a `missing()` row in `reel doctor` rather than an ImportError.
- The group is INSPECTED, so its requirements reach `reel doctor`, and either backend can
  be gated by name (`REEL_PROVIDERS_DISABLED=patchright`), like any other provider. Gating
  both leaves gated pages unfetchable, and `auto` says so rather than naming a backend it
  cannot load.
- `auto` prefers capability, not recency: patchright first, because only it can clear a
  widget. If patchright proves worse in practice on some deployment, `REEL_BROWSER` pins
  the other one and no code changes.
- An IP that a site has blocked stays blocked. This decision makes the gate solvable; it
  does not make a refused network path usable, and `BrowserFetchError` says so instead of
  pretending a different backend would help.
- A newer Chromium for patchright is a ~150 MB download (`patchright install chromium`),
  avoided entirely when a real Chrome is installed.
