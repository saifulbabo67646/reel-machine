"""The dialoguereel guide: what describe_recipe hands an agent so it can write a good script and its own characters."""

from __future__ import annotations

from typing import Any

from .library import builtin_names
from .svg import MAX_BYTES, OPTIONAL, REQUIRED, VIEW_BOX

# A complete, minimal character in the house style: a round mochi. It passes check_svg (a test says so).
EXAMPLE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400">
  <g id="arm-left" data-pivot="104,270">
    <ellipse cx="78" cy="286" rx="26" ry="15" transform="rotate(-30 78 286)" fill="#fff" stroke="#fff" stroke-width="28"/>
    <ellipse cx="78" cy="286" rx="26" ry="15" transform="rotate(-30 78 286)" style="fill:var(--secondary,#ffe1ec)" stroke="#3a2a3a" stroke-width="5.5"/>
  </g>
  <g id="arm-right" data-pivot="296,270">
    <ellipse cx="322" cy="286" rx="26" ry="15" transform="rotate(30 322 286)" fill="#fff" stroke="#fff" stroke-width="28"/>
    <ellipse cx="322" cy="286" rx="26" ry="15" transform="rotate(30 322 286)" style="fill:var(--secondary,#ffe1ec)" stroke="#3a2a3a" stroke-width="5.5"/>
  </g>
  <g id="body">
    <path d="M96 330 C70 250 110 120 200 120 C290 120 330 250 304 330 Q200 368 96 330 Z" fill="#fff" stroke="#fff" stroke-width="34" stroke-linejoin="round"/>
    <path d="M96 330 C70 250 110 120 200 120 C290 120 330 250 304 330 Q200 368 96 330 Z" style="fill:var(--primary,#fff4f7)" stroke="#3a2a3a" stroke-width="5.5" stroke-linejoin="round"/>
    <ellipse cx="200" cy="330" rx="96" ry="14" fill="#3a2a3a" opacity="0.08"/>
    <ellipse cx="150" cy="168" rx="18" ry="9" transform="rotate(-35 150 168)" fill="#fff" opacity="0.8"/>
    <circle cx="200" cy="112" r="14" style="fill:var(--accent,#9fe2c8)" stroke="#3a2a3a" stroke-width="5"/>
  </g>
  <g id="blush">
    <ellipse cx="136" cy="258" rx="17" ry="9" fill="#ff5f95" opacity="0.45"/>
    <ellipse cx="264" cy="258" rx="17" ry="9" fill="#ff5f95" opacity="0.45"/>
  </g>
  <g id="eyes-open">
    <circle cx="164" cy="234" r="12" fill="#3a2a3a"/><circle cx="236" cy="234" r="12" fill="#3a2a3a"/>
    <circle cx="160" cy="229" r="4" fill="#fff"/><circle cx="232" cy="229" r="4" fill="#fff"/>
  </g>
  <g id="eyes-closed" display="none" fill="none" stroke="#3a2a3a" stroke-width="5" stroke-linecap="round">
    <path d="M152 233 Q164 243 176 233"/><path d="M224 233 Q236 243 248 233"/>
  </g>
  <g id="eyes-happy" display="none" fill="none" stroke="#3a2a3a" stroke-width="5" stroke-linecap="round">
    <path d="M152 239 Q164 223 176 239"/><path d="M224 239 Q236 223 248 239"/>
  </g>
  <g id="mouth-closed" fill="none" stroke="#3a2a3a" stroke-width="5" stroke-linecap="round">
    <path d="M190 252 Q200 261 210 252"/>
  </g>
  <g id="mouth-open" display="none" stroke="#3a2a3a" stroke-width="4.5" stroke-linejoin="round">
    <path d="M187 250 Q200 247 213 250 Q212 274 200 274 Q188 274 187 250 Z" fill="#8a2f45"/>
  </g>
  <g id="mouth-smile" display="none" stroke="#3a2a3a" stroke-width="4.5" stroke-linejoin="round">
    <path d="M182 249 Q200 252 218 249 Q216 270 200 270 Q184 270 182 249 Z" fill="#8a2f45"/>
  </g>
