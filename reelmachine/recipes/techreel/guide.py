"""The techreel guide: what describe_recipe hands an agent so it can research and write a funny, fast, well-drawn reel.

The component reference is built at import from `models.Visual`, so a field added to a visual shows up here and a
visual with no example here fails at import.
"""

from __future__ import annotations

import json
from typing import Any, get_args

from pydantic import TypeAdapter

from . import models

# ---------------------------------------------------------------- components (generated from models.Visual)

# what each kind shows and one example of it; every kind of models.Visual has exactly one entry (checked below)
_KINDS: dict[str, tuple[str, dict[str, Any]]] = {
    "hero": ("moves or changes the reel's hero (see `heroes`); never takes stage space of its own",
             {"kind": "hero", "state": "split", "count": 3, "labels": ["Search", "Read", "Answer"],
              "at": {"line": 1, "word": "three"}}),
    "title": ("extra kinetic text, slammed in word by word; `*accent*` words in the accent colour",
              {"kind": "title", "text": "an *open-book* exam", "size": "l"}),
    "counter": ("a number rolling from `from` to `to`, big, in the accent colour; every number on screen is one",
                {"kind": "counter", "to": 14, "suffix": " GB", "label": "A 7B MODEL AT 16-BIT",
                 "at": {"line": 0, "word": "fourteen"}}),
    "stamp": ("a rotated rubber stamp slamming onto the newest block: the punchline",
              {"kind": "stamp", "text": "WRONG", "tone": "red", "at": {"line": 1, "word": "confidently"}}),
    "emoji": ("an emoji popping onto the newest block's corner with a wobble: the reaction",
              {"kind": "emoji", "emoji": "💀", "at": {"line": 2, "frac": 0.6}}),
    "chips": ("a row of pills appearing one by one (platforms, features, tags)",
              {"kind": "chips", "items": ["Windows", "Mac", "Linux"], "style": "filled"}),
    "window": ("an app or browser mock-up: tabs, a LIVE badge, chat or list rows, a moving cursor, a button",
               {"kind": "window", "title": "Notes app", "tabs": ["Chat", "Files"], "live": True,
                "rows": ["> where is my invoice?", "Found it: invoices/march.pdf"], "button": "Send",
                "cursor": [{"to": [0.8, 0.86], "at": {"line": 1, "word": "send"}}]}),
    "terminal": ("commands typed character by character; each output appears when its command is typed",
                 {"kind": "terminal", "lines": [{"cmd": "tiny pull chat-3b"}, {"out": "done in 41 s", "tone": "ok"},
                                                {"cmd": "tiny chat"}]}),
    "repo": ("a code-hosting repository card: owner/name, description, a rolling star count, licence, language",
             {"kind": "repo", "name": "someone/tiny-chat", "description": "Run small models offline",
              "stars": 12400, "license": "MIT", "language": "Rust"}),
    "equation": ("a stacked sum: rows, a rule, then the result ticking in",
                 {"kind": "equation", "rows": ["14 GB at 16-bit", "÷ 4"], "result": "3.5 GB"}),
    "chat": ("a group-chat thread, messages appearing one by one; `bot` marks the AI",
             {"kind": "chat", "channel": "support", "messages": [
                 {"from": "You", "text": "What's the refund window?"},
                 {"from": "Bot", "text": "30 days. Source: refunds.pdf", "bot": True}]}),
    "dialog": ("a permission dialog; with `click` the cursor presses a button (at `click_at`)",
               {"kind": "dialog", "title": "Save this model?", "body": "chat-3b-q4 · 1.9 GB", "approve": "Save",
                "decline": "Cancel", "click": "approve", "click_at": {"line": 1, "word": "save"}}),
    "error": ("a fake OS error box that shakes in over the stage: the mock-dramatic gag",
              {"kind": "error", "title": "Laptop.exe", "body": "A 70B model? I'm going to need a minute. Or a year.",
               "button": "Wait"}),
    "receipts": ("paper receipts stacking up, slightly rotated: bills, subscriptions, costs",
                 {"kind": "receipts", "items": [{"title": "Chatbot Plus", "sub": "monthly"},
                                                {"title": "Image AI Pro", "sub": "monthly · again"}]}),
    "checklist": ("items ticking in sequence (`progress` leaves the last one spinning)",
                  {"kind": "checklist", "items": ["No retraining", "Fresh documents", "Answers with sources"]}),
    "people": ("a grid of `total` person icons, `lit` of them light up: analogies of the few among many",
               {"kind": "people", "total": 100, "lit": 3, "label": "YOUR DOCUMENTS", "badge": "NOT RELEVANT 💤"}),
    "versus": ("two score cards with a VS; the bigger value wins the highlight",
               {"kind": "versus", "left": {"label": "Cloud bill", "value": "₹₹₹", "emoji": "💸"},
                "right": {"label": "Local bill", "value": "₹0", "emoji": "😌"}}),
    "nodes": ("labelled bubbles joined by lines: a pipeline (`flow`), a team (`triangle`), a loop (`ring`), a row",
              {"kind": "nodes", "layout": "flow", "items": [{"label": "Question", "icon": "❓"},
                                                            {"label": "Search", "icon": "🔎"},
                                                            {"label": "Answer", "icon": "💬"}]}),
    "bars": ("a few horizontal bars growing: benchmarks, sizes, prices; `highlight` is an item index",
             {"kind": "bars", "unit": "GB", "highlight": 2, "items": [{"label": "16-bit", "value": 14},
                                                                     {"label": "8-bit", "value": 7},
                                                                     {"label": "4-bit", "value": 3.5}]}),
    "flash": ("a full-bleed accent wipe with big text: the reveal or the big punchline",
              {"kind": "flash", "text": "bill: *zero*", "exit": {"line": 1}}),
    "heartbeat": ("an ECG line drawing across with a label: alive, running, overheating",
                  {"kind": "heartbeat", "label": "LAPTOP STATUS: ALIVE"}),
    "comment": ("a comment box typing the keyword, a send arrow, then the reply chip (the CTA)",
                {"kind": "comment", "keyword": "RAG", "reply": "cheat sheet on its way"}),
    "follow": ("the creator card (from `creator`): avatar, name, handle, Follow flipping to Following",
               {"kind": "follow", "at": {"line": 1, "word": "follow"}}),
    "image": (("a caller image (https URL; a path on local runs only) in a rounded frame with a slow drift; "
              "`highlight` boxes a region as [x, y, w, h] fractions"),
              {"kind": "image", "src": "https://example.com/screenshot.png", "highlight": [0.1, 0.2, 0.5, 0.3]}),
    "svg": ("your own SVG drawing for anything the catalogue lacks (see `drawings`)",
            {"kind": "svg", "size": "m",
             "svg": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
                    "<circle cx='50' cy='50' r='40' fill='none' stroke='currentColor' stroke-width='6'/></svg>"}),
}


