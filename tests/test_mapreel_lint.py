"""The hook lint: eight rules, two of them hard in a build."""

import json
from pathlib import Path

import pytest

from reelmachine.recipes.mapreel import kinds, lint
from reelmachine.recipes.mapreel.models import MapReelInputs
from reelmachine.recipes.mapreel.stages import estimated_windows

BRANDS = Path(__file__).parent.parent / "examples" / "mapreel" / "brands.json"
HOOK = "Only *three countries* dress the world. Number one will surprise you."
PAYOFF = "And number one is Turkey. Which brand did I miss?"


def country(code="ITA", narration="Italy has Gucci, Prada and Versace.", names=("Gucci", "Prada", "Versace"),
            **extra):
    return {"kind": "country", "country": code, "narration": narration, "source": "https://example.com",
            "items": [{"name": n} for n in names], **extra}


def highlight(narration, **extra):
    return {"kind": "highlight", "countries": ["USA", "ITA"], "narration": narration,
            "source": "https://example.com", **extra}


def rank(n, narration="But Turkey is number one with the most.", **extra):
    return {"kind": "rank", "rank": n, "country": "TUR", "narration": narration,
            "source": "https://example.com", **extra}


def run(segments, **extra):
    inputs = MapReelInputs.model_validate({"segments": segments, **extra})
    return lint.check(inputs, estimated_windows(inputs))


def rules(findings):
    return {f.rule for f in findings}


# -- 1 weak-opener -------------------------------------------------------------------


def test_weak_opener_flags_a_welcome():
    found = run([highlight("Welcome to a map of the brands you know.")], mode="storyboard")
    assert "weak-opener" in rules(found)


def test_weak_opener_flags_a_flat_start_with_no_signal():
    found = run([highlight("Italy has a famous long boot shaped coast line.")])
    assert "weak-opener" in rules(found)


def test_a_hook_with_a_number_passes_the_opener():
    assert "weak-opener" not in rules(run([highlight("Only 3 countries dress the whole world.")]))


@pytest.mark.parametrize("opener", [
    "Which country has the most McDonald's per person?",
    "Guess which country has the weirdest breakfast.",
    "Only one country has a flag that is not a rectangle.",
    "You have never heard of these five brands.",
    "Most people have no idea where Nike is from.",
    "How many countries have a coastline?",
])
def test_a_strong_hook_with_has_or_have_passes_the_opener(opener):
    assert "weak-opener" not in rules(run([highlight(opener)]))


def test_a_plain_list_statement_is_still_a_weak_opener():
    found = run([highlight("The United States has Nike, Apple, Coca-Cola and Ford.")])
    assert "weak-opener" in rules(found)


def test_weak_opener_is_an_error_only_in_a_strict_build():
    weak = [highlight("Welcome to a map of the brands you know.")]
    err = [f for f in run(weak) if f.rule == "weak-opener"][0]
    assert err.severity == "error" and err.segment == "seg-1" and err.hint
    assert [f for f in run(weak, mode="storyboard") if f.rule == "weak-opener"][0].severity == "warning"
    assert [f for f in run(weak, lint="warn") if f.rule == "weak-opener"][0].severity == "warning"


# -- 2 unpaid-loop -------------------------------------------------------------------


def test_unpaid_loop_flags_a_tease_never_paid():
    found = run([highlight(HOOK), country()])
    assert [f for f in found if f.rule == "unpaid-loop"][0].severity == "error"


def test_unpaid_loop_is_paid_by_a_rank_one():
    assert "unpaid-loop" not in rules(run([highlight(HOOK), country(), rank(1)]))


def test_unpaid_loop_accepts_a_spoken_reveal():
    assert "unpaid-loop" not in rules(run([highlight(HOOK), country(), highlight(PAYOFF)]))


def test_a_tease_of_number_n_is_paid_by_saying_number_n_later():
    found = run([highlight("Number five will surprise you."), country(), highlight("And number five? Sweden.")])
    assert "unpaid-loop" not in rules(found)


@pytest.mark.parametrize("later", ["And number 5 is Sweden.", "Number five is Sweden."])
def test_a_tease_of_number_n_accepts_the_digit_or_the_word(later):
    assert "unpaid-loop" not in rules(run([highlight("Wait for number 5."), country(), highlight(later)]))


def test_no_one_is_not_a_tease_of_number_one():
    assert "unpaid-loop" not in rules(run([highlight("No one knows where 3 brands come from."), country()]))


def test_a_tease_of_number_n_is_paid_by_a_rank_n_segment():
    found = run([highlight("Number five will surprise you."), country(), rank(5, "Sweden makes it.")])
    assert "unpaid-loop" not in rules(found)


