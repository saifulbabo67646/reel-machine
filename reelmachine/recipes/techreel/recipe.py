"""The techreel recipe: a funny AI/tech explainer as a vertical reel of narrated motion graphics."""

from __future__ import annotations

from typing import Any

from ...config import get_settings
from ...core.errors import InvalidInput
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
from . import assets, lint
from .models import TechReelInputs
from .schedule import estimate
from .stages import (
    AssetsStage,
    ComposeStage,
    NarrationStage,
    PreviewStage,
    RenderStage,
    ScriptStage,
    bound_provider,
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
                {"say": "भाई, पुराने लैपटॉप पर भी अपना एआई चलेगा।",
                 "text": "Bhai, purane laptop par bhi *apna* AI chalega."},
                {"say": "सिर्फ़ चार जीबी रैम में, बिना इंटरनेट के।",
                 "text": "Sirf *4 GB* RAM mein, bina internet ke."},
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
                {"say": "हर महीने बिल, हर सवाल उनके सर्वर पर।",
                 "text": "Har mahine bill, har sawaal unke server par."},
                {"say": "और प्राइवेसी? वो गई तेल लेने।",
                 "text": "Aur privacy? Woh gayi *tel lene*."},
                {"say": "मेरा क्रेडिट कार्ड अब मुझसे बात नहीं करता।",
                 "text": "Mera credit card ab mujhse baat nahi karta."},
            ],
            "visuals": [
                {"kind": "hero", "state": "shake"},
                {"kind": "receipts", "items": [{"title": "Cloud AI", "sub": "monthly"}]},
                {"kind": "stamp", "text": "PRIVACY GONE", "at": {"line": 1, "word": "privacy"}},
                {"kind": "emoji", "emoji": "💸", "at": {"line": 2, "word": "card"}},
            ],
        },
        {
            "id": "reveal", "kind": "reveal", "kicker": "OPEN SOURCE", "heading": "Milo *Pocket LLM* se",
            "transition": "flash",
            "lines": [
                {"say": "मिलो पॉकेट एल एल एम, एक ओपन ऐप।",
                 "text": "Milo *Pocket LLM* se, open source app."},
                {"say": "पूरा मॉडल तुम्हारे लैपटॉप पर चलता है।",
                 "text": "Poora model tumhare laptop par chalta hai."},
                {"say": "विंडोज़, मैक और लिनक्स, तीनों पर।", "text": "Windows, Mac aur Linux, *teeno* par."},
            ],
            "visuals": [
                {"kind": "hero", "state": "hidden"},
                {"kind": "repo", "name": "pocket-llm/pocket-llm",
                 "description": "Run small language models offline on any laptop", "stars": 12400,
                 "license": "MIT", "language": "Rust", "exit": {"line": 2}},
                {"kind": "chips", "items": ["Windows", "Mac", "Linux"], "style": "filled", "at": {"line": 1}},
                {"kind": "emoji", "emoji": "💻", "at": {"line": 2}},
            ],
        },
        {
            "id": "feature", "kind": "feature", "heading": "Bas *ek command*", "transition": "zoom",
            "lines": [
                {"say": "बस एक कमांड चलाओ, मॉडल डाउनलोड।",
                 "text": "Bas *ek command* chalao, model download."},
                {"say": "फिर चैट शुरू, सीधे अपने लैपटॉप पर।",
                 "text": "Phir chat shuru, seedha apne laptop par."},
                {"say": "और हाँ, लैपटॉप का पंखा गाना ज़रूर गाएगा।",
                 "text": "Aur haan, laptop ka pankha *gaana* zaroor gaayega."},
            ],
            "visuals": [
                {"kind": "terminal", "exit": {"line": 2}, "lines": [
                    {"cmd": "pocket pull tiny-3b"},
                    {"out": "downloaded 1.9 GB", "tone": "ok"},
                    {"cmd": "pocket chat"},
                ]},
                {"kind": "window", "title": "Pocket LLM", "tabs": ["Chat", "Models"], "live": True,
                 "button": "Send", "rows": ["> pankha itna shor kyun?", "Main soch raha hoon. Zor se."],
                 "at": {"line": 1},
                 "cursor": [{"to": [0.78, 0.86], "at": {"line": 2, "word": "gaana"}}]},
                {"kind": "hero", "state": "corner", "at": {"line": 2}},
            ],
        },
        {
            "id": "honest", "kind": "honest", "heading": "Ek *sach* baat", "transition": "cut",
            "lines": [
                {"say": "सच कहूँ, छोटा मॉडल थोड़ा कम समझदार है।",
                 "text": "Sach kahun toh chhota model thoda kam samajhdaar hai."},
                {"say": "पर प्राइवेसी और बिल के लिए, चलता है।",
                 "text": "Par privacy aur bill ke liye, *chalta hai*."},
            ],
            "visuals": [
                {"kind": "bars", "items": [{"label": "Cloud", "value": 9}, {"label": "Pocket", "value": 6}],
                 "highlight": 1},
                {"kind": "stamp", "text": "CHALTA HAI", "tone": "green", "at": {"line": 1}},
            ],
        },
        {
            "id": "cta", "kind": "cta", "transition": "slide", "keyword": "POCKET", "dm": "Link bhej dunga",
            "lines": [
                {"say": "लिंक चाहिए? नीचे पॉकेट लिखो।", "text": "Link chahiye? Neeche *POCKET* likho."},
                {"say": "और फ़ॉलो करना मत भूलना।", "text": "Aur *follow* karna mat bhoolna."},
            ],
            "visuals": [],
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
        "digits, and marks accent words as `*word*`; it defaults to `say`. Word cues match caption words. "
        "Hindi voices do not spell Latin letters: \"AI\" in a Devanagari `say` comes out as a long \"एएएए\", so "
        "write \"एआई\"; spell an acronym as spaced letters (\"एल एल एम\", not \"एलएलएम\", which runs together)."
    ),
}


