"""The MCP server: the engine, exposed to agents.

The `mcp` SDK is an optional extra; nothing in the engine imports this package.
"""

from .server import build_server, serve

__all__ = ["build_server", "serve"]
