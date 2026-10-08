"""The chain: try each configured subtitle source in order, stop at the first script.

`embedded` first because it is free and exactly synced; then the databases. A source
that is not configured is skipped with a recorded reason; a source that found nothing
is recorded too — the error a caller sees lists every attempt, so "no subtitles" is
never a mystery.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ....core.errors import ProviderUnavailable
from . import SubtitleFetch, SubtitleRequest, chain_order, describe_row


class AutoSubtitleProvider:
    name = "auto"

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings
        self._members: list[Any] | None = None

    def members(self) -> list[Any]:
        if self._members is None:
            from ....core.registry import Registry

            registry = Registry()
            members: list[Any] = []
            for name in chain_order():
                if name in {"auto", "fake"}:
                    continue
                try:
                    provider_class = registry.load("subtitles", name)
                except ProviderUnavailable:
                    continue
                members.append(provider_class(self.settings))
            self._members = members
        return self._members

    def missing(self) -> list[str]:
        members = self.members()
        if not members:
            return ["no subtitle providers are installed"]
        if any(not member.missing() for member in members):
            return []  # at least one source can run; the rest are reported per attempt
        problems: list[str] = []
        for member in members:
            for problem in member.missing():
                problems.append(f"{member.name}: {problem}")
        return problems or ["no subtitle source is usable"]

    def describe(self) -> dict[str, Any]:
        return describe_row(
            self.name,
            "The subtitle chain: " + " → ".join(member.name for member in self.members()),
            self.missing(),
            chain=[member.name for member in self.members()],
        )

    def close(self) -> None:
        for member in self.members():
            close = getattr(member, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - closing must never mask the run
                    pass

    def fetch(self, request: SubtitleRequest, *, dest_dir: Path) -> SubtitleFetch:
        attempts: list[dict[str, Any]] = []
        for member in self.members():
            problems = member.missing()
            if problems:
                attempts.append(
                    {"provider": member.name, "result": "unavailable", "detail": "; ".join(problems)}
                )
                continue
            try:
                found = member.fetch(request, dest_dir=dest_dir)
            except ProviderUnavailable as exc:
                attempts.append(
                    {
                        "provider": member.name,
                        "result": "failed",
                        "detail": exc.message,
                        "hint": exc.hint,
                    }
                )
                continue
            except Exception as exc:  # noqa: BLE001 - one source must not sink the chain
                attempts.append(
                    {
                        "provider": member.name,
                        "result": "failed",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            if found is None:
                attempts.append({"provider": member.name, "result": "no match"})
                continue
            found.meta = {**found.meta, "attempts": attempts}
            return found

        raise ProviderUnavailable(
            "no subtitle source had a script for this title",
            hint=self._hint(attempts),
            details={"attempts": attempts},
        )

    @staticmethod
    def _hint(attempts: list[dict[str, Any]]) -> str:
        unavailable = sorted(
            {attempt["provider"] for attempt in attempts if attempt["result"] == "unavailable"}
        )
        if unavailable:
            return (
                "configure "
                + ", ".join(unavailable)
                + " (REEL_SUBDL_API_KEY / REEL_OPENSUBTITLES_API_KEY), or pass subtitle_path "
                "with your own SRT"
            )
        return "try another subtitle_language, or pass subtitle_path with your own SRT"
