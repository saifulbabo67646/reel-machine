from reelmachine.recipes.dialoguereel.models import DialogueReelInputs, Line
import pytest

from reelmachine.recipes.dialoguereel.schedule import Cue, estimate, quiz_line_for, schedule
from reelmachine.recipes.dialoguereel.strings import STRINGS, strings_for


def test_lines_follow_the_hook_with_gaps():
    s = schedule([1000, 2000])
    assert [(w.start_ms, w.end_ms, w.meaning_ms) for w in s.lines] == [(1500, 2500, 2100), (2850, 4850, 4050)]
    assert s.duration_ms == 5750


def test_no_lines_is_just_hook_and_tail():
    assert schedule([]).duration_ms == 1500 + 900


def test_estimate_uses_plain_text_speech():
    inp = DialogueReelInputs(cast="x", lines=[Line(speaker="a", text="手伝{てつだ}います", meaning="m")])
    s = estimate(inp)
    assert len(s.lines) == 1 and s.lines[0].speech_ms > 0


def test_plain_schedule_is_unchanged():
    s = schedule([1000, 2000])
    assert [(w.start_ms, w.end_ms, w.meaning_ms) for w in s.lines] == [(1500, 2500, 2100), (2850, 4850, 4050)]
    assert s.duration_ms == 5750 and s.cues == (Cue(1500, 0, 1.0), Cue(2850, 1, 1.0))


def test_a_slow_line_replays_after_a_gap():
    s = schedule([1500, 1000], slow=[True, False])
    w = s.lines[0]
    assert (w.slow_start_ms, w.slow_ms) == (1500 + 1500 + 350, 2000)
    assert s.lines[1].start_ms == 3350 + 2000 + 350
    assert Cue(3350, 0, 0.75) in s.cues


def test_listen_first_plays_every_line_then_a_bridge():
    s = schedule([1000, 1000], listen_first=True)
    assert [w.start_ms for w in s.listen] == [1500, 2850]
    assert s.bridge_ms == 3850 + 350 and s.lines[0].start_ms == s.bridge_ms + 700


def test_recap_and_quiz_follow_the_dialogue():
    s = schedule([1000, 1200], recap=True, quiz_line=1)
    end = s.lines[-1].end_ms
    assert s.recap_ms == end + 350
    assert (s.quiz.start_ms, s.quiz.reveal_ms) == (s.recap_ms + 3000, s.recap_ms + 3000 + 3000)
    assert s.quiz.end_ms == s.quiz.reveal_ms + 1200 + 800 and s.duration_ms == s.quiz.end_ms + 900
    assert s.cues[-1] == Cue(s.quiz.reveal_ms, 1, 1.0)


@pytest.mark.parametrize("listen", [False, True])
@pytest.mark.parametrize("slow", [[False] * 3, [True, False, True]])
@pytest.mark.parametrize("quiz", [None, 0])
def test_cues_never_overlap_and_fit(listen, slow, quiz):
    speech = [900, 1300, 700]
    s = schedule(speech, slow=slow, listen_first=listen, recap=True, quiz_line=quiz)
    ends = [c.at_ms + round(speech[c.line] / c.rate) for c in s.cues]
    assert all(e <= n.at_ms for e, n in zip(ends, s.cues[1:])) and ends[-1] <= s.duration_ms


def _inputs(quiz, *line_specs):
    lines = [Line(speaker="a", text="x", meaning=m, keys=[{"text": "k", "meaning": "m"}] * n) for m, n in line_specs]
    return DialogueReelInputs(cast="x", lines=lines, quiz=quiz)


def test_quiz_line_rule():
    assert quiz_line_for(_inputs(True, ("a", 1), ("b", 2), ("c", 2))) == 1
    assert quiz_line_for(_inputs(True, ("a", 0), ("longer", 0), ("c", 0))) == 1
    assert quiz_line_for(_inputs(False, ("a", 1))) is None
    assert quiz_line_for(_inputs(2, ("a", 1), ("b", 1), ("c", 0))) == 2
    assert quiz_line_for(_inputs(True)) is None


def test_estimate_passes_the_options():
    inp = _inputs(True, ("a", 1), ("b", 2))
    inp = inp.model_copy(update={"flow": "listen_first"})
    s = estimate(inp)
    assert s.listen and s.recap_ms is not None and s.quiz.line == 1


def test_strings_fall_back_to_english_and_format_the_level():
    assert strings_for("fr", "N5")["follow"] == "Follow for daily N5"
    assert strings_for("en", "")["follow"] == "Follow for more" and strings_for("ja-JP", "N5")["recap"] != STRINGS["en"]["recap"]
    assert strings_for("ja", "N5")["follow"] == "毎日のN5はフォローしてね"
