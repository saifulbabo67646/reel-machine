"""The techreel recipe: a funny AI/tech explainer as a vertical reel of narrated motion graphics."""

from __future__ import annotations

from typing import Any

from ...core.manifest import CheckResult, VerificationReport
from ...core.recipe import (
    CostNote,
    ProbeContext,
    ProbeReport,
    ProviderReq,
    RecipeSpec,
    StageSpec,
)
from ...core.stage import StageContext
from ..doodle.narration import provider_for
from . import lint
from .models import TechReelInputs
from .schedule import estimate
from .stages import (
    ComposeStage,
    NarrationStage,
    PreviewStage,
    RenderStage,
    ScriptStage,
    lint_findings,
)
from .verify import verify_techreel

# an original Hindi new-tool reel about a fictional open-source app: Devanagari `say` with the numbers as words,
# Hinglish `text` with the digits and the accents
EXAMPLE: dict[str, Any] = {
    "mode": "storyboard",
    "title": "Pocket LLM: AI on an old laptop",
    "language": "hi",
    "voice": "fake:default",
    "look": "mint",
    "hero": {"kind": "orb"},
    "creator": {"name": "Your Name", "handle": "@yourhandle"},
    "sources": ["https://example.com/pocket-llm"],
    "beats": [
        {
            "id": "hook", "kind": "hook", "kicker": "NEW TOOL", "heading": "Laptop pe *apna* AI?",
            "lines": [
                {"say": "भाई, अब तुम्हारे पुराने लैपटॉप पर भी अपना खुद का AI चलेगा।",
                 "text": "Bhai, ab tumhare purane laptop par bhi *apna* khud ka AI chalega."},
                {"say": "सिर्फ़ चार जीबी रैम में, और वो भी बिना इंटरनेट के।",
                 "text": "Sirf *4 GB* RAM mein, aur woh bhi bina internet ke."},
            ],
            "visuals": [
                {"kind": "hero", "state": "center"},
                {"kind": "hero", "state": "pulse", "at": {"line": 0, "word": "AI"}},
                {"kind": "hero", "state": "small-top", "at": {"line": 1}},
                {"kind": "counter", "to": 4, "suffix": " GB", "label": "RAM KAAFI HAI",
                 "at": {"line": 1, "word": "4"}},
            ],
        },
        {
            "id": "pain", "kind": "pain", "heading": "Cloud AI ka *bill*", "transition": "slide",
            "lines": [
                {"say": "हर महीने का बिल, हर सवाल उनके सर्वर पर, और प्राइवेसी गई तेल लेने।",
                 "text": "Har mahine ka bill, har sawaal unke server par, aur privacy gayi *tel lene*."},
                {"say": "मेरा क्रेडिट कार्ड अब मुझसे बात नहीं करता।",
                 "text": "Mera credit card ab mujhse baat nahi karta."},
            ],
            "visuals": [
                {"kind": "hero", "state": "shake"},
                {"kind": "stamp", "text": "PRIVACY GONE", "at": {"line": 0, "word": "privacy"}},
                {"kind": "emoji", "emoji": "💸", "at": {"line": 1, "word": "card"}},
            ],
        },
        {
            "id": "reveal", "kind": "reveal", "kicker": "OPEN SOURCE", "heading": "Milo *Pocket LLM* se",
            "transition": "flash",
            "lines": [
                {"say": "मिलो पॉकेट एलएलएम से, एक ओपन सोर्स ऐप जो पूरा मॉडल तुम्हारे लैपटॉप पर चलाता है।",
                 "text": "Milo *Pocket LLM* se, ek open source app jo poora model tumhare laptop par chalata hai."},
                {"say": "विंडोज़, मैक और लिनक्स, तीनों पर।", "text": "Windows, Mac aur Linux, *teeno* par."},
            ],
            "visuals": [
                {"kind": "hero", "state": "hidden"},
                {"kind": "repo", "name": "pocket-llm/pocket-llm",
                 "description": "Run small language models offline on any laptop", "stars": 12400,
                 "license": "MIT", "language": "Rust", "at": {"line": 0, "word": "open"}},
                {"kind": "chips", "items": ["Windows", "Mac", "Linux"], "style": "filled", "at": {"line": 1}},
            ],
        },
        {
            "id": "feature", "kind": "feature", "heading": "Bas *ek command*", "transition": "zoom",
            "lines": [
                {"say": "बस एक कमांड चलाओ, मॉडल डाउनलोड, और चैट शुरू।",
                 "text": "Bas *ek command* chalao, model download, aur chat shuru."},
                {"say": "और हाँ, लैपटॉप का पंखा थोड़ा गाना ज़रूर गाएगा।",
                 "text": "Aur haan, laptop ka pankha thoda *gaana* zaroor gaayega."},
            ],
            "visuals": [
                {"kind": "terminal", "exit": {"line": 1}, "lines": [
                    {"cmd": "pocket pull tiny-3b"},
                    {"out": "downloaded 1.9 GB", "tone": "ok"},
                    {"cmd": "pocket chat"},
                    {"out": "> namaste! main offline hoon"},
                ]},
                {"kind": "window", "title": "Pocket LLM", "tabs": ["Chat", "Models"], "live": True,
                 "button": "Send", "at": {"line": 1},
                 "cursor": [{"to": [0.78, 0.86], "at": {"line": 1, "word": "gaana"}}]},
                {"kind": "hero", "state": "corner", "at": {"line": 1, "word": "pankha"}},
            ],
        },
    ],
}