</svg>"""

GUIDE: dict[str, Any] = {
    "workflow": [
        "1. Pick the learner's level (N5, TOPIK 1, A1, ...; see `levels`) and one everyday situation (see "
        "`situations`): it becomes the title (see `hooks`), and `scene` takes one emoji for it.",
        "2. make_cast with two characters: built-ins by name (see `builtins`) or your own SVG (see `characters`). "
        "It is free and instant and returns a castId you can reuse across reels.",
        "3. Look at the preview: get_job(previewJobId), then get_artifact(previewJobId, \"cast.png\"). "
        "Fix a character that looks wrong before writing lines for it.",
        "4. Write 5 to 9 short lines (3 to 12 are allowed; see `lines` and `writing`), each spoken by one of the two cast ids, with "
        "`cast` set to the castId. Mark 2 to 6 `keys` across the reel, an `emotion` on a beat or two, and `slow` "
        "on the hardest line.",
        "5. Choose the ending (see `endings`): `quiz` true (the default) asks about the line with the most keys; "
        "`flow: \"listen_first\"` plays the dialogue once without subtitles before the subtitled pass.",
        "6. probe the inputs: it checks the script, the lint, the voices and the renderer and spends nothing. "
        "Fix every `key-missing` and `level` finding; the other warnings are advice.",
        "7. start_job in `storyboard` mode (the default) and look at storyboard.jpg: one still per line.",
        "8. start_job again in `full` mode for reel.mp4, captions.srt and reel.json.",
    ],
    "builtins": builtin_names(),
    "characters": {
        "contract": [
            f"One <svg> root with viewBox=\"{VIEW_BOX}\"; the character stands in it facing the viewer, "
            "filling most of it: feet near y=330-380, top of the head near y=60-120.",
            f"At most {MAX_BYTES:,} bytes ({MAX_BYTES // 1000} kB).",
            f"Required groups (ids): {', '.join(REQUIRED)}. The runtime shows and hides them: eyes-closed for blinks, "
            "mouth-open while the character speaks (it flaps with the voice), mouth-closed otherwise.",
            f"Optional groups: {', '.join(OPTIONAL)}. arm-left/arm-right wave while speaking; give each a "
            "data-pivot=\"x,y\" (the shoulder, in viewBox units) to rotate around. mouth-smile and eyes-happy are "
            "used for cheerful beats.",
            "Every alternate group (eyes-closed, mouth-open, mouth-smile, eyes-happy, eyes-surprised, sweat, "
            "sparkle) starts hidden with display=\"none\"; eyes-open and mouth-closed start shown.",
            "Each id is used once. Ids are prefixed per character when inlined, so both characters may use the same ids.",
        ],
        "refused": [
            "Anything starting <! or <?: comments, CDATA, <!DOCTYPE>/<!ENTITY> declarations, processing "
            "instructions and the <?xml ...?> line. Strip editor comments before passing the SVG.",
            "Two attributes on one element whose names differ only in case (id and ID), or href with xlink:href.",
            "Child elements inside <title> or <desc> (text only).",
            "The elements script, foreignObject, iframe, object, embed, audio, video, animate, animateTransform, "
            "animateMotion, set, and <style>: use inline style=\"...\" attributes instead (a <style> element would "
            "restyle the whole page). Motion belongs to the runtime, so no SVG or CSS animation.",
            "Any element that is not plain SVG (no HTML, no editor namespaces such as inkscape: or sodipodi:).",
            "Any on* event attribute (onload, onclick, ...).",
            "href / xlink:href that does not start with # - except <image> with data:image/png;base64, or "
            "data:image/jpeg;base64,.",
            "In any attribute or style: @import, @font-face, javascript:, url(...) that is not url(#id), "
            "image()/image-set(), and CSS backslash escapes.",
        ],
        "house_style": [
            "Chunky, cute and flat: a round body, big dark eyes with a white highlight, a small mouth, rosy blush.",
            "Outline everything in #3a2a3a at stroke-width 5-5.5 with round joins.",
            "A white sticker border: draw the silhouette once underneath in #fff with a wide (about 30) white stroke.",
            "Colour with CSS variables and a fallback, style=\"fill:var(--primary,#ffb3d1)\" (also --secondary, "
            "--accent), so a cast can recolour the character.",
            "A soft shadow or highlight at low opacity (#3a2a3a at 0.1, #fff at 0.75) is enough shading; no gradients needed.",
            "Keep the face around the middle of the viewBox (eyes near y=200-240) and arms clear of the body edge.",
        ],
        "example": EXAMPLE_SVG,
        "use": 'Pass it as {"kind": "svg", "svg": "<svg ...>"} in a character\'s "source".',
    },
    "lines": {
        "shape": "Each line is {speaker, text, reading, meaning, keys, emotion, slow}: speaker is a cast id, text is "
                 "what is said in the target language, meaning is its translation; keys, emotion and slow are "
                 "optional (see `writing` and `emotions`).",
        "keys": "Up to 4 per line, each {text, reading, meaning}: text is the word in dictionary form (あらう for "
                "あらって, だす for だして) and may carry ruby (手伝{てつだ}う); it is found in the line by its plain "
                "text or its stem, then underlined and shown as a chip, and every key returns on the recap card. "
                "reading is romanised like the line's (arau); meaning is short (to wash).",
        "ruby": {
            "rule": "Furigana: put the reading in braces right after a kanji run; it attaches to that run only.",
            "example": "手伝{てつだ}いましょうか。",
        },
        "reading": "For a non-Latin script, give `reading` as the romanised line (Tetsudai mashou ka.) so a beginner "
                   "can follow; leave it out for Latin scripts.",
        "length": "Keep every line short: one breath, at most about 15 characters of Japanese or 8 words. "
                  "3 to 12 lines; a reel of 6-8 lines lasts about 20-30 seconds. probe's `length` warning aims for 15-45 s "
                  "and does not count the listen_first pass.",
        "tips": [
            "Alternate speakers so the conversation moves; a question then its answer teaches best.",
            "Use the level's grammar and vocabulary only; repeat a useful phrase rather than adding new ones.",
            "`meaning` is natural English (or meaning_language), not a word-by-word gloss.",
        ],
    },
    "situations": [
        {"level": "N5", "situation": "Offering to help at home (〜ましょうか, 〜てください)", "example_title": "Helping Mom at home"},
        {"level": "N5", "situation": "Ordering at a café (〜をください, 〜にします)", "example_title": "Ordering coffee in Japanese"},
        {"level": "N5", "situation": "Buying a ticket at the station (〜まで, いくらですか)", "example_title": "Your first train ticket"},
        {"level": "N5", "situation": "Asking where the toilet is (〜はどこですか, あります)", "example_title": "Where is the toilet?"},
        {"level": "N5", "situation": "Introducing yourself to a classmate (〜です, 〜から来ました)", "example_title": "Your first day at school"},
        {"level": "N5", "situation": "Paying at a convenience store (〜はいりますか, 〜でいいです)", "example_title": "At the konbini till"},
        {"level": "N5", "situation": "Asking the price at a market (これはいくらですか)", "example_title": "How much is this?"},
        {"level": "N5", "situation": "Talking about breakfast (〜を食べました, 〜ましたか)", "example_title": "What did you eat today?"},
        {"level": "N5", "situation": "Making weekend plans with a friend (〜ませんか, 〜ましょう)", "example_title": "What are you doing Sunday?"},
        {"level": "N5", "situation": "Asking the time and the day (なんじ, なんようび)", "example_title": "What time is it now?"},
        {"level": "N5", "situation": "Talking about the weather (〜いです, 〜くないです)", "example_title": "Is it cold today?"},
        {"level": "N5", "situation": "Choosing a drink at a vending machine (〜がいいです, 〜たい)", "example_title": "Hot or cold?"},
        {"level": "N5", "situation": "Asking a teacher to repeat (もういちど〜てください, 〜てもいいですか)", "example_title": "One more time, please"},
        {"level": "N5", "situation": "Saying where you want to go on holiday (〜たいです, 〜へ行きます)", "example_title": "Where do you want to go?"},
        {"level": "N4", "situation": "Calling in sick to a part-time job (〜んです, 〜ので, potential form)", "example_title": "I can't come in today"},
        {"level": "N4", "situation": "Asking a stranger for directions (〜たら, 〜と, 〜てみる)", "example_title": "Lost in Tokyo"},
        {"level": "N4", "situation": "Returning an item at a shop (potential form: 返品できますか)", "example_title": "Can I return this?"},
        {"level": "N4", "situation": "Inviting a friend to a movie in casual speech (見に行かない？, volitional 行こう)", "example_title": "Want to see a movie?"},
        {"level": "N4", "situation": "At the doctor's: saying what hurts (〜んです, 〜そう)", "example_title": "My head hurts"},
        {"level": "N4", "situation": "Borrowing something from a coworker (〜てもらえますか, 〜てくれる)", "example_title": "Can I borrow your pen?"},
        {"level": "N4", "situation": "Apologising for being late (〜てしまう, plain past)", "example_title": "Sorry I'm late!"},
        {"level": "N4", "situation": "Giving advice to a friend who is tired (〜たほうがいい, 〜ようにする)", "example_title": "You should rest"},
        {"level": "N4", "situation": "Telling a friend what happened yesterday (plain past, 〜たり〜たり)", "example_title": "Guess what happened"},
        {"level": "N4", "situation": "Checking in at a hotel (basic set keigo: 〜でございます, お〜ください)", "example_title": "Checking in, the polite way"},
        {"level": "N3", "situation": "Turning down an invitation politely (〜ちゃって, 〜わけにはいかない)", "example_title": "How to say no nicely"},
        {"level": "N3", "situation": "Complaining about noise to a neighbour (passive: 〜られて困る)", "example_title": "My neighbour is too loud"},
        {"level": "N3", "situation": "Explaining why you study Japanese (〜ように purpose, 〜ために)", "example_title": "Why I study Japanese"},
        {"level": "N3", "situation": "A job interview: talking about strengths (keigo, 〜ようにしています)", "example_title": "Your first Japanese interview"},
        {"level": "N3", "situation": "Making a reservation by phone (〜たいのですが, 〜はずだ)", "example_title": "Booking a table by phone"},
        {"level": "N3", "situation": "Telling a friend about rumours you heard (〜らしい, 〜って)", "example_title": "Did you hear?"},
        {"level": "N3", "situation": "Asking a senior colleague for a favour (〜ていただけませんか, causative 〜させて)", "example_title": "Asking your senpai for help"},
        {"level": "N3", "situation": "Talking about a change of plans (〜ことになった, 〜ばかり)", "example_title": "The plan changed"},
    ],
    "writing": [
        "Write what two people would really say, not textbook sentences: short turns, fillers a learner should know "
        "(えっと, あ, そうか), and lines that answer each other.",
        "Choose polite (です/ます) or casual (plain form) on purpose and keep each speaker consistent: strangers, "
        "staff and seniors speak politely; family and friends speak casually. A kid offering help politely to a "
        "mom who answers casually is a lesson in itself.",
        "Teach one new grammar point per reel (〜ましょうか, 〜てください, 〜たい); everything else stays at or "
        "below the level, and the new point appears at least twice if it can.",
        "Mark 2 to 6 `keys` across the reel: the words worth saving, at the reel's level or easier. Put the most "
        "useful line's keys on one line so the quiz asks about it.",
        "Set `slow` on the hardest line (at N5 nearly always; at N3 only for a long or fast one): it is replayed at "
        "0.75x right after it is spoken. One or two slow lines per reel, never every line.",
        "Use `flow: \"listen_first\"` when the dialogue is short and easy enough to guess by ear (N5-N4, 5-7 lines): "
        "the viewer hears it once without subtitles (\"how much did you catch?\"), then sees it subtitled. It "
        "doubles the dialogue's time, so skip it for long reels and harder levels.",
        "Give a line an `emotion` (see `emotions`) where the feeling changes: a happy offer, a surprised reply, a "
        "thinking question. Two or three per reel; neutral is the default.",
        "End on a resolution (お願い, ありがとう, I'll do it now) so the conversation feels finished before the quiz.",
    ],
    "hooks": {
        "rule": "The title is the hook shown in the first 1.5 s: name the situation or the learner's goal, at most "
                "about 40 characters, in the meaning language.",
        "formulas": [
            "How to <do the thing> in <language>: How to order coffee in Japanese",
            "Your first <situation>: Your first train ticket",
            "<A line from the reel, translated>?: Can I borrow your pen?",
            "<Level> <situation>: N5 shopping in 30 seconds",
            "Don't say X, say Y: Don't say すみません, say ありがとう",
            "Can you understand this? (with listen_first): Can you understand this kitchen chat?",
        ],
    },
    "endings": {
        "quiz": "`quiz: true` (the default) asks \"How do you say:\" with the meaning of the line with the most keys "
                "(ties go to the earliest; with no keys, the longest meaning), counts down 3 s, then speaks the "
                "answer. Pass a 0-based line index to choose the line, or false for no quiz. An index outside the "
                "lines is the `quiz-line` error.",
        "cta": "After the quiz the reel shows \"Comment your answer 👇\" and \"Follow for daily <level>\" in the "
               "meaning language (en, ja, ko, es, zh; others fall back to en). Set `level` so the follow line names it.",
        "recap": "With at least one key the reel shows the recap card (\"Words from this video\") before the quiz.",
    },
    "emotions": {
        "neutral": "the default: the character's normal face",
        "happy": "eyes-happy and mouth-smile when not talking, ❤️",
        "surprised": "eyes-surprised, ❗",
        "sad": "sweat, 💦",
        "thinking": "the head tilts 8°, ❓",
        "note": "A character without the optional part falls back to its neutral part; the emoji still shows.",
    },
    "levels": {
        "JLPT": "Japanese: N5 (です/ます, 〜たい, 〜てください, 〜てもいい, 〜ましょう(か), あります/います, basic "
                "particles) → N4 (plain and casual past, 〜そう, 〜ようにする, 〜たほうがいい, potential form, "
                "〜てしまう) → N3 (passive and causative, 〜ように for purpose, 〜ばかり, 〜はずだ, 〜らしい) → N2 → N1. "
                "Write `level` as \"N5\".",
        "TOPIK": "Korean: TOPIK 1-2 (beginner, 해요체) → 3-4 (intermediate, connectives) → 5-6 (advanced). "
                 "Write `level` as \"TOPIK 1\".",
        "CEFR": "Other languages: A1 (fixed phrases, present tense) → A2 (past, everyday tasks) → B1 (opinions, "
                "plans) → B2 → C1. Write `level` as \"A1\".",
        "check": "probe checks each key's JLPT level only when `language` is Japanese and `level` is N1-N5: a key "
                 "harder than the reel is a `level` warning. Other languages and ladders are not checked, so "
                 "choose their words with care.",
    },
    "voices": {
        "rule": "A deployment serves one narration provider; every character's voice must name it "
                "(fake:..., macos:..., elevenlabs:..., cartesia:...). probe's `voice` check says which one is bound.",
        "advice": "Give the two characters different voice ids of that provider, so they sound like two people "
                  "(for example macos:Kyoko and macos:Reed (Japanese (Japan)) for Japanese; `say -v '?'` lists "
                  "the macOS voices). A voice that cannot speak the reel's language mumbles or skips it.",
        "recasting": "Voices are not part of the castId (only how the characters look and are named): calling "
                     "make_cast again with the same characters but other voices keeps the same castId and replaces "
                     "the saved voices, so every later reel that passes that castId speaks with the new voices.",
    },
}
