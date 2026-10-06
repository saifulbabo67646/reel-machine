"""Second, independent re-measurement of an aligned plan ("refuse rather than guess")."""

from __future__ import annotations

from typing import Any

from ...align import cached_envelope
from ...config import Settings, get_settings
from ...sources import SourceProvider, get_provider
from .models import Plan


def verify_plan(
    plan: Plan,
    *,
    provider: SourceProvider | None = None,
    settings: Settings | None = None,
    client: Any | None = None,
    margin_ms: int = 4000,
) -> list[dict[str, Any]]:
    """Re-measure each mapped window against a tight search around the prediction.

    A second, independent look: if the first pass locked onto the wrong repeated
    line, this low-margin re-check usually drops its score and flags it.
    """
    settings = settings or get_settings()
    provider = provider or get_provider(settings=settings)
    report: list[dict[str, Any]] = []

    for item in plan.ok_items:
        if item.asset is None:
            continue
        entry: dict[str, Any] = {
            "segmentId": item.key,
            "media": item.media_name,
            "episode": item.segment.episode,
        }
        try:
            clips = provider.reference_clips([item.segment], client=client, asset=item.asset)
            clip = clips.get(item.key)
            if clip is None:
                entry.update({"status": "no-clip"})
                report.append(entry)
                continue
            episode_env = cached_envelope(item.asset.url, headers=item.asset.headers or None)
            from ...align import envelope_of, locate

            match = locate(
                episode_env,
                envelope_of(clip),
                search_start_ms=item.local_start_ms,
                margin_ms=margin_ms,
            )
            entry.update(
                {
                    "status": "ok" if match.score >= 0.3 else "suspect",
                    "score": round(match.score, 4),
                    "predictedMs": item.local_start_ms,
                    "remeasuredMs": match.offset_ms,
                    "deltaMs": match.offset_ms - item.local_start_ms,
                }
            )
        except Exception as exc:  # noqa: BLE001 - report, don't abort a batch
            entry.update({"status": "error", "error": str(exc)})
        report.append(entry)
    return report
