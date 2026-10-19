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
  {"id": "reveal", "kind": "reveal", "heading": "milo *pocket*", "kicker": "TOH MERE DOSTON", "transition": "flash",
   "lines": [{"say": "toh mere doston milo pocket se", "text": "toh mere doston, milo *Pocket* se"},
             {"say": "bees hazaar stars aur license bhi free", "text": "*20 hazaar* stars, aur license bhi *free*"}],
   "visuals": [{"kind": "hero", "state": "corner"},
               {"kind": "repo", "name": "pocket-llm/pocket-llm", "description": "Run a 176B mixture-of-experts model on any laptop. One-click install.",
                "stars": 19900, "license": "MIT", "language": "Python", "at": {"line": 0, "word": "pocket"}},
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
  # the cards (cards.mjs), one beat each so every card can be looked at alone
  {"id": "math", "kind": "explain", "heading": "*total* kitna?", "kicker": "AB SUNO", "transition": "cut",
   "lines": [{"say": "ek sau pachchees billion plus embeddings", "text": "*125B* plus embeddings"},
             {"say": "matlab total ek sau chhihattar billion", "text": "matlab total *176B*"}],
   "visuals": [{"kind": "hero", "state": "hidden"}, {"kind": "equation", "rows": ["125B", "+ embeddings"], "result": "176B"}]},
  {"id": "slack", "kind": "feature", "heading": "slack mein *tag* karo", "kicker": "FEATURE 03/04", "transition": "slide",
   "lines": [{"say": "slack mein dot ko tag karo", "text": "Slack mein *Dot* ko tag karo"},
             {"say": "wahin reply karega bina chai break ke", "text": "wahin *reply* karega, bina chai break ke"}],
   "visuals": [{"kind": "chat", "channel": "launch-team", "messages": [
                   {"from": "Riya", "text": "@Dot iska summary bana do 🙏"},
                   {"from": "Dot", "text": "Done! Three key points + next steps 👇", "bot": True},
                   {"from": "Meera", "text": "bhai ye intern kab so ta hai?"}]}]},
  {"id": "ask", "kind": "feature", "heading": "pehle *poochega*", "kicker": "PERMISSION", "transition": "cut",
   "lines": [{"say": "koi bhi kaam karne se pehle", "text": "koi bhi kaam karne se pehle"},
             {"say": "approve button aata hai", "text": "*approve* button aata hai"}],
   "visuals": [{"kind": "dialog", "title": "Send an email to the client 📧", "body": "Dot wants to use your Gmail to send one draft.",
                "approve": "Approve", "decline": "Decline", "click": "approve", "click_at": {"line": 1, "word": "approve"}}]},
  {"id": "bills", "kind": "pain", "heading": "monthly *kharcha*", "kicker": "PROBLEM?", "transition": "zoom",
   "lines": [{"say": "chatgpt plus claude pro gemini advanced", "text": "ChatGPT Plus, Claude Pro, Gemini Advanced"},
             {"say": "subscription hai ya emi", "text": "subscription hai ya *EMI*?"}],
   "visuals": [{"kind": "receipts", "items": [{"title": "ChatGPT Plus", "sub": "monthly · due again"},
                                              {"title": "Claude Pro", "sub": "monthly · due again"},
                                              {"title": "Gemini Advanced", "sub": "monthly · due again"}]}]},
  {"id": "todo", "kind": "feature", "heading": "baat bhi *kaam* bhi", "kicker": "WHILE YOU TALK", "transition": "cut",
   "lines": [{"say": "wo baat bhi karta rahega", "text": "wo baat bhi karta rahega"},
             {"say": "aur kaam bhi aur main multitasking mein", "text": "aur *kaam* bhi, aur main multitasking mein"}],
   "visuals": [{"kind": "checklist", "items": ["Searching the web", "Reading twelve sources", "Writing the draft"], "state": "progress"},
               {"kind": "error", "title": "Me.exe", "body": "multitasking.exe has stopped working", "at": {"line": 1, "word": "multitasking"}}]},
  {"id": "setup", "kind": "honest", "heading": "setup *done*", "kicker": "INSTALL", "transition": "cut",
   "lines": [{"say": "install kiya model laga diya", "text": "install kiya, model laga diya"},
             {"say": "aur chal bhi gaya", "text": "aur *chal* bhi gaya"}],
   "visuals": [{"kind": "checklist", "items": ["Clone the repo", "Add your API key", "Run the app"]}]},
  {"id": "fight", "kind": "other", "heading": "*permission* kaun maange", "kicker": "SCORECARD", "transition": "flash",
   "lines": [{"say": "mere roommate se zyada permission", "text": "mere roommate se zyada *permission*"},
             {"say": "ye dot maangta hai", "text": "ye *Dot* maangta hai"}],
   "visuals": [{"kind": "versus", "left": {"label": "Roommate", "value": "0", "emoji": "😒"},
                "right": {"label": "Dot", "value": "1", "emoji": "🫧"}}]},
  {"id": "bench", "kind": "honest", "heading": "GPU *chahiye*", "kicker": "REALITY CHECK", "transition": "slide",
   "lines": [{"say": "achhi speed ke liye nvidia gpu chahiye", "text": "achhi speed ke liye *NVIDIA* GPU chahiye"},
             {"say": "mera laptop toh museum mein hai", "text": "mera laptop toh *museum* mein hai"}],
   "visuals": [{"kind": "bars", "unit": "tok/s", "highlight": 0, "items": [
                   {"label": "RTX 4090", "value": 142}, {"label": "M3 Max", "value": 61.5}, {"label": "My laptop", "value": 3}]}]},
  # the social and analogy visuals (social.mjs)
  {"id": "office", "kind": "explain", "heading": "mixture of *experts*", "kicker": "TWIST 🤓", "transition": "cut",
   "lines": [{"say": "matlab office jaisa company mein ek sau pachchees log", "text": "matlab *office* jaisa, company mein 125 log"},
             {"say": "kaam sirf chhe karte hain baaki meeting mein", "text": "kaam sirf *6* karte hain, baaki *meeting* mein"}],
   "visuals": [{"kind": "people", "total": 125, "lit": 6, "label": "OFFICE MEIN 125 LOG", "badge": "MEETING 💤"}]},
  {"id": "team", "kind": "explain", "heading": "teen *dots*, ek team", "kicker": "HOW IT WORKS", "transition": "slide",
   "lines": [{"say": "researcher writer aur reviewer", "text": "researcher, writer aur *reviewer*"},
             {"say": "teeno aapas mein baat karte hain", "text": "teeno aapas mein *baat* karte hain"}],
   "visuals": [{"kind": "nodes", "layout": "triangle", "items": [
                   {"label": "Researcher", "icon": "🔎"}, {"label": "Writer", "icon": "✍️"}, {"label": "Reviewer", "icon": "🧐"}]}]},
  {"id": "pipe", "kind": "explain", "heading": "*RAG* kaise chalta hai", "kicker": "PIPELINE", "transition": "cut",
   "lines": [{"say": "sawaal aaya documents dhoondhe", "text": "sawaal aaya, *documents* dhoondhe"},
             {"say": "phir model ne jawab likha", "text": "phir model ne *jawab* likha"}],
   "visuals": [{"kind": "nodes", "layout": "flow", "items": [
                   {"label": "Question", "icon": "❓"}, {"label": "Search", "icon": "📚"}, {"label": "Model"}, {"label": "Answer", "icon": "💬"}]},
               {"kind": "nodes", "layout": "ring", "at": {"line": 1}, "items": [
                   {"label": "Plan"}, {"label": "Act", "icon": "⚡"}, {"label": "Check"}, {"label": "Learn", "icon": "🧠"}, {"label": "Repeat"}]}]},
  {"id": "alive", "kind": "honest", "heading": "laptop *zinda* hai", "kicker": "STATUS", "transition": "zoom",
   "lines": [{"say": "aur sabse badi baat", "text": "aur sabse badi baat"},
             {"say": "laptop abhi tak zinda hai", "text": "laptop abhi tak *zinda* hai"}],
   "visuals": [{"kind": "heartbeat", "label": "LAPTOP STATUS: ZINDA", "at": {"line": 0, "frac": 0.3}}]},
  {"id": "free", "kind": "reveal", "heading": "MIT *license*", "kicker": "FEATURE 04/04", "transition": "flash",
   "lines": [{"say": "mit license", "text": "MIT license,"}, {"say": "yaani free", "text": "yaani *free*"}],
   "visuals": [{"kind": "flash", "text": "yaani *free*", "at": {"line": 1}}]},
  {"id": "cta", "kind": "cta", "heading": "ai ki emi se *pareshaan*?", "kicker": "", "transition": "cut",
   "lines": [{"say": "toh comment karo pocket", "text": "toh comment karo *POCKET*"},
             {"say": "link dm kar dunga aur follow kar lo", "text": "link DM kar dunga, aur *follow* kar lo"}],
   "visuals": [{"kind": "comment", "keyword": "POCKET", "reply": "link DM kar dunga", "at": {"line": 0, "word": "comment"}},
               {"kind": "follow", "at": {"line": 1, "word": "follow"}}]},
]
inputs = TechReelInputs(title="techreel runtime fixture", language="hi", look="mint", hero={"kind": "orb"}, beats=beats,
                        creator={"name": "Your Name", "handle": "@yourhandle"})
# the estimate is a slow reader; the references speak about twice as fast
plan = estimate(inputs)
plan = schedule(inputs.beats, [[round(ln.speech_ms * 0.52) for ln in b.lines] for b in plan.beats])
times = [[unit_times([], units(ln.caption, inputs.language), slot.speech_ms) for ln, slot in zip(b.lines, bs.lines)]
         for b, bs in zip(inputs.beats, plan.beats)]
doc = reel_document(inputs, plan, times)
out = Path(sys.argv[1])
out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(doc["duration"], [(b["id"], b["startMs"], b["endMs"]) for b in doc["beats"]])
