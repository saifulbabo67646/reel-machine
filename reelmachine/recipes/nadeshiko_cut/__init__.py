"""The nadeshiko-cut recipe: anime and J-Drama dialogue reels, one code path.

Exports both the recipe (declaration + stages) and the functional API the test suite
and the demo script use; both run the same step functions.
"""

from .mine import mine_episodes
from .models import (
    NadeshikoCutInputs,
    Plan,
    PlannedSegment,
    parse_corpus,
)
from .pipeline import (
    RenderResult,
    build_plan,
    render,
    summarise,
)
from .recipe import NadeshikoCutRecipe, run_nadeshiko_cut
from .selection import (
    _category_of,
    compute_cut_window,
    compute_scene_window,
    expected_clip_ms,
    score_segment,
    select_segments,
)
from .verify import verify_plan

__all__ = [
    "NadeshikoCutInputs",
    "NadeshikoCutRecipe",
    "Plan",
    "PlannedSegment",
    "RenderResult",
    "_category_of",
    "build_plan",
    "compute_cut_window",
    "compute_scene_window",
    "expected_clip_ms",
    "mine_episodes",
    "parse_corpus",
    "render",
    "run_nadeshiko_cut",
    "score_segment",
    "select_segments",
    "summarise",
    "verify_plan",
]