GUIDE: dict[str, Any] = {
    "summary": (
        "Write a short, funny, fact-checked script in beats; each beat is a few narrated lines and the "
        "visuals that enter on their words. reel-machine narrates, animates, renders and verifies; you do the "
        "research and the writing."
    ),
    "workflow": [
        "research the topic or link yourself: confirm every number from a primary source, put each URL in `sources`",
        "outline the reel as beats: hook, pain, reveal, explain/feature, honest, cta",
        "write each beat's lines (1–4), one joke every 2–3 lines",
        "pick a visual for every beat from the components; cue each on the word it illustrates",
        "probe: fix every lint error",
        "build mode `storyboard` and look at storyboard.jpg",
        "build mode `full`",
    ],
    "language": (
        "`say` is exactly what the voice reads: in the voice's script (Devanagari for Hindi), numbers, symbols "
        "and names with digits written as words (\"चार जीबी\", \"G P T four o\"), no `*` markup; digits or `*` in "
        "`say` are lint errors. `text` is the caption (any script; Hinglish in Latin for Hindi), keeps the "
        "digits, and marks accent words as `*word*`; it defaults to `say`. Word cues match caption words."
    ),
}


def _role(ctx: ProbeContext, role: str) -> Any:
    try:
        return ctx.providers.get(role) if ctx.providers else None
    except KeyError:
        return None


class TechReelRecipe:
    spec = RecipeSpec(
        id="techreel",
        title="Tech & AI explainer reel",
        summary=(
            "Turns a researched AI or tech topic (a new tool, a news item or a concept) into a funny, "
            "fast vertical reel: a conversational voice-over over dense motion graphics (UI mock-ups, counters, "
            "stamps, emoji gags, kinetic headings and word-by-word captions). "
            "`storyboard` renders a sheet of stills, `full` the reel."
        ),
        input_model=TechReelInputs,
        version=1,
        stages=(
            StageSpec(stage=ScriptStage()),
            StageSpec(stage=NarrationStage(), feeds="script", uses=("narration",)),
            StageSpec(stage=ComposeStage(), feeds="narration"),
            StageSpec(stage=PreviewStage(), feeds="compose", uses=("renderer",)),
            StageSpec(stage=RenderStage(), feeds="preview", uses=("renderer",)),
        ),
        providers={
            "narration": ProviderReq(group="narration", default="fake",
                                     description="Text-to-speech with word timings; Supertonic recommended"),
            "renderer": ProviderReq(group="renderers", default="hyperframes",
                                    description="Renders the reel (Node >= 22, HyperFrames, Chrome, ffprobe)"),
        },
        cost=CostNote(unit="tts_characters", estimate="the length of every line's `say`",
                      notes="the fake, macOS and local Supertonic voices are free; cloud voices bill per character",
                      providers=("supertonic", "elevenlabs", "cartesia", "macos", "fake")),
        artifacts=("reel.mp4", "captions.srt", "reel.json", "storyboard.jpg", "script.resolved.json"),
        render_modes=("hyperframes",),
        example=EXAMPLE,
        guide=GUIDE,
    )

    def probe(self, inputs: TechReelInputs, ctx: ProbeContext) -> ProbeReport:
        """Checks the lint, the renderer and the voice, and estimates the length. Writes nothing."""
        checks: list[CheckResult] = []
        details: dict[str, Any] = {}
        bound = getattr(_role(ctx, "narration"), "name", "")
        found = lint_findings(inputs, bound or None)
        details["lint"] = found
        errors = [f"{f['path']}: {f['message']}" for f in found if f["severity"] == "error"]
        blocking = bool(errors) and lint.blocks(inputs)
        checks.append(CheckResult(name="lint", ok=not blocking,
                                  detail="; ".join(errors) if blocking else f"{len(found)} suggestions"))
        plan = estimate(inputs)
        details["estimatedMs"] = plan.duration_ms
        details["beats"] = [{"index": i, "estimatedMs": b.end_ms - b.start_ms} for i, b in enumerate(plan.beats)]
        details["cost"] = {"ttsCharacters": sum(len(line.say) for beat in inputs.beats for line in beat.lines)}
        renderer = _role(ctx, "renderer")
        problems = renderer.missing() if renderer is not None else ["no renderer bound"]
        checks.append(CheckResult(name="renderer", ok=not problems,
                                  detail="; ".join(problems) or getattr(renderer, "name", "")))
        wanted = provider_for(inputs.voice)
        checks.append(CheckResult(name="voice", ok=wanted == bound,
                                  detail=f"{inputs.voice!r} is served by {bound!r}" if wanted == bound
                                  else f"{inputs.voice!r} needs {wanted!r}; this deployment serves {bound!r}"))
        return ProbeReport(ok=all(c.ok for c in checks), checks=checks, details=details)

    def verify(self, ctx: StageContext, result: dict[str, Any]) -> VerificationReport:
        return verify_techreel(ctx, result)
