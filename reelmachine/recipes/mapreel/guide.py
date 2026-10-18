"""How to write a map reel that holds a viewer: what an agent reads before it writes a mapreel script."""

from __future__ import annotations

from typing import Any

GUIDE: dict[str, Any] = {
    "workflow": [
        "1. Topic: pick an angle people argue about in the comments (see `topics`): brands, dishes, inventions, "
        "bans, records.",
        "2. Research: find every fact you will say on a page you can cite (Wikipedia is fine) and keep its URL "
        "for `source`. Drop what you cannot confirm.",
        "3. Hook: write segment 1 first (see `hooks`). Open on a number, a challenge, a surprise or 'you', never "
        "'X has…'. If you tease 'number one', end on a rank 1 segment that pays it off.",
        "4. Segments: plan the shots (see `kinds`). Put a rank, versus or highlight segment every few segments "
        "and a re-hook line (see `rehooks`) at least every 12 s. End on the payoff, then a call to comment "
        "(see `endings`).",
        "5. Places: resolve countries with find_places (codes like 'ITA'). For a regions segment, list the "
        "country's states or prefectures with find_places(query, limit, country='USA'); an empty query lists "
        "every region (raise `limit`, up to 50). Name each by its ISO 3166-2 code ('US-TX', 'JP-13') or name.",
        "6. Images: search with find_images(query, kind='logo'|'photo'|'flag'|'any') and put a result's `id` "
        "('commons:File:<name>') in the item's `image` (see `images`). An https URL or a local file works too; "
        "an `emoji` is the light option. Anything unusable shows as a bold text badge.",
        "7. probe and fix every failed check and every `lint` finding in details.lint (see `lint`).",
        "8. start_job with mode 'storyboard', look at storyboard.jpg, then start_job with mode 'build'. Paste "
        "sources.md (every claim's source, every image's licence, the map and voice credits) into the video "
        "description.",
    ],
    "kinds": [
        "country: `country` and 1-8 `items` (name, plus say/emoji/image/source/at). The camera flies in, the "
        "country fills, each item pops when the voice names it.",
        "highlight: `countries` (1-60) and an optional `legend`. Several countries fill at once: a hook, a "
        "reveal or the closing shot.",
        "rank: `rank` (1-99) and `country`, an optional `stat` {value, label, source} that counts up, and up "
        "to 8 `items`. A countdown beat with a numbered badge; rank 1 pays off a 'number one' tease.",
        "versus: `countries` (2-3, all different) and one `stats` entry per country {value, label, source}. "
        "Split screen, one panel per country; say each value and country in the narration.",
        "regions: `country` and 1-12 `regions` {region, say, items (up to 6), colour}. A country's states or "
        "prefectures fill one by one as the voice names them (`say` or the name), each with its own items.",
    ],
    "looks": [
        "satellite (the default): NASA Blue Marble imagery; vivid fills with a white glow. The most cinematic.",
        "flat: a clean atlas, pale sea and cream land with grey borders; fills nearly opaque, a dark ghost name. "
        "Reads best for dense or data-heavy reels.",
        "dark: a night map, navy land on near-black sea; fills glow neon in their own colour. Good for "
        "dramatic or 'shocking' topics.",
        "Set `look` on the script; every segment uses it. flat and dark need no satellite download.",
        "Every look's map credits go in sources.md: Natural Earth for the borders, and NASA Blue Marble for "
        "the satellite imagery when the look is satellite.",
    ],
    "images": [
        "find_images(query, kind, limit, include_nonfree) searches Wikimedia Commons. kind 'logo' adds 'logo' to "
        "the query and puts SVG and PNG first, 'flag' adds 'flag', 'photo' drops SVGs. Each result has an `id` "
        "('commons:File:<name>'), a title, a thumb_url to look at, its licence, attribution and `permitted`.",
        "Put the `id` exactly as given in the item's `image`: 'commons:File:Nike_logo.svg'. The build checks "
        "its licence, fetches it (an SVG as a 1024 px PNG) and caches it for later reels.",
        "Licence note: only free licences (public domain, CC0, CC BY, CC BY-SA and the like) are returned unless "
        "you pass include_nonfree; a result with `permitted: false` is never drawn and shows as a badge. Logos "
        "and brand names may be trademarked even when the file is free: check `restrictions`, and leave a logo "
        "out when unsure.",
        "Attribution is automatic: every image's licence, author and link are written to sources.md, the "
        "file each reel ships for the video description. An https URL or local file is credited there as "
        "caller-supplied: you answer for its rights.",
        "No image found? Use an `emoji`, or nothing: the item shows as a bold text badge in its country's colour.",
    ],
    "hooks": [
        "Curiosity gap: hold back the answer. 'Number five might shock you.' / 'Only one country eats this "
        "for breakfast.'",
        "Challenge: dare the viewer. 'You won't guess number one.' / 'Bet you can't name number one.'",
        "Shock stat: open on a number. '750,000 chocolate-sprinkle sandwiches a day, in one country.'",
        "'You' framing: put the viewer in the shot. 'You'd never eat number one on this list.'",
        "Contradiction: say the opposite of what they expect. 'You think <brand> is American. It's <country>.'",
    ],
    "rehooks": [
        "But wait, it gets weirder.",
        "Now guess which country is next.",
        "Here's the one nobody expects.",
        "Believe it or not…",
        "Plot twist:",
        "And it's not even close.",
        "Number three is where it gets strange.",
        "Meanwhile, next door…",
        "Actually, that's the normal one.",
        "Even they can't beat this one.",
    ],
    "endings": [
        "Pay off the loop, then ask: 'And number one? Italy. … Which one would you try? Tell me in the comments.'",
        "Ask what was missed: 'Which country did I miss? Comment it below.'",
        "Pick a side: 'Team Korea or team Japan? Comment your pick.'",
        "Make it personal: 'What's the strangest thing your country eats? I'll read every comment.'",
    ],
    "topics": [
        "Brands you didn't know were from these countries",
        "What each country is weirdly famous for",
        "Countries that banned something normal",
        "Fast food chains that failed in one country",
        "The most expensive city to buy a Big Mac",
        "Foods other countries find disgusting",
        "What each country eats for breakfast",
        "The national dish of every country you know",
        "Countries that drink the most coffee",
        "Where the world's chocolate really comes from",
        "Inventions you didn't know came from these countries",
        "Car brands and the countries that own them now",
        "Tech giants and the country they started in",
        "Countries with the most Michelin stars",
        "Countries where tipping is rude",
        "The weirdest laws in each country",
        "Countries that drive on the left",
        "Countries with no army",
        "The most visited country in the world",
        "Where the most Nobel prizes came from",
        "Countries with more sheep than people",
        "The world's biggest beer drinkers",
        "Islands that belong to a country on the other side of the world",
        "Countries that changed their name",
        "States that are bigger than whole countries",
        "What each US state is famous for eating",
        "Every Japanese prefecture's signature food",
        "Countries where the same snack tastes different",
        "Sports each country is unbeatable at",
        "Countries with the most UNESCO World Heritage Sites",
        "Toys and games that came from these countries",
        "Countries that eat the most instant noodles",
    ],
    "lint": [
        "weak-opener (an error in a strict build): the first seven words have no hook (a number, a question, "
        "'you', a superlative, 'only') or open 'Welcome…', 'Today…', or a plain 'X has…' list ('Which country has…?' "
        "and 'Only one country has…' are fine). Fix: rewrite the opening with "
        "a formula from `hooks`.",
        "unpaid-loop (an error in a strict build): the opening teases ('number one', 'guess', 'surprise', 'at "
        "the end') and nothing pays it off. Fix: end on a rank 1 segment, or say 'and number one is…' or "
        "'finally' in a later line. A tease of 'number five' is paid by a rank 5 segment or a later "
        "'number five'.",
        "beat-gap: more than 1.2 s of narration with nothing new on screen. Fix: name an item sooner, add an "
        "item, or split the line into two segments.",
        "no-rehook: more than 12 s without a rank, versus or highlight segment or a re-hook line. Fix: add one "
        "of those shots or a line from `rehooks`.",
        "monotony: 4 or more country segments in a row. Fix: put a rank, versus or highlight between them.",
        "length: the script reads well short of `length` (padding), or runs past 75 s. Fix: set `length` to "
        "the suggested value, write more, or cut.",
        "unsourced: an item, stat or rank with no `source` (on itself or its segment). Fix: add the URL you "
        "checked the fact against.",
        "no-ending: the last line asks nothing of the viewer and is not a rank 1 payoff. Fix: end with a "
        "question or a call to comment (see `endings`).",
        "weak-opener and unpaid-loop make a build fail while `lint` is \"strict\" (the default); a storyboard "
        "only warns. Set the input lint: \"warn\" to build anyway with them as warnings. Every other rule is "
        "always a warning.",
    ],
    "tips": [
        "Items pop when the voice says their name: write the name in the narration exactly, or give `say` "
        "for how it is pronounced and `at` (seconds) to force a time.",
        "Keep a new visual every second: 3-5 items per country segment, segments of 3-5 seconds.",
        "`image` takes a 'commons:File:<name>' id from find_images, an https URL or a local file; anything "
        "unusable shows as a bold text badge.",
        "Every stat needs a source: give each item, stat and rank a `source` URL to the page that states it.",
        "A region's tag on the map is its `say` or its name shortened ('Ōsaka Prefecture' shows 'Ōsaka'); "
        "keep `say` short.",
        "beat-gap is a suggestion: natural spoken sentences beat filler cards. Add an item only when it is new "
        "information; a few beat-gap warnings are fine.",
        "A stat's first number counts up on screen ('750,000', '$5.69'); say the value in the narration so it "
        "lands with the voice.",
    ],
}