def test_a_tease_of_number_n_is_not_paid_by_number_one_and_its_hint_names_n():
    found = run([highlight("Number five will surprise you."), country(), highlight("And number one is Turkey.")])
    finding = [f for f in found if f.rule == "unpaid-loop"]
    assert finding
    assert "number five" in finding[0].hint and "rank 5" in finding[0].hint
    assert "number one" not in finding[0].hint and "rank 1 " not in finding[0].hint


# -- 3 beat-gap ----------------------------------------------------------------------


def test_beat_gap_flags_a_long_stretch_with_nothing_on_screen():
    long = ("Italy is a long country with a very long coast and many many many many many many many many many "
            "many many many many many words before Gucci.")
    found = run([highlight("Only 3 countries dress the world."), country(narration=long, names=["Gucci"])])
    assert [f.segment for f in found if f.rule == "beat-gap"] == ["seg-2"]


def test_beat_gap_passes_when_names_land_every_second():
    found = run([highlight("Only 3 countries dress the world."), country()])
    assert "beat-gap" not in rules(found)


# -- 4 no-rehook ---------------------------------------------------------------------


def test_no_rehook_flags_a_long_flat_stretch():
    flat = country(narration="Italy has Gucci, Prada and Versace, and a very long list of other famous "
                             "brands that are made in the north of the country every year.")
    found = run([highlight("Only 3 countries dress the world."), flat, flat, flat, flat, flat, flat])
    finding = [f for f in found if f.rule == "no-rehook"]
    assert finding and finding[0].severity == "warning" and "s" in finding[0].message


def test_no_rehook_passes_on_a_short_reel():
    assert "no-rehook" not in rules(run([highlight("Only 3 countries dress the world."), country()]))


# -- 5 monotony ----------------------------------------------------------------------


def test_monotony_flags_four_countries_in_a_row():
    found = run([highlight("Only 3 countries dress the world.")] + [country()] * 4)
    finding = [f for f in found if f.rule == "monotony"]
    assert finding and "rank" in finding[0].hint and "versus" in finding[0].hint


def test_monotony_passes_with_a_highlight_between():
    found = run([highlight("Only 3 countries dress the world."), country(), country(), country(),
                 highlight("But which is first?"), country()])
    assert "monotony" not in rules(found)


# -- 6 length ------------------------------------------------------------------------


def test_length_warns_when_the_target_means_padding():
    found = run([highlight("Only 3 countries dress the world."), country()], length=60)
    finding = [f for f in found if f.rule == "length"][0]
    assert "padding" in finding.message


def test_length_passes_without_a_target():
    assert "length" not in rules(run([highlight("Only 3 countries dress the world."), country()]))


def test_length_warns_over_75_seconds():
    wordy = country(narration=" ".join(["Italy has many famous brands"] * 12))
    found = run([highlight("Only 3 countries dress the world.")] + [wordy] * 12)
    assert any(f.rule == "length" and "60" in f.message for f in found)


# -- 7 unsourced ---------------------------------------------------------------------


def test_unsourced_lists_every_item_without_a_source_in_one_finding():
    bare = {**country(), "source": None}
    found = run([highlight("Only 3 countries dress the world."), bare])
    finding = [f for f in found if f.rule == "unsourced"]
    assert len(finding) == 1 and "Gucci" in finding[0].message and "Versace" in finding[0].message


def test_unsourced_passes_with_a_segment_or_item_source():
    own = {**country(), "source": None, "items": [{"name": n, "source": "https://x.org"} for n in ("A", "B")]}
    assert "unsourced" not in rules(run([highlight("Only 3 countries dress the world."), country(), own]))


def test_unsourced_leaves_out_a_rank_whose_stat_has_its_own_source():
    sourced = rank(2, "Italy makes 70% of it.", source=None, country="ITA",
                   stat={"value": "70%", "source": "https://x.org"})
    found = run([highlight("Only 3 countries dress the world."), sourced])
    assert "unsourced" not in rules(found)
    bare = rank(2, "Italy makes 70% of it.", source=None, country="ITA", stat={"value": "70%"})
    finding = [f for f in run([highlight("Only 3 countries dress the world."), bare]) if f.rule == "unsourced"]
    assert finding and "rank 2" in finding[0].message


# -- 8 no-ending ---------------------------------------------------------------------


def test_no_ending_flags_a_script_that_just_stops():
    found = run([highlight("Only 3 countries dress the world."), country(narration="Italy has Gucci.")])
    assert "no-ending" in rules(found)


