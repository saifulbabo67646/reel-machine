"""The inverse of planning: which graded words does an episode I already have contain?"""

from __future__ import annotations

from typing import Any, Sequence

from ...config import Settings, get_settings
from ...nadeshiko import NadeshikoClient


def mine_episodes(
    client: NadeshikoClient,
    targets: Sequence[tuple[str, int]],
    *,
    settings: Settings | None = None,
    levels: Sequence[int] | None = None,
    limit: int = 25,
    per_episode_cap: int = 900,
    categories: Sequence[str] | None = None,
    dictionary: Any = None,
    verbose: bool = False,
) -> list[tuple[Any, int]]:
    """Every JLPT word spoken across `targets`, ranked by how often it is said.

    This is what makes an already-downloaded episode worth more than the single
    reel it was fetched for: pull all of its dialogue once and it yields dozens
    of graded words, each of which can become its own reel *from the same file*
    — no further downloading.

    Note the deliberate inversion of `build_plan`: that searches for a word and
    asks which episodes have it, this asks which words an episode has.  The API
    only offers the first, so it is replayed per episode behind a media filter.
    """
    from ...vocab import JishoDictionary, mine_tokens

    settings = settings or get_settings()
    ratings = settings.content_rating or None
    tokens: list[Any] = []
    for media_public_id, episode in targets:
        filters: dict[str, Any] = {
            "media": {"include": [{"mediaPublicId": media_public_id, "episodes": [episode]}]}
        }
        if categories:
            filters["category"] = [c.upper() for c in categories]
        found = 0
        for segment in client.iter_search(
            None,
            max_results=per_episode_cap,
            pages=max(1, per_episode_cap // 50 + 1),
            filters=filters,
            content_rating=ratings,
            include_media=True,
        ):
            if segment.textJa.tokens:
                tokens.extend(segment.textJa.tokens)
                found += 1
        if verbose:
            print(f"  · {media_public_id} ep{episode}: {found} line(s) of dialogue")
    if not tokens:
        return []

    if dictionary is None and not settings.dictionary_offline:
        dictionary = JishoDictionary(settings.cache_dir / "dictionary.json")
    return mine_tokens(
        tokens,
        dictionary=dictionary,
        levels=list(levels) if levels else None,
        limit=limit,
    )