def _lint_counts(found: list[dict[str, Any]]) -> str:
    """'2 errors (not blocking), 3 warnings': a non-blocking lint's findings by severity."""
    errors = sum(f["severity"] == "error" for f in found)
    others = len(found) - errors
    parts = [f"{errors} error{'s' * (errors != 1)} (not blocking)"] if errors else []
    parts.append(f"{others} warning{'s' * (others != 1)}")
    return ", ".join(parts)


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
            StageSpec(stage=AssetsStage(), feeds="script"),
            StageSpec(stage=NarrationStage(), feeds="assets", uses=("narration",)),
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
        """Checks the lint, the drawings and image sources (fetching nothing), the renderer and the voice, and
        estimates the length. Writes nothing."""
        checks: list[CheckResult] = []
        details: dict[str, Any] = {}
        bound = getattr(bound_provider(ctx, "narration"), "name", "")
        config = ctx.config or get_settings()
        found = lint_findings(inputs, bound or None, allow_local=config.local_files)
        details["lint"] = found
        errors = [f"{f['path']}: {f['message']}" for f in found if f["severity"] == "error"]
        blocking = bool(errors) and lint.blocks(inputs)
        checks.append(CheckResult(name="lint", ok=not blocking,
                                  detail="; ".join(errors) if blocking else _lint_counts(found)))
        try:
            assets.check_svgs(inputs)
            assets.check_sources(inputs, allow_local=config.local_files)
            count = sum(1 for _ in assets.image_sources(inputs))
            checks.append(CheckResult(name="assets", ok=True,
                                      detail=f"{count} image{'s' * (count != 1)} to fetch" if count else "no images"))
        except InvalidInput as exc:
            path = exc.details.get("path", "")
            checks.append(CheckResult(name="assets", ok=False, detail=f"{path}: {exc.message}"))
            details["assets"] = {"path": path, "message": exc.message, "hint": exc.hint}
        plan = estimate(inputs)
        details["estimatedMs"] = plan.duration_ms
        details["beats"] = [{"index": i, "estimatedMs": b.end_ms - b.start_ms} for i, b in enumerate(plan.beats)]
        details["cost"] = {"ttsCharacters": sum(len(line.say) for beat in inputs.beats for line in beat.lines)}
        renderer = bound_provider(ctx, "renderer")
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
