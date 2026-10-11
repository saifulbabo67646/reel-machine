"""The wordreel recipe: an English word or phrase becomes a learning reel.

The public surface is the recipe declaration and the vocabulary matcher — the matcher
is exported because "does this line use this word?" is useful on its own, to an agent
deciding what to teach before it asks for a render.
"""

from .clips import ClipCandidate, ClipRequest, ClipSource, describe_row
from .matching import Match, WordMatcher, content_words, score_example, variants
from .models import WordReelInputs
from .recipe import WordReelRecipe, run_wordreel

__all__ = [
    "ClipCandidate",
    "ClipRequest",
    "ClipSource",
    "Match",
    "WordMatcher",
    "WordReelInputs",
    "WordReelRecipe",
    "content_words",
    "describe_row",
    "run_wordreel",
    "score_example",
    "variants",
]