def _members() -> tuple[type, ...]:
    union = get_args(models.Visual)[0]
    return get_args(union)


def _kind_of(cls: type) -> str:
    return get_args(cls.model_fields["kind"].annotation)[0]


def _shape(prop: dict[str, Any], defs: dict[str, Any]) -> str:
    """A JSON-schema property as a short type: `accent|red|green`, `[{title, sub?}] 1-5`, `text ≤ 40`."""
    if "$ref" in prop:
        d = defs[prop["$ref"].rsplit("/", 1)[-1]]
        need = set(d.get("required", []))
        return "{" + ", ".join(k if k in need else f"{k}?" for k in d["properties"]) + "}"
    if "anyOf" in prop:
        alts = [_shape(p, defs) for p in prop["anyOf"] if p.get("type") != "null"]
        return " or ".join(alts)
    if "const" in prop:
        return repr(prop["const"]) if prop.get("type") == "string" else str(prop["const"])
    if "enum" in prop:
        return "|".join(str(e) for e in prop["enum"])
    kind = prop.get("type")
    if kind == "array":
        if "prefixItems" in prop:
            return "[" + ", ".join(_shape(p, defs) for p in prop["prefixItems"]) + "]"
        out = "[" + _shape(prop["items"], defs) + "]"
        lo, hi = prop.get("minItems"), prop.get("maxItems")
        return out + (f" {lo or 0}-{hi}" if hi is not None else f" ≥ {lo}" if lo else "")
    if kind == "string":
        return f"text ≤ {prop['maxLength']}" if "maxLength" in prop else "text"
    if kind in ("integer", "number"):
        name = "int" if kind == "integer" else "number"
        lo, hi = prop.get("minimum"), prop.get("maximum")
        return name + (f" {lo}-{hi}" if lo is not None and hi is not None else f" ≥ {lo}" if lo is not None else "")
    return kind or "any"


