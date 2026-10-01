"""The engine: how a recipe is executed.

This package owns stage execution. It must not import CLI or MCP concerns.
"""

from .runner import StageRun, config_subset, run_stages

__all__ = ["StageRun", "config_subset", "run_stages"]
