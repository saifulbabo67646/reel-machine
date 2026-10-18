from reelmachine.recipes.dialoguereel.models import DialogueReelInputs, Line
from reelmachine.recipes.dialoguereel.schedule import estimate, schedule


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
