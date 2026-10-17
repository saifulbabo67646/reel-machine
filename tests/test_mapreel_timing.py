from reelmachine.core.timeline import WordSpan
from reelmachine.recipes.mapreel.timing import FLY_MS, POP_GAP_MS, match, pop_times, tokens


def _words(text, step=300):
    return [WordSpan(text=w, start_ms=i * step, end_ms=i * step + 250) for i, w in enumerate(text.split())]


def test_tokens_normalise_brand_spellings():
    assert tokens("Levi's") == ["levis"]
    assert tokens("Dolce & Gabbana") == ["dolce", "and", "gabbana"]
    assert tokens("Fjällräven") == ["fjallraven"]
    assert tokens("H&M") == ["h", "and", "m"]


def test_items_match_their_spoken_words_in_order():
    words = _words("Italy has Gucci, Prada, Dolce and Gabbana, and Valentino.")
    assert match(["Gucci", "Prada", "Dolce & Gabbana", "Valentino"], words) == [600, 900, 1200, 2400]


def test_a_name_spoken_differently_falls_back_to_none():
    words = _words("Sweden has H and M and Acne Studios.")
    assert match(["H&M", "Acne Studios", "Fjallraven"], words) == [600, 1800, None]


def test_a_repeated_word_matches_the_next_occurrence():
    words = _words("Canada has Canada Goose and Roots.")
    assert match(["Canada Goose", "Roots"], words) == [600, 1500]


def test_an_out_of_order_name_is_still_found():
    words = _words("France has Dior and Chanel.")
    assert match(["Chanel", "Dior"], words) == [1200, 600]


def test_pop_times_wait_for_the_flight_and_keep_a_gap():
    times, spread = pop_times([100, 120, None, 3000], overrides=[None, None, None, None],
                              speech_start_ms=250, speech_end_ms=3500)
    assert times[0] >= FLY_MS - 100 and times[1] - times[0] >= POP_GAP_MS
    assert spread == [2] and times[1] < times[2] < times[3]


def test_overrides_win():
    times, spread = pop_times([500, 900], overrides=[2000, None], speech_start_ms=0, speech_end_ms=3000)
    assert times[0] == 2000 and spread == []


def test_a_run_of_unheard_names_spreads_evenly():
    """A highlight whose countries are never named: fills spread across the speech, not
    bunched at the end by halving the remaining gap each time."""
    times, spread = pop_times([None] * 4, overrides=[None] * 4, speech_start_ms=250, speech_end_ms=5000)
    assert spread == [0, 1, 2, 3]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert max(gaps) - min(gaps) <= 1 and times[0] < 2000 and times[-1] < 4500
