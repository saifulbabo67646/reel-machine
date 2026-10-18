"""Build and run the MCP server.

One server, two transports: `stdio` for local agents and `streamable-http` for hosted
deployments. The tool list is deliberately small and frozen; within a major version the
schemas only grow.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import sys
from typing import Any

from .. import __version__
from ..config import get_settings
from ..core.errors import ReelError
from ..engine import Engine
from ..observability import configure_logging
try:  # the SDK is an optional extra; annotations must still resolve without it
    from mcp.server.mcpserver import Context
except ImportError:  # pragma: no cover - exercised only when the extra is absent
    Context = Any  # type: ignore[assignment,misc]

from .artifacts import ArtifactSigner, artifact_file
from .tenants import TenantRegistry
from .tools import McpTools

log = logging.getLogger("reelmachine.mcp")

INSTRUCTIONS = (
    "reel-machine turns a request into a rendered video reel. Start with list_recipes, "
    "check your inputs with describe_recipe/probe, then start_job and poll get_job. "
    "Artifacts are fetched with get_artifact, which returns a signed URL — never bytes. "
    "Jobs outlive a single tool call; nothing here blocks on a render. Call browser_status "
    "when a source or page is behind Cloudflare Turnstile: it reports which stealth "
    "browser backend is selected, whether it is installed, and which hosts its profile "
    "has already cleared."
)

DESCRIPTIONS: dict[str, str] = {
    "list_recipes": (
        "List the reel recipes this server can make (id, title, render modes, cost). "
        "Call this first to discover what is available.\n"
        "Example: {}"
    ),
    "browser_status": (
        "Report the browser backends available for fetching pages that sit behind "
        "Cloudflare Turnstile: which one is selected (patchright preferred, agent-browser "
        "as fallback), whether it is installed, the persistent profile, and which hosts "
        "that profile has already cleared. Cheap and read-only — it never launches a "
        "browser, because a solve takes seconds and no tool call here may wait.\n"
        "Example: {}"
    ),
    "describe_recipe": (
        "Describe one recipe: its input JSON Schema, the providers it needs and their "
        "state, its cost/quota implications, its artifacts, and one example request. "
        "Call this before start_job to build valid inputs.\n"
        "Example: {\"recipeId\": \"nadeshiko-cut\"}"
    ),
    "probe": (
        "Validate inputs and source availability without spending quota or rendering. "
        "Use it to catch a bad request cheaply before start_job.\n"
        "Example: {\"recipeId\": \"nadeshiko-cut\", \"inputs\": {\"word\": \"彼女\", "
        "\"only\": [\"<mediaPublicId>:3\"]}}"
    ),
    "start_job": (
        "Start a render and return a job id immediately — this never waits for the "
        "render. Poll get_job for state, progress and artifacts. Pass an idempotencyKey "
        "to make retries safe: the same key returns the same job.\n"
        "Example: {\"recipe\": \"nadeshiko-cut\", \"inputs\": {\"word\": \"彼女\", "
        "\"count\": 5}, \"idempotencyKey\": \"reel-2026-10-01\"}"
    ),
    "get_job": (
        "Current state of one job: stage and percent, produced artifacts, and a "
        "structured error (code, message, hint) when it failed.\n"
        "Example: {\"jobId\": \"9f2c1ab34d5e\"}"
    ),
    "list_jobs": (
        "List this caller's jobs, newest first, optionally filtered by state.\n"
        "Example: {\"state\": \"running\", \"limit\": 20}"
    ),
    "cancel_job": (
        "Cancel a queued or running job. A job that already finished is returned "
        "unchanged.\n"
        "Example: {\"jobId\": \"9f2c1ab34d5e\"}"
    ),
    "get_artifact": (
        "Get a signed, expiring URL for one artifact of a finished job, plus its size, "
        "hash, provenance and licence. It never returns file bytes; fetch the URL "
        "yourself.\n"
        "Example: {\"jobId\": \"9f2c1ab34d5e\", \"name\": \"reel.mp4\"}"
    ),
    "suggest_words": (
        "Words the corpus can actually teach, best first — for choosing what to make a "
        "reel about. Reads the subtitles on disk, so it is fast, free and read-only; "
        "nothing is rendered and no job is started. Words come back because they recur "
        "in real dialogue, not from a word list, so each one is known to have examples "
        "behind it.\n"
        "Example: {\"recipe\": \"wordreel\", \"limit\": 12}"
    ),
    "find_examples": (
        "The lines of dialogue that use a word or phrase, each with its file, timestamp "
        "and the form that matched — call this before start_job to see exactly what a "
        "reel would be built from. Inflected forms match too (`gave up` for \"give "
        "up\") unless allowInflected is false. Read-only: no job, no render.\n"
        "Example: {\"word\": \"give up\", \"inputs\": {\"library\": [\"/media/films\"]}}"
    ),
    "find_assets": (
        "Search the CC0 catalogue for an explainer3d set or cast: kind \"model\" (props, food, "
        "furniture, a photoreal fish), \"sky\" (360° photos of real places — kitchens, living "
        "rooms, workshops, streets, lakes — that light a set and form its backdrop) or "
        "\"texture\" (floors, walls, counters). Read-only and free.\n"
        "Example: {\"query\": \"kitchen\", \"kind\": \"sky\"}"
    ),
    "find_places": (
        "Find countries for a mapreel script by name or code ('Holland', 'Korea', 'ITA'). Returns ISO "
        "codes and flags names that mean more than one country. With `country`, finds that country's "
        "states, prefectures or provinces instead (ISO 3166-2 codes like US-TX, JP-13); an empty query "
        "lists them all, and a country marked \"regions\": true has some. Fetches the map data once on "
        "first use (regions: a larger file, only when asked for).\n"
        "Example: {\"query\": \"texas\", \"country\": \"USA\"}"
    ),
    "find_images": (
        "Search Wikimedia Commons for an image (a logo, photo or flag) to put on a mapreel item. "
        "Returns ids like \"commons:File:Nike.svg\" to use as an item's \"image\", with licence, "
        "attribution and `permitted` (free to use). Files without a free licence are hidden unless "
        "include_nonfree. kind: any | logo | photo | flag. Logos may be trademarked.\n"
        "Example: {\"query\": \"Nike\", \"kind\": \"logo\"}"
    ),
    "make_cast": (
        "Make the two characters for a dialoguereel: built-ins by name (cupcake, mug, …) or your own SVG "
        "following describe_recipe('dialoguereel').guide.characters. Free and instant; returns a castId to "
        "reuse across reels and starts a preview job whose cast.png shows each character. Example: "
        "{\"characters\": [{\"id\": \"kid\", \"name\": \"Cupcake\", \"side\": \"left\", \"source\": "
        "{\"kind\": \"builtin\", \"name\": \"cupcake\"}}, {\"id\": \"mom\", \"name\": \"Mug\", "
        "\"side\": \"right\", \"source\": {\"kind\": \"builtin\", \"name\": \"mug\"}}]}"
    ),
    "find_models": (
        "Search the CC0 3D model catalogue (Poly Haven, Khronos samples) for an explainer3d "
        "cast: bowls, food, rocks, plants, tools, a photoreal fish. Returns model sources "
        "to put in a cast member. Read-only and free; the first call fetches the "
        "catalogue.\n"
        "Example: {\"query\": \"wooden bowl\"}"
    ),
}


def _require_mcp() -> Any:
    try:
        from mcp.server import MCPServer  # noqa: F401
        from mcp.server.mcpserver import Context  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise SystemExit(
            "the MCP server needs the optional dependency:\n"
            "  pip install 'reel-machine[mcp]'"
        ) from exc
    return None


def build_server(
    *,
    engine: Engine | None = None,
    tenants: TenantRegistry | None = None,
    signer: ArtifactSigner | None = None,
    transport: str = "stdio",
    base_url: str = "",
) -> Any:
    """Build the MCP server with exactly the tools described in DESCRIPTIONS."""
    _require_mcp()
    from mcp.server import MCPServer
    from mcp.server.mcpserver import Context

    if engine is None:
        engine = Engine(dataclasses.replace(get_settings()), policy_for=(tenants or TenantRegistry()).policy)
    if transport == "streamable-http":
        engine.settings.local_files = False  # a remote caller never reads this machine's files
    tenants = tenants if tenants is not None else TenantRegistry.from_env()
    signer = signer if signer is not None else ArtifactSigner.from_env()
    tools = McpTools(
        engine=engine, tenants=tenants, signer=signer, transport=transport, base_url=base_url
    )

    server = MCPServer(name="reel-machine", version=__version__, instructions=INSTRUCTIONS)

    @server.tool(name="list_recipes", description=DESCRIPTIONS["list_recipes"])
    def list_recipes(ctx: Context) -> dict[str, Any]:
        return tools.list_recipes(ctx)

    @server.tool(name="browser_status", description=DESCRIPTIONS["browser_status"])
    def browser_status(ctx: Context) -> dict[str, Any]:
        return tools.browser_status(ctx)

    @server.tool(name="describe_recipe", description=DESCRIPTIONS["describe_recipe"])
    def describe_recipe(ctx: Context, recipe_id: str) -> dict[str, Any]:
        return tools.describe_recipe(ctx, recipe_id)

    @server.tool(name="probe", description=DESCRIPTIONS["probe"])
    def probe(ctx: Context, recipe_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
        return tools.probe(ctx, recipe_id, inputs)

    @server.tool(name="start_job", description=DESCRIPTIONS["start_job"])
    def start_job(
        ctx: Context,
        recipe: str,
        inputs: dict[str, Any],
        idempotency_key: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return tools.start_job(ctx, recipe, inputs, idempotency_key, config)

    @server.tool(name="get_job", description=DESCRIPTIONS["get_job"])
    def get_job(ctx: Context, job_id: str) -> dict[str, Any]:
        return tools.get_job(ctx, job_id)

    @server.tool(name="list_jobs", description=DESCRIPTIONS["list_jobs"])
    def list_jobs(ctx: Context, state: str | None = None, limit: int = 50) -> dict[str, Any]:
        return tools.list_jobs(ctx, state, limit)

    @server.tool(name="cancel_job", description=DESCRIPTIONS["cancel_job"])
    def cancel_job(ctx: Context, job_id: str) -> dict[str, Any]:
        return tools.cancel_job(ctx, job_id)

    @server.tool(name="get_artifact", description=DESCRIPTIONS["get_artifact"])
    def get_artifact(ctx: Context, job_id: str, name: str) -> dict[str, Any]:
        return tools.get_artifact(ctx, job_id, name)

    @server.tool(name="suggest_words", description=DESCRIPTIONS["suggest_words"])
    def suggest_words(
        ctx: Context,
        recipe: str = "wordreel",
        inputs: dict[str, Any] | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        return tools.suggest_words(ctx, recipe, inputs, limit)

    @server.tool(name="find_examples", description=DESCRIPTIONS["find_examples"])
    def find_examples(
        ctx: Context,
        word: str,
        recipe: str = "wordreel",
        inputs: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return tools.find_examples(ctx, word, recipe, inputs, limit)

    @server.tool(name="find_assets", description=DESCRIPTIONS["find_assets"])
    def find_assets(ctx: Context, query: str, kind: str = "model", limit: int = 12) -> dict[str, Any]:
        return tools.find_assets(ctx, query, kind, limit)

    @server.tool(name="find_models", description=DESCRIPTIONS["find_models"])
    def find_models(ctx: Context, query: str, limit: int = 12) -> dict[str, Any]:
        return tools.find_models(ctx, query, limit)

    @server.tool(name="find_places", description=DESCRIPTIONS["find_places"])
    def find_places(ctx: Context, query: str, limit: int = 8, country: str | None = None) -> dict[str, Any]:
        return tools.find_places(ctx, query, limit, country)

    @server.tool(name="find_images", description=DESCRIPTIONS["find_images"])
    def find_images(
        ctx: Context, query: str, kind: str = "any", limit: int = 12, include_nonfree: bool = False
    ) -> dict[str, Any]:
        return tools.find_images(ctx, query, kind, limit, include_nonfree)

    @server.tool(name="make_cast", description=DESCRIPTIONS["make_cast"])
    def make_cast(ctx: Context, characters: list[dict[str, Any]], preview: bool = True) -> dict[str, Any]:
        return tools.make_cast(ctx, characters, preview)

    @server.custom_route("/artifacts/{caller}/{job}/{name}", methods=["GET"], name="artifact")
    async def artifact_route(request: Any) -> Any:  # pragma: no cover - exercised over HTTP
        from starlette.responses import FileResponse, PlainTextResponse

        caller = request.path_params["caller"]
        job_id = request.path_params["job"]
        name = request.path_params["name"]
        try:
            expires = int(request.query_params.get("expires", "0") or 0)
        except ValueError:
            return PlainTextResponse("bad expires", status_code=400)
        signature = request.query_params.get("sig", "")
        if not signer.verify(caller, job_id, name, expires, signature):
            return PlainTextResponse("invalid or expired signature", status_code=403)
        try:
            path = artifact_file(engine, job_id, name, caller=caller)
        except ReelError:
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(path, filename=name)

    return server


def serve(
    *,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
    tenants_path: str | None = None,
) -> None:
    """Run the server until interrupted."""
    _require_mcp()
    if tenants_path:
        os.environ["REEL_MCP_TENANTS"] = str(tenants_path)
    configure_logging()
    tenants = TenantRegistry.from_env()
    engine = Engine(dataclasses.replace(get_settings()), policy_for=tenants.policy)
    server = build_server(engine=engine, tenants=tenants, transport=transport)
    log.info("serving %s over %s", "reel-machine", transport)
    try:
        if transport == "streamable-http":
            server.run("streamable-http", host=host, port=port)
        else:
            server.run("stdio")
    finally:
        engine.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="reel-machine MCP server")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--tenants", default=None, help="JSON file of caller tokens and policies")
    args = parser.parse_args(argv)
    serve(transport=args.transport, host=args.host, port=args.port, tenants_path=args.tenants)
    return 0


if __name__ == "__main__":
    sys.exit(main())
