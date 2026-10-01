"""Timeline round-trip, materialisation and digests."""

from __future__ import annotations

from reelmachine.core.timeline import (
    Background,
    Caption,
    Clip,
    GeneratedScene,
    GradientSpec,
    ImageSequence,
    SourceCut,
    SpeechSegment,
    TimedAudioRef,
    Timeline,
    WordSpan,
    timeline_digest,
)

ASSET = "a" * 32


def _timeline() -> Timeline:
    return Timeline(
        duration_ms=4000,
        aspect="vertical",
        render_mode="stroke",
        style="doodle.default",
        video=[
            SourceCut(element_id="e1", asset=ASSET, src_in_ms=1000, src_out_ms=2200, start_ms=0),
            Background(
                element_id="e2",
                duration_ms=1500,
                start_ms=0,
                gradient=GradientSpec(colors=["#000000", "#111111"]),
            ),
            ImageSequence(element_id="e3", frames_dir="/tmp/frames", duration_ms=800, start_ms=500),
            GeneratedScene(
                element_id="e4",
                duration_ms=2000,
                start_ms=2000,
                renderer="stroke",
                spec={"regions": []},
                render_mode="stroke",
            ),
        ],
        audio=[
            TimedAudioRef(
                element_id="a1", asset=ASSET, duration_ms=4000, role="narration"
            )
        ],
        captions=[
            Caption(
                start_ms=0,
                end_ms=900,
                text="hello",
                lang="en",
                words=[WordSpan(text="hello", start_ms=0, end_ms=900)],
            )
        ],
    )


def test_timeline_round_trips_through_json() -> None:
    timeline = _timeline()
    payload = timeline.model_dump_json()
    assert Timeline.model_validate_json(payload) == timeline


def test_video_kinds_parse_from_the_discriminator() -> None:
    timeline = _timeline()
    kinds = [element.kind for element in timeline.video]
    assert kinds == ["source_cut", "background", "image_sequence", "generated_scene"]
    assert timeline.unprepared and not timeline.prepared


def test_materialise_replaces_every_declarative_element() -> None:
    timeline = _timeline()
    replacements = {
        element.element_id: Clip(
            element_id=element.element_id,
            asset="b" * 32,
            duration_ms=100,
            origin="cut",
        )
        for element in timeline.unprepared
    }
    prepared = timeline.materialise(replacements)
    assert prepared.prepared
    assert all(isinstance(el, Clip) for el in prepared.video)
    assert len(prepared.video) == len(timeline.video)


def test_materialise_refuses_to_drop_an_element() -> None:
    timeline = _timeline()
    try:
        timeline.materialise({})
    except ValueError as exc:
        assert "e1" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected ValueError")


def test_video_at_returns_the_element_covering_a_time() -> None:
    timeline = _timeline()
    assert timeline.video_at(100).element_id == "e1"
    assert timeline.video_at(2500).element_id == "e4"
    assert timeline.video_at(4000) is None


def test_digest_is_stable_and_sensitive() -> None:
    timeline = _timeline()
    assert timeline_digest(timeline) == timeline_digest(_timeline())
    changed = timeline.model_copy(update={"duration_ms": 4001})
    assert timeline_digest(changed) != timeline_digest(timeline)


def test_speech_segments_carry_word_timings() -> None:
    segment = SpeechSegment(
        text="bismillah",
        start_ms=0,
        end_ms=1200,
        words=[WordSpan(text="bismillah", start_ms=0, end_ms=1200)],
    )
    assert segment.words[0].end_ms == 1200
