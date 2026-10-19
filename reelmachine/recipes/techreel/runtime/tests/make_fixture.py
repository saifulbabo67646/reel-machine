"""Dev script: rebuild runtime/tests/fixture.json through the real reel_document path (estimated timings).

    uv run python reelmachine/recipes/techreel/runtime/tests/make_fixture.py reelmachine/recipes/techreel/runtime/tests/fixture.json

The fixture's hero is the orb; to check the laptop and monitor, set doc["hero"]["kind"] before
compose.write_project(doc, dest) and snapshot the same times (every hero state appears in the fixture).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from reelmachine.recipes.techreel.compose import reel_document
from reelmachine.recipes.techreel.markup import units
from reelmachine.recipes.techreel.models import TechReelInputs
from reelmachine.recipes.techreel.schedule import estimate, schedule
from reelmachine.recipes.techreel.words import unit_times

beats = [
  {"id": "hook", "kind": "hook", "heading": "bhai *176B* AI?", "kicker": "OPENAI · 29.09", "transition": "cut",
   "lines": [{"say": "bhai maine laptop mein ek sau chhihattar billion ka AI ghusa diya",
              "text": "bhai maine laptop mein *176 billion* ka AI ghusa diya"},
             {"say": "aur laptop abhi tak zinda hai", "text": "aur laptop abhi tak *zinda* hai"}],
   "visuals": [{"kind": "hero", "state": "center"},
               {"kind": "counter", "to": 176, "suffix": "B", "label": "BILLION PARAMETERS", "at": {"line": 0, "word": "176"}},
               {"kind": "emoji", "emoji": "💀", "at": {"line": 1, "word": "zinda"}}]},
  {"id": "pain", "kind": "pain", "heading": "monthly *kharcha*", "kicker": "PROBLEM?", "transition": "slide",
   "lines": [{"say": "har mahine chatgpt claude gemini ka bill", "text": "har mahine *ChatGPT*, Claude, Gemini ka bill"},
             {"say": "use kar raha hoon ya emi bhar raha hoon", "text": "use kar raha hoon ya *EMI* bhar raha hoon?"}],
   "visuals": [{"kind": "hero", "state": "small-top"},
               {"kind": "chips", "items": ["ChatGPT Plus", "Claude Pro", "Gemini Adv"], "style": "filled", "at": {"line": 0, "word": "chatgpt"}},
               {"kind": "stamp", "text": "EMI", "tone": "red", "rotate": -10, "at": {"line": 1, "word": "emi"}},
               {"kind": "hero", "state": "shake", "at": {"line": 1, "frac": 0.7}}]},
  {"id": "reveal", "kind": "reveal", "heading": "milo *strata*", "kicker": "TOH MERE DOSTON", "transition": "flash",
   "lines": [{"say": "toh mere doston milo strata se", "text": "toh mere doston, milo *Strata* se"},
             {"say": "bees hazaar stars aur license bhi free", "text": "*20 hazaar* stars, aur license bhi *free*"}],
   "visuals": [{"kind": "hero", "state": "corner"},
               {"kind": "repo", "name": "strata-ai/strata", "description": "Run a 176B mixture-of-experts model on any laptop. One-click install.",
                "stars": 19900, "license": "MIT", "language": "Python", "at": {"line": 0, "word": "strata"}},
               {"kind": "stamp", "text": "FREE ✓", "tone": "green", "rotate": 8, "at": {"line": 1, "word": "free"}}]},
  {"id": "explain", "kind": "explain", "heading": "*apna* computer", "kicker": "FEATURE 01/04", "transition": "zoom",
   "lines": [{"say": "har dot ka apna computer files terminal sab", "text": "har dot ka apna *computer*, files, terminal, sab"},
             {"say": "aur zarurat pade toh control tum le lo", "text": "aur zarurat pade toh *control* tum le lo"}],
   "visuals": [{"kind": "hero", "state": "hidden"},
               {"kind": "window", "title": "dot-01 · computer", "tabs": ["Browser", "Files", "Terminal"], "active": 0, "live": True,
                "button": "TAKE CONTROL",
                "cursor": [{"to": [0.7, 0.45], "at": {"line": 0, "word": "files"}}, {"to": [0.5, 0.86], "at": {"line": 1, "word": "control"}}]},
               {"kind": "emoji", "emoji": "🍿", "place": "right", "at": {"line": 1, "frac": 0.5}}]},
  {"id": "api", "kind": "feature", "heading": "OpenAI jaisa *API*", "kicker": "FEATURE 02/04 · LOCALHOST", "transition": "cut",
   "lines": [{"say": "openai jaisa api localhost pe", "text": "OpenAI jaisa *API*, localhost pe"},
             {"say": "matlab tumhare apps samjhenge ki chatgpt hai", "text": "matlab tumhare apps samjhenge ki *ChatGPT* hai"}],
   "visuals": [{"kind": "hero", "state": "pulse"},
               {"kind": "terminal", "at": {"line": 0, "word": "api"}, "lines": [
                   {"cmd": "POST localhost:8080/v1/chat"}, {"out": "→ 200 OK", "tone": "ok"},
                   {"cmd": "GET /v1/models"}, {"out": "error: gpu not found", "tone": "err"}, {"out": "falling back to cpu…", "tone": "dim"}]},
               {"kind": "hero", "state": "rings", "at": {"line": 1}},
               {"kind": "title", "text": "desi *jugaad* 😎", "size": "m", "at": {"line": 1, "word": "chatgpt"}}]},
  {"id": "honest", "kind": "honest", "heading": "*reality* check", "kicker": "HONEST MOMENT", "transition": "slide",
   "lines": [{"say": "सच बोलूँ तो सेटअप थोड़ा मुश्किल है", "text": "सच बोलूँ तो *सेटअप* थोड़ा मुश्किल है"},
             {"say": "sirf chhe billion kaam karte hain baaki so rahe hain", "text": "sirf *6B* kaam karte hain, baaki *so* rahe hain"}],
   "visuals": [{"kind": "hero", "state": "screen", "text": "LOCAL ✓"},
               {"kind": "counter", "to": 6, "suffix": "B", "label": "ACTIVE", "place": "left", "at": {"line": 1, "word": "6b"}},
               {"kind": "counter", "to": 125, "suffix": "B", "label": "TOTAL", "place": "right", "at": {"line": 1, "word": "kaam"}},
               {"kind": "hero", "state": "sleep", "at": {"line": 1, "word": "so"}}]},
]
inputs = TechReelInputs(title="techreel runtime fixture", language="hi", look="mint", hero={"kind": "orb"}, beats=beats)
# the estimate is a slow reader; the references speak about twice as fast
plan = estimate(inputs)
plan = schedule(inputs.beats, [[round(ln.speech_ms * 0.52) for ln in b.lines] for b in plan.beats])
times = [[unit_times([], units(ln.caption, inputs.language), slot.speech_ms) for ln, slot in zip(b.lines, bs.lines)]
         for b, bs in zip(inputs.beats, plan.beats)]
doc = reel_document(inputs, plan, times)
out = Path(sys.argv[1])
out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(doc["duration"], [(b["id"], b["startMs"], b["endMs"]) for b in doc["beats"]])