def _component(cls: type) -> dict[str, Any]:
    schema = TypeAdapter(cls).json_schema(by_alias=True)
    defs = schema.get("$defs", {})
    need = set(schema.get("required", []))
    base_place = TypeAdapter(models.VisualBase).json_schema()["properties"]["place"]
    required: dict[str, str] = {}
    optional: dict[str, str] = {}
    for name, prop in schema["properties"].items():
        if name in ("kind", "at", "exit") or (name == "place" and prop == base_place):
            continue                                    # the fields every visual shares: see `visual_fields`
        shape = _shape(prop, defs)
        if name in need:
            required[name] = shape
        else:
            default = prop.get("default")
            optional[name] = shape + (f" = {json.dumps(default, ensure_ascii=False)}" if default not in (None, [], "") else "")
    what, example = _KINDS[_kind_of(cls)]
    return {"what": what, "required": required, "optional": optional, "example": example}


def _components() -> dict[str, dict[str, Any]]:
    kinds = {_kind_of(cls): cls for cls in _members()}
    if set(kinds) != set(_KINDS):
        raise RuntimeError(f"techreel guide: components out of step with models.Visual: "
                           f"{sorted(set(kinds) ^ set(_KINDS))}")
    adapter = TypeAdapter(models.Visual)
    for _, example in _KINDS.values():
        adapter.validate_python(example)                # an example that stops validating fails the import
    return {kind: _component(cls) for kind, cls in kinds.items()}


COMPONENTS = _components()

# ---------------------------------------------------------------- the guide

