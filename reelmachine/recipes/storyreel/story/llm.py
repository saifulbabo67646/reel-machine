"""The model brain: an OpenAI-compatible chat model writes the concepts and the story.

Concepts are found map-reduce style — scenes are scanned in batches for the moments
that matter, then consolidated into five complete storytelling opportunities — which
scales to a two-hour film without pretending a model reads 1 000 cues at once. A short
reel's script is written in one call; a 5-10 minute explainer is too long for one
answer, so it is outlined into acts and written act by act, each act continuing from
the previous one's last lines. The caller's own concepts/story always win: this
provider only runs when they are absent. Errors steer repair (retry, smaller batches,
a different model) instead of leaking a traceback.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ....core.errors import InvalidInput, ProviderUnavailable
from ..models import (
    MicroClipSpec,
    ResolvedMedia,
    SceneChunk,
    SeoPackage,
    StoryConcept,
    StoryPackage,
    StoryReelInputs,
    StorySection,
)
from ..prompts import (
    ACT_SYSTEM,
    CONCEPTS_SYSTEM,
    MOMENTS_SYSTEM,
    OUTLINE_SYSTEM,
    SELECT_SYSTEM,
    SEO_SYSTEM,
    STORY_SYSTEM,
    content_line,
    media_line,
    scene_block,
)
from ..selection import section_budget
from . import describe_row

MAX_PROMPT_CHARS = 100_000
BATCH_CHARS = 14_000
BATCH_SCENES = 16
#: Up to this many sections the script is written in one call; beyond it, in acts.
SINGLE_CALL_SECTIONS = 30
#: One act call never asks for more than this many lines.
ACT_SECTION_CAP = 28
MIN_ACT_SECTIONS = 6
MAX_ACTS = 8


class LlmStoryBrain:
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
        env = os.environ.get
        self.settings = settings
        self.base_url = (
            base_url
            or env("REEL_STORY_BASE_URL")
            or env("REEL_SCRIPT_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        self.api_key = (
            api_key
            if api_key is not None
            else env("REEL_STORY_API_KEY")
            or env("REEL_SCRIPT_API_KEY")
            or env("OPENAI_API_KEY")
            or ""
        )
        self.model = model or env("REEL_STORY_MODEL") or env("REEL_SCRIPT_MODEL") or "gpt-4o-mini"
        self._client = client

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=300.0, follow_redirects=True)
        return self._client

    def missing(self) -> list[str]:
        if self.api_key:
            return []
        return ["REEL_STORY_API_KEY (or REEL_SCRIPT_API_KEY / OPENAI_API_KEY) is not set"]

    def describe(self) -> dict[str, Any]:
        return describe_row(
            self.name,
            f"Concepts and story written by {self.model}",
            self.missing(),
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------ concepts
    def concepts(
        self,
        *,
        scenes: list[SceneChunk],
        media: ResolvedMedia,
        inputs: StoryReelInputs,
        on_detail: Any = None,
    ) -> list[StoryConcept]:
        if not scenes:
            raise InvalidInput(
                "there is no transcript to analyse",
                hint="the subtitles stage produced no cues; check the subtitle source",
            )
        batches = _batches(scenes)
        moments: list[dict[str, Any]] = []
        for index, batch in enumerate(batches, start=1):
            if on_detail:
                on_detail(f"  · scanning {batch[0].id}–{batch[-1].id} ({index}/{len(batches)})")
            payload = self._chat(
                MOMENTS_SYSTEM,
                _moments_prompt(batch, media, inputs),
                temperature=0.5,
            )
            moments.extend(_moments_of(payload))
        if on_detail:
            on_detail(f"  · {len(moments)} moment(s) found; consolidating into 5 concepts")
        payload = self._chat(
            CONCEPTS_SYSTEM,
            _concepts_prompt(scenes, moments, media, inputs),
            temperature=0.8,
        )
        concepts = _concepts_of(payload)
        if not concepts:
            raise InvalidInput(
                "the model produced no concepts",
                hint="retry; or supply concepts yourself (the caller's always win)",
                details={"provider": self.name},
            )
        for index, concept in enumerate(concepts, start=1):
            if not concept.id:
                concepts[index - 1] = concept.model_copy(update={"id": f"c{index}"})
        return concepts[:5]

    # -------------------------------------------------------------------- script
    def script(
        self,
        *,
        scenes: list[SceneChunk],
        media: ResolvedMedia,
        inputs: StoryReelInputs,
        concept: StoryConcept,
        on_detail: Any = None,
    ) -> StoryPackage:
        budget = section_budget(inputs.target_ms)
        if budget <= SINGLE_CALL_SECTIONS:
            chosen = scenes
            if _transcript_chars(scenes) > MAX_PROMPT_CHARS:
                chosen = self._select_scenes(scenes, media, inputs, concept, on_detail=on_detail)
            payload = self._chat(
                STORY_SYSTEM,
                _story_prompt(chosen, scenes, media, inputs, concept),
                temperature=0.7,
            )
            story = _story_of(payload, concept)
        else:
            story = self._script_in_acts(
                scenes=scenes,
                media=media,
                inputs=inputs,
                concept=concept,
                budget=budget,
                on_detail=on_detail,
            )
        if not story.sections:
            raise InvalidInput(
                "the model produced no sections",
                hint="retry; or supply the story package yourself",
                details={"provider": self.name},
            )
        return story

    def _script_in_acts(
        self,
        *,
        scenes: list[SceneChunk],
        media: ResolvedMedia,
        inputs: StoryReelInputs,
        concept: StoryConcept,
        budget: int,
        on_detail: Any = None,
    ) -> StoryPackage:
        """A 5-10 minute script is outlined into acts, then written act by act.

        One answer holding 150 sections would be truncated or sloppy; an act of ~20
        lines fits comfortably and each act call carries the outline, its own scenes
        and the previous act's last lines, so the story keeps moving forward.
        """
        acts = self._outline(scenes, media, inputs, concept, budget, on_detail=on_detail)
        by_id = {scene.id: scene for scene in scenes}
        sections: list[StorySection] = []
        for index, act in enumerate(acts, start=1):
            asked = int(act["sections"])
            if on_detail:
                on_detail(f"  · act {index}/{len(acts)}: {act['title']} (~{asked} lines)")
            act_scenes = [by_id[scene_id] for scene_id in act["scenes"] if scene_id in by_id]
            if not act_scenes:
                act_scenes = _even_slice(scenes, index - 1, len(acts))
            previous = " ".join(section.voiceover for section in sections[-2:])[-500:]
            payload = self._chat(
                ACT_SYSTEM,
                _act_prompt(
                    media, inputs, concept, acts, act, act_scenes, previous, len(sections), budget
                ),
                temperature=0.75,
            )
            act_sections = _sections_of(payload)
            if not act_sections:
                raise InvalidInput(
                    f"the model produced no sections for act {act['id']!r}",
                    hint="retry; or supply the story package yourself",
                    details={"provider": self.name, "act": act["id"]},
                )
            sections.extend(act_sections)
        renumbered = [
            section.model_copy(update={"id": f"sec-{index:03d}"})
            for index, section in enumerate(sections, start=1)
        ]
        seo = self._seo(media, inputs, concept, renumbered)
        return StoryPackage(
            concept_id=concept.id,
            concept_name=concept.name,
            viral_reason=concept.why_viral,
            sections=renumbered,
            seo=seo,
        )

    def _outline(
        self,
        scenes: list[SceneChunk],
        media: ResolvedMedia,
        inputs: StoryReelInputs,
        concept: StoryConcept,
        budget: int,
        *,
        on_detail: Any = None,
    ) -> list[dict[str, Any]]:
        digest = "\n".join(scene_block(scene, full=False) for scene in scenes)
        if len(digest) > 60_000:
            digest = digest[:60_000] + "\n…"
        prompt = (
            f"Film: {media_line(media)}\n{content_line(inputs)}\n"
            f"Total short lines for the whole reel: {budget}\n\n"
            f"Story concept:\nname: {concept.name}\nangle: {concept.angle}\n"
            f"summary: {concept.summary}\n\nScene index:\n{digest}"
        )
        try:
            payload = self._chat(OUTLINE_SYSTEM, prompt, temperature=0.5)
        except (InvalidInput, ProviderUnavailable):
            return _even_acts(budget, scenes)
        raw = payload.get("acts") or []
        acts: list[dict[str, Any]] = []
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                continue
            act_scenes = [str(value) for value in (item.get("scenes") or []) if isinstance(value, str)]
            title = str(item.get("title") or "").strip()
            if not title and not act_scenes:
                continue
            asked = item.get("sections")
            try:
                asked_int = int(asked)
            except (TypeError, ValueError):
                asked_int = 0
            asked_int = max(MIN_ACT_SECTIONS, min(ACT_SECTION_CAP, asked_int or budget // 4))
            acts.append(
                {
                    "id": str(item.get("id") or f"a{index}"),
                    "title": title or f"Act {index}",
                    "summary": str(item.get("summary") or "").strip(),
                    "sections": asked_int,
                    "scenes": act_scenes,
                }
            )
        if len(acts) < 2:
            if on_detail:
                on_detail("  · outline unusable; splitting the film into even acts")
            return _even_acts(budget, scenes)
        return acts[:MAX_ACTS]

    def _seo(
        self,
        media: ResolvedMedia,
        inputs: StoryReelInputs,
        concept: StoryConcept,
        sections: list[StorySection],
    ) -> SeoPackage | None:
        head = " | ".join(section.voiceover for section in sections[:3])
        tail = " | ".join(section.voiceover for section in sections[-3:])
        prompt = (
            f"Film: {media_line(media)}\n{content_line(inputs)}\n"
            f"Story concept: {concept.name} — {concept.summary}\n"
            f"The reel is {len(sections)} short lines, {inputs.target_ms / 1000:.0f} seconds. "
            f"It opens with: {head}\nand ends with: {tail}"
        )
        try:
            payload = self._chat(SEO_SYSTEM, prompt, temperature=0.6)
        except (InvalidInput, ProviderUnavailable):
            return None
        raw = payload.get("seo") if isinstance(payload.get("seo"), dict) else payload
        try:
            return _LlmSeo.model_validate(raw).to_seo()
        except ValidationError:
            return None

    def _select_scenes(
        self,
        scenes: list[SceneChunk],
        media: ResolvedMedia,
        inputs: StoryReelInputs,
        concept: StoryConcept,
        *,
        on_detail: Any = None,
    ) -> list[SceneChunk]:
        if on_detail:
            on_detail("  · long transcript: selecting the scenes that matter")
        digest = "\n".join(scene_block(scene, full=False) for scene in scenes)
        prompt = (
            f"Film: {media_line(media)}\n{content_line(inputs)}\n"
            f"Story concept: {concept.name} — {concept.summary}\n\n"
            f"Scenes:\n{digest}"
        )
        try:
            payload = self._chat(SELECT_SYSTEM, prompt, temperature=0.3)
        except (InvalidInput, ProviderUnavailable):
            return _sampled(scenes, 30)
        wanted = [str(item) for item in (payload.get("scenes") or []) if isinstance(item, str)]
        by_id = {scene.id: scene for scene in scenes}
        chosen = [by_id[identifier] for identifier in wanted if identifier in by_id]
        if len(chosen) < 8:
            return _sampled(scenes, 30)
        return chosen

    # --------------------------------------------------------------------- chat
    def _chat(self, system: str, user: str, *, temperature: float) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderUnavailable(
                "writing the story needs a model key",
                hint="set REEL_STORY_API_KEY (or REEL_SCRIPT_API_KEY), or supply the story yourself",
                details={"provider": self.name},
            )
        last_error = ""
        for attempt in range(2):
            response = self._http().post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "temperature": temperature,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": user
                            if attempt == 0
                            else user + "\n\nAnswer with the JSON object only.",
                        },
                    ],
                },
            )
            if response.status_code == 401:
                raise ProviderUnavailable(
                    "the model rejected the API key",
                    hint="check REEL_STORY_API_KEY",
                    details={"provider": self.name},
                )
            if response.status_code == 429:
                raise ProviderUnavailable(
                    "the model is rate-limiting this key",
                    hint="wait a moment and retry, or use a smaller model",
                    details={"provider": self.name},
                )
            if response.status_code >= 400:
                raise ProviderUnavailable(
                    f"the model answered HTTP {response.status_code}",
                    hint="check REEL_STORY_BASE_URL and REEL_STORY_MODEL",
                    details={"provider": self.name, "body": response.text[:300]},
                )
            try:
                text = response.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ProviderUnavailable(
                    "the model returned no usable answer",
                    hint="retry; if it persists, check the model name",
                    details={"provider": self.name},
                ) from exc
            try:
                return _parse_json(text)
            except InvalidInput as exc:
                last_error = exc.message
        raise InvalidInput(
            "the model's answer was not JSON",
            hint="retry; a model that ignores the format can be swapped with REEL_STORY_MODEL",
            details={"provider": self.name, "error": last_error},
        )


# ------------------------------------------------------------------ prompt building


def _batches(scenes: list[SceneChunk]) -> list[list[SceneChunk]]:
    batches: list[list[SceneChunk]] = []
    current: list[SceneChunk] = []
    size = 0
    for scene in scenes:
        if current and (len(current) >= BATCH_SCENES or size + len(scene.text) > BATCH_CHARS):
            batches.append(current)
            current, size = [], 0
        current.append(scene)
        size += len(scene.text)
    if current:
        batches.append(current)
    return batches


def _moments_prompt(
    batch: list[SceneChunk], media: ResolvedMedia, inputs: StoryReelInputs
) -> str:
    scenes = "\n".join(scene_block(scene) for scene in batch)
    return f"Film: {media_line(media)}\n{content_line(inputs)}\n\nScenes:\n{scenes}"


def _concepts_prompt(
    scenes: list[SceneChunk],
    moments: list[dict[str, Any]],
    media: ResolvedMedia,
    inputs: StoryReelInputs,
) -> str:
    digest = "\n".join(scene_block(scene, full=False) for scene in scenes)
    if len(digest) > 60_000:
        digest = digest[:60_000] + "\n…"
    found = (
        "\n".join(
            f"- {moment.get('scene', '?')} [{moment.get('kind', '?')}] "
            f"strength {moment.get('strength', '?')}: {moment.get('why', '')}"
            for moment in moments
        )
        or "- (none found)"
    )
    return (
        f"Film: {media_line(media)}\n{content_line(inputs)}\n\n"
        f"Strongest moments found:\n{found}\n\nScene index:\n{digest}"
    )


def _story_prompt(
    chosen: list[SceneChunk],
    all_scenes: list[SceneChunk],
    media: ResolvedMedia,
    inputs: StoryReelInputs,
    concept: StoryConcept,
) -> str:
    scenes = "\n".join(scene_block(scene) for scene in chosen)
    rest = [scene for scene in all_scenes if scene not in chosen]
    omitted = ""
    if rest:
        digest = "\n".join(scene_block(scene, full=False) for scene in rest)
        if len(digest) > 30_000:
            digest = digest[:30_000] + "\n…"
        omitted = f"\n\nRest of the film (for orientation; clip timestamps may come from here too):\n{digest}"
    duration = f"Film duration: {all_scenes[-1].end_ms} ms\n" if all_scenes else ""
    return (
        f"Film: {media_line(media)}\n{duration}{content_line(inputs)}\n"
        f"Write about {section_budget(inputs.target_ms)} sections.\n\n"
        f"Story concept:\nname: {concept.name}\nangle: {concept.angle}\n"
        f"summary: {concept.summary}\ncharacters: {', '.join(concept.characters)}\n\n"
        f"Scenes:\n{scenes}{omitted}"
    )


def _sampled(scenes: list[SceneChunk], count: int) -> list[SceneChunk]:
    if len(scenes) <= count:
        return scenes
    step = len(scenes) / count
    return [scenes[min(len(scenes) - 1, int(index * step))] for index in range(count)]


def _even_slice(scenes: list[SceneChunk], index: int, count: int) -> list[SceneChunk]:
    """The `index`-th of `count` contiguous slices of the film — the fallback plan."""
    if not scenes:
        return []
    per = max(1, len(scenes) // count)
    start = min(len(scenes) - 1, index * per)
    return scenes[start : start + per] or scenes[-1:]


def _even_acts(budget: int, scenes: list[SceneChunk]) -> list[dict[str, Any]]:
    """When the outline is unusable: evenly sized acts over evenly sliced scenes."""
    count = 4 if budget <= 90 else 6
    per_act = min(ACT_SECTION_CAP, max(MIN_ACT_SECTIONS, budget // count))
    return [
        {
            "id": f"a{index + 1}",
            "title": f"Act {index + 1}",
            "summary": "",
            "sections": per_act,
            "scenes": [scene.id for scene in _even_slice(scenes, index, count)],
        }
        for index in range(count)
    ]


def _act_prompt(
    media: ResolvedMedia,
    inputs: StoryReelInputs,
    concept: StoryConcept,
    acts: list[dict[str, Any]],
    act: dict[str, Any],
    act_scenes: list[SceneChunk],
    previous: str,
    written: int,
    budget: int,
) -> str:
    outline = "\n".join(
        f"- {item['id']} {item['title']}: {item['summary']} (~{item['sections']} lines)"
        for item in acts
    )
    scenes_text = "\n".join(scene_block(scene) for scene in act_scenes)
    prior = previous.strip() or "(this is the first act — open the story)"
    return (
        f"Film: {media_line(media)}\n{content_line(inputs)}\n"
        f"Lines written so far: {written} of about {budget}.\n\n"
        f"Story concept:\nname: {concept.name}\nangle: {concept.angle}\n"
        f"summary: {concept.summary}\ncharacters: {', '.join(concept.characters)}\n\n"
        f"Full outline:\n{outline}\n\n"
        f"Now write act {act['id']} — {act['title']}: {act['summary']}\n"
        f"Write about {act['sections']} short lines.\n\n"
        f"Previous lines (continue from these, do not repeat them):\n{prior}\n\n"
        f"Scenes for this act:\n{scenes_text}"
    )


def _transcript_chars(scenes: list[SceneChunk]) -> int:
    return sum(len(scene.text) for scene in scenes)


# ------------------------------------------------------------------- model answers


class _LlmClip(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scene_ref: str = ""
    source_start_ms: int = 0
    source_end_ms: int | None = None
    scene: str = ""
    zoom: str = "none"
    text_overlay: str = ""
    transition: str = "cut"

    def to_spec(self) -> MicroClipSpec:
        zoom = self.zoom if self.zoom in ("none", "in", "out") else "none"
        transition = self.transition if self.transition in ("cut", "fade") else "cut"
        end = self.source_end_ms if (self.source_end_ms or 0) > self.source_start_ms else None
        return MicroClipSpec(
            scene_ref=self.scene_ref.strip(),
            source_start_ms=max(0, int(self.source_start_ms or 0)),
            source_end_ms=end,
            scene=self.scene.strip(),
            zoom=zoom,
            text_overlay=self.text_overlay.strip(),
            transition=transition,
        )


class _LlmSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    voiceover: str = ""
    clip: _LlmClip = _LlmClip()


class _LlmSeo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    description: str = ""
    hashtags: list[str] = []
    hook: str = ""
    cta: str = ""

    def to_seo(self) -> SeoPackage:
        return SeoPackage(
            title=self.title.strip(),
            description=self.description.strip(),
            hashtags=[str(tag).strip() for tag in self.hashtags if str(tag).strip()],
            hook=self.hook.strip(),
            cta=self.cta.strip(),
        )


class _LlmStory(BaseModel):
    model_config = ConfigDict(extra="ignore")

    concept_id: str = ""
    concept_name: str = ""
    viral_reason: str = ""
    sections: list[_LlmSection] = []
    seo: _LlmSeo | None = None


class _LlmConcept(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    name: str = ""
    angle: str = ""
    why_viral: str = ""
    emotional_score: Any = 5
    viral_score: Any = 5
    retention_score: Any = 5
    characters: list[Any] = []
    summary: str = ""
    audience_reaction: str = ""

    def to_concept(self, index: int) -> StoryConcept:
        return StoryConcept(
            id=self.id.strip() or f"c{index}",
            name=self.name.strip() or f"Concept {index}",
            angle=self.angle.strip(),
            why_viral=self.why_viral.strip(),
            emotional_score=_score(self.emotional_score),
            viral_score=_score(self.viral_score),
            retention_score=_score(self.retention_score),
            characters=[str(item).strip() for item in self.characters if str(item).strip()],
            summary=self.summary.strip(),
            audience_reaction=self.audience_reaction.strip(),
        )


def _score(value: Any) -> int:
    try:
        number = int(float(re.match(r"\s*(-?\d+(?:\.\d+)?)", str(value)).group(1)))  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError):
        return 5
    return max(1, min(10, number))


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?|```", "", text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise InvalidInput(
            "the model's answer was not JSON",
            hint="retry; a model that ignores the format can be swapped with REEL_STORY_MODEL",
            details={"answer": cleaned[:200]},
        )
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise InvalidInput(
            "the model's answer did not parse as JSON",
            hint="retry; if it persists, check the model",
            details={"answer": cleaned[:200]},
        ) from exc
    if not isinstance(payload, dict):
        raise InvalidInput(
            "the model's answer was not a JSON object",
            hint="retry; the contract is a single JSON object",
            details={"answer": cleaned[:200]},
        )
    return payload


def _moments_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    moments = payload.get("moments") or []
    return [moment for moment in moments if isinstance(moment, dict)]


def _concepts_of(payload: dict[str, Any]) -> list[StoryConcept]:
    raw = payload.get("concepts") or []
    concepts: list[StoryConcept] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        try:
            concepts.append(_LlmConcept.model_validate(item).to_concept(index))
        except ValidationError:
            continue
    return concepts


def _sections_from(raw_sections: list[_LlmSection]) -> list[StorySection]:
    sections: list[StorySection] = []
    for index, section in enumerate(raw_sections, start=1):
        voiceover = section.voiceover.strip()
        if not voiceover:
            continue
        sections.append(
            StorySection(
                id=section.id.strip() or f"sec-{index:03d}",
                voiceover=voiceover,
                clip=section.clip.to_spec(),
            )
        )
    return sections


def _sections_of(payload: dict[str, Any]) -> list[StorySection]:
    """Read `{"sections": [...]}` — an act answer, without concept/SEO fields."""
    try:
        model = _LlmStory.model_validate(payload)
    except ValidationError as exc:
        raise InvalidInput(
            "the model's answer did not look like sections",
            hint='expected {"sections": [{"voiceover": "…", "clip": {…}}]}',
            details={"error": str(exc)[:300]},
        ) from exc
    return _sections_from(model.sections)


def _story_of(payload: dict[str, Any], concept: StoryConcept) -> StoryPackage:
    try:
        model = _LlmStory.model_validate(payload)
    except ValidationError as exc:
        raise InvalidInput(
            "the model's story did not look like a story package",
            hint='expected {"sections": [{"voiceover": "…", "clip": {…}}], "seo": {…}}',
            details={"error": str(exc)[:300]},
        ) from exc
    return StoryPackage(
        concept_id=model.concept_id.strip() or concept.id,
        concept_name=model.concept_name.strip() or concept.name,
        viral_reason=model.viral_reason.strip() or concept.why_viral,
        sections=_sections_from(model.sections),
        seo=model.seo.to_seo() if model.seo else None,
    )