def test_no_ending_passes_with_a_question_or_a_rank_one():
    q = run([highlight("Only 3 countries dress the world."), country(narration="Which brand did I miss?")])
    assert "no-ending" not in rules(q)
    assert "no-ending" not in rules(run([highlight("Only 3 countries dress the world."), rank(1, "Turkey wins.")]))


# -- the reference reel and the example ------------------------------------------------


def test_the_reference_reel_fails_its_hook():
    places = ["USA", "ITA", "FRA", "JPN", "SWE", "GBR", "DEU", "ESP", "CHN", "KOR", "IND", "BRA", "CAN", "MEX", "NLD"]
    brands = [("Nike", "Apple", "Coca-Cola", "Ford"), ("Gucci", "Prada", "Fiat"), ("Chanel", "Dior")]
    segments = [country(code, f"The United States has {', '.join(b[:-1])} and {b[-1]}.", b)
                for code, b in ((p, brands[i % 3]) for i, p in enumerate(places))]
    found = rules(run(segments))
    assert {"weak-opener", "no-rehook", "monotony"} <= found


def test_brands_example_passes_the_hard_rules():
    inputs = MapReelInputs.model_validate({**json.loads(BRANDS.read_text()), "mode": "build"})
    found = lint.check(inputs, estimated_windows(inputs))
    assert not [f for f in found if f.severity == "error"], found


def test_estimate_beats_are_reel_seconds_per_segment():
    inputs = MapReelInputs.model_validate({"segments": [highlight("Only 3 countries dress the world."), country()]})
    windows = estimated_windows(inputs)
    beats = lint.estimate_beats(inputs, windows)
    assert len(beats) == 2 and all(b == sorted(b) for b in beats)
    assert beats[1][0] >= windows[1].start_ms / 1000


def _inputs(segments):
    return MapReelInputs.model_validate({"segments": segments})


def test_a_versus_vs_beat_uses_the_runtime_time():
    versus = {"kind": "versus", "countries": ["USA", "ITA"], "narration": "Only 3 lands matter here today ok.",
              "stats": [{"value": "9"}, {"value": "8"}]}
    inputs = _inputs([highlight("Only 3 countries dress the world."), versus])
    windows = estimated_windows(inputs)
    beats = lint.estimate_beats(inputs, windows)[1]
    assert (windows[1].start_ms + kinds.VS_AT_MS) / 1000 in beats


def test_a_rank_badge_beat_lands_just_after_the_fill():
    # no flight into the first segment: fill at 300, badge at 450; after a flight: fill 700, badge 850
    first = _inputs([rank(1)])
    w = estimated_windows(first)
    assert (w[0].start_ms + 450) / 1000 in lint.estimate_beats(first, w)[0]
    later = _inputs([highlight("Only 3 countries dress the world."), rank(1)])
    w = estimated_windows(later)
    assert (w[1].start_ms + 850) / 1000 in lint.estimate_beats(later, w)[1]
    held = _inputs([highlight("Only 3 countries dress the world."), rank(1, camera={"hold": True})])
    w = estimated_windows(held)
    assert (w[1].start_ms + 450) / 1000 in lint.estimate_beats(held, w)[1]


def test_a_versus_stat_is_one_beat_matched_on_its_value():
    versus = {"kind": "versus", "countries": ["USA", "ITA"], "narration": "Only 3 lands: 9 beats 8 by far, ok.",
              "stats": [{"value": "9"}, {"value": "8"}]}
    inputs = _inputs([versus])
    # start, VS, and one beat per country
    assert len(lint.estimate_beats(inputs, estimated_windows(inputs))[0]) == 4


def test_an_iso_coded_highlight_country_gets_a_matched_beat_when_its_name_is_said():
    text = "Only 3 lands matter and the first of them is the United States."
    inputs = _inputs([highlight(text)])
    inputs.segments[0].countries = ["USA"]
    window = estimated_windows(inputs)[0]
    names = {"seg-1/0": ["United States"]}
    matched = lint.estimate_beats(inputs, [window], names=names)[0]
    spread = lint.estimate_beats(inputs, [window])[0]
    want = (window.start_ms + window.speech_offset_ms + int(text.index("United States") / len(text) * window.speech_ms)) / 1000
    assert any(abs(b - want) < 0.002 for b in matched)
    assert not any(abs(b - want) < 0.002 for b in spread)


def test_a_name_matches_whole_words_only():
    assert lint._at("I can afford it", ["Ford"]) is None
    assert lint._at("a ford and Ford", ["ford"]) == 2 / len("a ford and Ford")
