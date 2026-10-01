"""Script → beats.

A structured script is used exactly as given — that is the path a calling agent (or a
person) takes, and it is the one to prefer: the caller knows the lesson it wants told.
A topic alone goes through the `scripts` provider group: the deterministic `template`
by default, or `llm` for a deployment that would rather a model wrote the beats.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ...core.errors import InvalidInput, ProviderUnavailable
from .models import Beat

SYSTEM_PROMPT = (
    "You write scripts for short hand-drawn explainer videos. Answer with JSON only, "
    'shaped as {"beats": [{"narration": "...", "keywords": ["..."]}]}. Each beat is one '
    "sentence of narration (at most 22 words, spoken aloud) and one to three keywords; "
    "the keywords become the on-screen labels, so keep them short nouns of two words at "
    "most. Tell one idea per beat, in order, starting from what the viewer already knows. "
    "Use three to eight beats."
)

TEMPLATES = (
    ("hook", "Most people get {topic} wrong, and it costs them."),
    ("shape", "Here is how {topic} actually works, in one picture."),
    ("payoff", "Do this with {topic}, and the result compounds."),
)


class TemplateScriptProvider:
    """A deterministic three-beat script for any topic."""

    name = "template"

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings

    def missing(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "scripts",
            "description": "A deterministic three-beat script for any topic",
            "missing": [],
        }

    def beats(self, topic: str, *, language: str = "en") -> list[Beat]:
        cleaned = (topic or "this idea").strip().rstrip(".")
        beats: list[Beat] = []
        for index, (beat_id, template) in enumerate(TEMPLATES, start=1):
            narration = template.format(topic=cleaned)
            keywords = self._keywords(cleaned)
            beats.append(
                Beat(id=f"beat-{index}", narration=narration, keywords=keywords)
            )
        return beats

    @staticmethod
    def _keywords(topic: str) -> list[str]:
        words = [word.strip(".,!?").capitalize() for word in topic.split() if len(word) > 2]
        return words[:3] or ["Idea"]


class _LlmBeat(BaseModel):
    """A model answer is not schema-lawyered: unknown keys are dropped, not fatal."""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    narration: str = ""
    keywords: list[str] = []

    def to_beat(self, index: int) -> Beat:
        return Beat(
            id=self.id or f"beat-{index}",
            narration=self.narration.strip(),
            keywords=[str(keyword) for keyword in self.keywords][:3],
        )


class _ScriptPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    beats: list[_LlmBeat]


class LlmScriptProvider:
    """A topic becomes beats through an OpenAI-compatible chat model.

    The caller's own script always wins when it supplies one — this is for the case where
    all an agent has is a topic.
    """

    name = "llm"

    def __init__(
        self,
        settings: Any = None,
        *,
        client: httpx.Client | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = (
            base_url or os.environ.get("REEL_SCRIPT_BASE_URL") or "https://api.openai.com/v1"
        ).rstrip("/")
        self.api_key = (
            api_key
            if api_key is not None
            else os.environ.get("REEL_SCRIPT_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        )
        self.model = model or os.environ.get("REEL_SCRIPT_MODEL", "gpt-4o-mini")
        self._client = client

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=120.0, follow_redirects=True)
        return self._client

    def missing(self) -> list[str]:
        if self.api_key:
            return []
        return ["REEL_SCRIPT_API_KEY (or OPENAI_API_KEY) is not set"]

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": "scripts",
            "description": f"A topic written into beats by {self.model}",
            "missing": self.missing(),
        }

    def beats(self, topic: str, *, language: str = "en") -> list[Beat]:
        if not self.api_key:
            raise ProviderUnavailable(
                "writing the script needs a model key",
                hint="set REEL_SCRIPT_API_KEY (or OPENAI_API_KEY), or supply beats yourself",
                details={"provider": self.name},
            )
        response = self._http().post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            json={
                "model": self.model,
                "temperature": 0.4,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Write the script for a short explainer about: {topic} "
                        f"(spoken in {language}).",
                    },
                ],
            },
        )
        response.raise_for_status()
        try:
            text = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailable(
                "the model returned no usable answer",
                hint="retry; if it persists, check the model name and the account",
                details={"provider": self.name},
            ) from exc
        payload = _parse_script(text)
        beats = [
            beat.to_beat(index)
            for index, beat in enumerate(payload.beats, start=1)
            if beat.narration.strip()
        ]
        if not beats:
            raise InvalidInput(
                "the model produced no beats",
                hint="retry, or supply the script yourself",
                details={"provider": self.name},
            )
        return beats

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def _parse_script(text: str) -> _ScriptPayload:
    """Read the JSON object out of a model answer, fences and prose included."""
    cleaned = re.sub(r"```(?:json)?|```", "", text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise InvalidInput(
            "the model's answer was not JSON",
            hint="retry; a model that ignores the format can be swapped with REEL_SCRIPT_MODEL",
            details={"answer": cleaned[:200]},
        )
    try:
        return _ScriptPayload.model_validate(json.loads(cleaned[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise InvalidInput(
            "the model's answer did not look like a script",
            hint='expected {"beats": [{"narration": "…", "keywords": ["…"]}]}',
            details={"answer": cleaned[:200]},
        ) from exc