GUIDE: dict[str, Any] = {
    "summary": (
        "Write a short, funny, fact-checked script in beats; each beat is a few narrated lines and the "
        "visuals that enter on their words. reel-machine narrates, animates, renders and verifies; you do the "
        "research and the writing."
    ),
    "workflow": [
        "1. Research: read the link or topic yourself (see `research`). Note every number with its source URL.",
        ("2. Outline the reel in the formula (see `formula`): hook, pain, reveal, explain/features, honest, cta. "
        "One idea per beat; 6 to 10 beats; 45 to 90 seconds."),
        ("3. Write each beat's lines (1 to 4 short lines, about 6 to 9 words each): `say` for the voice, `text` "
        "for the caption when they differ (see `language`). One joke every 2 to 3 lines (see `humour`)."),
        ("4. Pick the visuals (see `visuals` and `components`): every analogy gets a literal picture, every number "
        "a `counter`, every punchline a `stamp` or `emoji`; cue each on the word it illustrates."),
        ("5. probe: fix every lint error; treat the warnings as a reviewer's notes (pacing, crowding, structure). "
        "It also estimates the length per beat."),
        ("6. start_job in mode `storyboard` and look at storyboard.jpg (stills at each beat's middle and last cue). "
        "Fix what reads badly: a crowded stage, a stamp over the wrong thing, a caption that says too much."),
        "7. start_job in mode `full` for reel.mp4, captions.srt and reel.json.",
    ],
    "research": [
        "Read the given link first: the README, the release notes, the paper's abstract. Then the official site.",
        ("Confirm every number (stars, sizes, prices, benchmarks, dates) from a primary source on the day you write; "
        "star counts and prices change weekly."),
        ("Never invent a number, a quote, a date, a feature or a benchmark. A joke may exaggerate only what is "
        "obviously a joke (\"refunds within nine hundred years\"), never a fact the viewer would believe."),
        "When a fact is uncertain, say less: \"a few gigabytes\" beats a wrong \"3.2 GB\".",
        "Put every source URL in `sources` (the lint warns without them); they go into reel.json.",
        "For news, put the date in the hook when it matters (\"released last Tuesday\").",
        "A concept reel needs sources too: the paper that named the idea, the official docs.",
    ],
    "formula": [
        ("hook (≤ 3 s, kind `hook`): one shock line: a number, a bold claim, a confident lie. The first line must "
        "land before a thumb moves (the lint warns over 3.5 s) and the hook opens on a visual."),
        ("pain (6–12 s, kind `pain`): the relatable problem: the bills, the paywall, the laptop that dies on three "
        "tabs, the chatbot that makes things up."),
        ("reveal (5–8 s, kind `reveal`): \"so meet ...\": the name and the trust signals (stars, licence, platforms). "
        "A `flash` transition suits it."),
        ("explain (15–30 s, kind `explain`): what it is or how it works, one idea per beat, each with one "
        "everyday analogy drawn literally."),
        "features (15–30 s, kind `feature`): numbered (kicker \"FEATURE 01/03\"), each with a gag.",
        ("honest (6–10 s, kind `honest`): the catch: setup, hardware, quality, cost. Trust is what makes people "
        "follow (the lint warns when there is no honest beat)."),
        ("cta (5–8 s, kind `cta`, last): comment keyword → link by DM → follow → the next reel's tease. A callback "
        "to an earlier joke lands well here."),
        ("A beat is 1–4 lines and at most about 9 s (lint `beat-long`); something new must enter at least every "
        "3.5 s (lint `static`)."),
    ],
    "humour": {
        "rules": [
            "One joke every 2 to 3 lines; the information never stops for it.",
            ("Analogies must be funny AND technically right: a quantised model really is a vacuum-packed suitcase "
            "(same clothes, less air, a few wrinkles); a wrong analogy teaches the wrong thing."),
            ("Devices: self-roast (the narrator's laptop, wallet, gym progress), everyday life (rent, roommates, "
            "commutes, chai, exams), deadpan understatement, a mock-dramatic error box, a callback near the end."),
            ("Safe humour: jokes punch at the situation or at the narrator, never at groups of people, real "
            "people, competitors or users. No politics, religion, bodies or tragedies. Keep it brand-safe."),
            "Put the punchline on the line's last words and cue its `stamp` or `emoji` on them.",
            "Plan a callback: set a joke up in the pain beat and pay it off in the honest or cta beat.",
        ],
        "examples": [
            "en: \"Then its knowledge freezes. Like my gym progress.\" (self-roast, the model's training cutoff)",
            "en: \"And it can cite its sources.\" / \"Unlike me in every argument.\" (self-roast)",
            ("en: \"Messy documents in, messy answers out.\" / \"So clean that folder called final final two.\" "
             "(self-roast, and the honest catch)"),
            "en: \"Your chatbot? Still confident. Now also right.\" (callback to the hook's confident lie)",
            ("hi: \"मेरा क्रेडिट कार्ड अब मुझसे बात नहीं करता।\" / \"Mera credit card ab mujhse baat nahi karta.\" "
            "(self-roast over a pile of receipts)"),
            "hi: \"और प्राइवेसी? वो गई तेल लेने।\" / \"Aur privacy? Woh gayi *tel lene*.\" (deadpan, with a stamp)",
            ("hi: \"और हाँ, लैपटॉप का पंखा गाना ज़रूर गाएगा।\" / \"Aur haan, laptop ka pankha *gaana* zaroor gaayega.\" "
             "(the honest catch as a joke)"),
            ("hi: \"मेरे दोस्तों से ज़्यादा राज़ यही रखता है।\" / \"Mere doston se zyada *raaz* yahi rakhta hai.\" "
            "(everyday life, privacy)"),
            "ja: \"僕のスマホ、写真でもう満杯なのに。\" (self-roast: the phone is already full of photos)",
            "ja: \"旅行の圧縮袋みたいなもの。\" / \"ちょっとシワになるけどね。\" (an everyday analogy that is technically right)",
            "ja: \"冬はカイロ代わりに、どうぞ。\" (deadpan: a hot phone as a hand warmer)",
            "ja: \"これなら、写真を消さなくても入る。\" (callback to the full phone)",
        ],
    },
    "visuals": {
        "rules": [
            "One idea per beat, one main block for it; add a stamp or emoji on the punchline.",
            ("Keep a stamp to one or two short words (about 12 characters): a longer one runs off the frame. A "
            "stamp covers part of the block under it, so do not stamp over the number the viewer must read."),
            "Pair every analogy with a literal visual (the table below); prefer a component to plain text.",
            "Every number on screen is a `counter` (or `bars`/`versus`/`equation` when it is a comparison).",
            "Cue visuals on the word that names them (`at: {line, word}`); the hook's first visual at line 0.",
            ("At most 4 visuals live at once (lint `crowded`): give earlier ones an `exit` cue. The stack scales down "
            "when it is too tall, so two big blocks plus an overlay is the comfortable most."),
            "Use the hero at least every 3 beats: it is the reel's recurring character (see `heroes`).",
            "`place: left|right` puts two consecutive blocks side by side (two counters, two cards).",
            ("Overlays (`stamp`, `emoji`, `error`, `flash`) take no stage space: a stamp lands across the newest "
            "block, an emoji at its corner, an error over the stage, a flash over everything."),
            ("Agent drawings (`svg`) for what the catalogue lacks: a suitcase, a bookshelf, a GPU fan. Keep them "
            "simple line art in the look's colours (see `drawings`)."),
        ],
        "analogies": {
            "most people idle, the few that work (mixture of experts, retrieval)": "people",
            "a pipeline, steps, an architecture": "nodes (layout flow)",
            "a team of agents or roles": "nodes (layout triangle) or hero split with labels",
            "a loop (plan, act, check)": "nodes (layout ring)",
            "asking permission, a confirmation": "dialog (with click)",
            "a crash, an overload, a model too big": "error",
            "bills, subscriptions": "receipts",
            "a benchmark, sizes, speeds": "bars",
            "A vs B (cloud vs local, before vs after)": "versus",
            "a big number": "counter",
            "how a number is made": "equation",
            "installing, running a command": "terminal",
            "the app itself": "window (rows show the chat), or image with a real screenshot",
            "a chatbot conversation, a team thread": "chat",
            "trust signals (stars, licence)": "repo + chips",
            "features done, setup steps": "checklist",
            "platforms, tags, features in a row": "chips",
            "alive, running, hot": "heartbeat",
            "private, locked away": "hero locked",
            "frozen knowledge, idle": "hero sleep",
            "the reveal, the big punchline": "flash",
        },
    },
    "language": {
        "say_and_text": (
            "`say` is exactly what the voice reads: in the voice's script (Devanagari for Hindi), numbers, symbols "
            "and names with digits written as words (\"चार जीबी\", \"G P T four o\"), no `*` markup; digits or `*` in "
            "`say` are lint errors. `text` is the caption (any script; Hinglish in Latin for Hindi), keeps the "
            "digits, and marks accent words as `*word*`; it defaults to `say`. Word cues match caption words. "
            "Hindi voices do not spell Latin letters: \"AI\" in a Devanagari `say` comes out as a long \"एएएए\", so "
            "write \"एआई\"; spell an acronym as spaced letters (\"एल एल एम\", not \"एलएलएम\", which runs together)."
        ),
        "english": "`text` is usually omitted (it defaults to `say`); give it only when the caption should show "
                   "digits or accents: say \"fourteen gigabytes\", text \"*14 GB*\".",
        "hindi": "`say` in Devanagari, English tech words transliterated (\"लैपटॉप\", \"सर्वर\", \"एआई\"); `text` in "
                 "Hinglish Latin script like the reels people watch (\"Sirf *4 GB* RAM mein\"). Keep the two the same "
                 "length roughly (the lint warns outside 0.4–2.5×). Hindi numbers 11–99 are irregular words: look "
                 "each one up (19 उन्नीस, 25 पच्चीस, 99 निन्यानवे).",
        "japanese": "`say` in natural Japanese with numbers in kana (\"よんビット\", \"じゅうよんギガ\") so the voice "
                    "cannot misread them, and Latin acronyms in katakana (\"エーアイ\"). `text` only when the caption "
                    "should show digits (\"*4ビット*\"). Captions chunk by characters (14 per chunk), so keep lines "
                    "short with 、 and 。 at natural breaks. A cue `word` in Japanese is one character (its first "
                    "occurrence in the line): prefer `frac` (0–1 through the line) for Japanese cues.",
        "numbers": {
            "en": ["0 zero, 1 one, 2 two, 3 three, 4 four, 5 five, 6 six, 7 seven, 8 eight, 9 nine, 10 ten",
                   "100 one hundred, 250 two hundred fifty, 1,000 one thousand, 12,400 twelve thousand four hundred",
                   "7B seven billion (or \"seven B\"), 12K stars twelve thousand stars, 3.5 GB three point five gigabytes",
                   ("4-bit four bit, 10x ten times, 30% thirty percent, $20/month twenty dollars a month, 2025 twenty "
                   "twenty-five")],
            "hi": ["0 शून्य, 1 एक, 2 दो, 3 तीन, 4 चार, 5 पाँच, 6 छह, 7 सात, 8 आठ, 9 नौ, 10 दस",
                   ("100 सौ (एक सौ), 250 दो सौ पचास, 1,000 एक हज़ार, 12,400 बारह हज़ार चार सौ, 1,00,000 एक लाख, "
                   "1,00,00,000 एक करोड़"),
                   "7B सात बिलियन, 12K स्टार्स बारह हज़ार स्टार्स, 4 GB चार जीबी, 3.5 GB साढ़े तीन जीबी",
                   "4-bit चार बिट, 10x दस गुना, 30% तीस प्रतिशत, ₹500 पाँच सौ रुपये, $20 बीस डॉलर"],
            "ja": ["0 ゼロ, 1 いち, 2 に, 3 さん, 4 よん, 5 ご, 6 ろく, 7 なな, 8 はち, 9 きゅう, 10 じゅう",
                   ("100 ひゃく, 300 さんびゃく, 600 ろっぴゃく, 800 はっぴゃく, 1,000 せん, 3,000 さんぜん, "
                   "8,000 はっせん, 10,000 いちまん, 12,400 いちまんにせんよんひゃく"),
                   ("7B ななビー (or ななじゅうおく), 1B じゅうおく, 12K いちまんにせん, 4 GB よんギガ, "
                   "3.5 GB さんてんごギガ"),
                   "4-bit よんビット, 16-bit じゅうろくビット, 10x じゅうばい, 30% さんじゅっパーセント"],
        },
        "names": [
            "GPT-4o: en \"G P T four o\"; hi \"जीपीटी फ़ोर ओ\"; ja \"ジーピーティー フォーオー\"",
            "H100: en \"H one hundred\"; hi \"एच वन हंड्रेड\"; ja \"エイチひゃく\"",
            "Llama 3.1: en \"Llama three point one\"; hi \"लामा थ्री पॉइंट वन\"; ja \"ラマ さんてんいち\"",
            "RTX 4090: en \"R T X forty ninety\"; hi \"आर टी एक्स फ़ोर्टी नाइंटी\"; ja \"アールティーエックス よんまるきゅうまる\"",
            ("AI / LLM / GPU / API: hi \"एआई\", \"एल एल एम\", \"जी पी यू\", \"ए पी आई\"; ja \"エーアイ\", "
            "\"エルエルエム\", \"ジーピーユー\", \"エーピーアイ\""),
            ("a name with no digits stays as it is in English `say` (\"GitHub\", \"Linux\"); in Hindi `say` write it "
            "in Devanagari (\"गिटहब\", \"लिनक्स\")"),
        ],
        "voices": (
            "One narrator for the reel: `voice`. It must name the deployment's narration provider (probe's `voice` "
            "check says which is bound). Supertonic, the recommended provider (REEL_TTS=supertonic), has ten voices "
            "speaking en, hi, ja and more: supertonic:F1 to supertonic:F5 (female) and supertonic:M1 to "
            "supertonic:M5 (male). The examples use fake:default so they run anywhere; swap in a Supertonic voice "
            "for a real reel."
        ),
    },
    "components": COMPONENTS,
    "visual_fields": (
        "Every visual also takes `at` (a cue: {line, word?, frac?}; default the beat's start), `exit` (a cue, or "
        "\"beat\" to stay to the beat's end, the default) and `place` (stack|left|right; `emoji` takes "
        "auto|left|right). A cue's `word` is a word of that line's caption (`text`, else `say`), matched without "
        "case or punctuation; `frac` is a fraction of the line's speech."
    ),
    "heroes": {
        "kinds": [
            "orb: a chrome liquid sphere (an AI, an idea, the product); splits into labelled dots",
            "laptop / monitor / phone: line-art devices with a screen that shows `text` in the `screen` state",
            "chip: a GPU card with spinning fans (hardware, compute)",
            "robot: a friendly bot head (an assistant, an agent, a model); splits like the orb",
            "svg: your own drawing as the hero (`hero.svg`, see `drawings`)",
            "image: a caller image as the hero (`hero.src`)",
        ],
        "states": [
            "center: big in the middle of the stage",
            "small-top: small at the top of the stage (it goes there by itself when blocks are live)",
            "corner: small in the corner, out of the way",
            "hidden: gone",
            "pulse: breathes (talking, thinking)",
            "shake: a short shake (pain, overload)",
            "rings: call rings around it (calling, searching, listening)",
            "screen: laptop/monitor/phone show the state's `text` on screen",
            "sleep: dims with a floating z z z (idle, frozen, outdated)",
            "locked: a frosted glass box drops over it with a padlock (private, paywalled)",
            "split: orb/robot split into `count` dots (2–6) with `labels` (a team, parts of a system)",
        ],
        "how": "Set the reel's `hero` ({kind, text?, svg?, src?}); change its state with "
               "{\"kind\": \"hero\", \"state\": ..., \"at\": cue} in any beat's visuals. It persists across beats and "
               "moves smoothly. Hero visuals do not count toward the 4-visual limit.",
    },
    "drawings": (
        "An `svg` visual or hero is your own SVG: at most 60 KB, a viewBox, no script, foreignObject, event "
        "attributes or external references; embedded images as data: PNG/JPEG only. Use simple line art "
        "(stroke-width 4–8 at a 200-unit viewBox) in currentColor and var(--accent) so it follows the look; "
        "ids are scoped per drawing for you."
    ),
    "looks": "`look`: mint (teal), ember (orange), violet, mono; `accent` (#rrggbb) overrides the accent colour. One "
             "look for the whole reel.",
    "cta": {
        "default": (
            "A `cta` beat with no visuals of its own (hero moves aside) gets the standard ending: the comment box "
            "typing `keyword` (default: the title's first word) at line 0 with the `dm` reply chip, the creator's "
            "follow card at line 1 (needs `creator`), and `next` as a title on the last line. Give the beat 2–3 "
            "lines. Author `comment`/`follow` visuals yourself to place them differently."
        ),
        "patterns": {
            "en": ["\"Comment RAG and I'll send you the cheat sheet.\" / \"Follow for more AI, minus the jargon.\"",
                   "\"Want the link? Comment TINY.\" / \"Follow, part two drops this week.\""],
            "hi": ["\"लिंक चाहिए? नीचे पॉकेट लिखो।\" / \"Link chahiye? Neeche *POCKET* likho.\"",
                   "\"कमेंट करो डॉट्स, लिंक डीएम कर दूँगा।\" / \"Comment karo *DOTS*, link DM kar dunga.\"",
                   "\"और फ़ॉलो कर लो, अगला वीडियो और भी मज़ेदार है।\" / \"Aur *follow* kar lo, agla video aur bhi mazedaar hai.\""],
            "ja": ["\"もっと知りたい人は、コメントにタイニーって書いてね。\" / text \"…コメントに*TINY*って書いてね。\"",
                   "\"フォローしておくと、次の動画も見逃さないよ。\""],
        },
        "creator": "Use the caller's own name and handle in `creator`; the examples use the placeholders "
                   "\"Your Name\" / \"@yourhandle\".",
    },
    "examples": {
        "hi-pocket-llm.json": "Hindi new-tool reel (the recipe's `example`): a fictional open-source app; "
                              "Devanagari `say`, Hinglish `text`; the orb hero locked and split; repo, chips, "
                              "terminal, window, receipts, versus, error, flash; the default cta.",
        "en-what-is-rag.json": "English concept reel \"What is RAG?\": analogies drawn literally (open-book exam, "
                               "a library photo, a pipeline, the office where only three people know); the robot hero "
                               "asleep and searching; chat, nodes, people, equation, checklist, svg, image; an "
                               "authored comment and follow with a callback to the hook.",
        "ja-tiny-models.json": "Japanese concept reel on small, quantised models: kana numbers in `say`, digits "
                               "in `text`, `frac` cues; the phone hero's screen; counter, equation, bars, dialog, "
                               "heartbeat, a drawn compression bag (svg); the default cta with a `next` tease.",
    },
}
