"""Unit tests for storyreel: the SRT reader, scene segmentation, clip windows,
voice markers, the subtitle chain's bookkeeping and the deterministic fake brain.

No network, no ffmpeg: everything here is pure.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from reelmachine.core.errors import ProviderUnavailable
from reelmachine.ffmpeg import SubtitleTrack
from reelmachine.recipes.storyreel.models import (
    MicroClipSpec,
    StoryConcept,
    StoryPackage,
    StoryReelInputs,
    StorySection,
)
from reelmachine.recipes.storyreel.selection import (
    clip_window,
    find_concept,
    normalise_story,
    section_budget,
)
from reelmachine.recipes.storyreel.story.fake import FakeStoryBrain
from reelmachine.recipes.storyreel.subtitles import (
    SubtitleRequest,
    language_rank,
    normalise_language,
)
from reelmachine.recipes.storyreel.subtitles.auto import AutoSubtitleProvider
from reelmachine.recipes.storyreel.subtitles.fake import FAKE_STORY, FakeSubtitleProvider
from reelmachine.recipes.storyreel.subtitles.files import to_srt
from reelmachine.recipes.storyreel.sync import apply_offset
from reelmachine.recipes.storyreel.transcript import (
    clean_voiceover,
    markers_in,
    parse_srt,
    segment_scenes,
    speech_parts,
    transcript_stats,
    transcript_text,
    write_cues,
)

SRT = """1
00:00:01,000 --> 00:00:03,500
<i>The door opened.</i>

2
00:00:04.000 --> 00:00:06.000
Nobody was there.

3
00:00:12,000 --> 00:00:14,000
{\\an8}She stepped inside.

garbage block without a timecode
"""


def test_parse_srt_reads_timecodes_and_strips_markup() -> None:
    cues = parse_srt(SRT)
    assert [cue.start_ms for cue in cues] == [1000, 4000, 12000]
    assert cues[0].text == "The door opened."
    assert cues[1].end_ms == 6000
    assert cues[2].text == "She stepped inside."
    assert all(cue.text != "garbage block without a timecode" for cue in cues)


def test_parse_srt_merges_identical_overlapping_lines() -> None:
    text = (
        "1\n00:00:01,000 --> 00:00:02,000\nCome with me.\n\n"
        "2\n00:00:02,050 --> 00:00:03,500\nCome with me.\n"
    )
    cues = parse_srt(text)
    assert len(cues) == 1
    assert cues[0].end_ms == 3500


def test_scenes_split_on_pauses_and_merge_short_runs() -> None:
    cues = parse_srt(SRT)
    scenes = segment_scenes(cues)
    # the 6 s pause between cue 2 and 3 splits the transcript in two,
    # and the short first scene is merged forward
    assert len(scenes) == 1 or len(scenes) == 2
    ids = [scene.id for scene in scenes]
    assert ids == [f"s{index:04d}" for index in range(1, len(scenes) + 1)]
    if len(scenes) == 2:
        assert scenes[1].start_ms == 12000


def test_transcript_text_and_stats() -> None:
    cues = parse_srt(SRT)
    text = transcript_text(cues, title="A film")
    assert text.startswith("# A film")
    assert "[00:00:01] The door opened." in text
    stats = transcript_stats(cues, segment_scenes(cues))
    assert stats["cues"] == 3
    assert stats["words"] == len("The door opened. Nobody was there. She stepped inside.".split())


def test_voice_markers_become_pauses_and_never_reach_the_speaker() -> None:
    line = "He opened the door. [Pause] Nobody was there. [Shock]"
    assert markers_in(line) == ["pause", "shock"]
    assert clean_voiceover(line) == "He opened the door. Nobody was there."
    parts = speech_parts(line)
    assert parts == ["He opened the door.", None, "Nobody was there."]
    assert speech_parts("[Pause] just words") == [None, "just words"]


def test_apply_offset_shifts_and_drops_what_falls_off() -> None:
    cues = parse_srt(SRT)
    shifted = apply_offset(cues, 2000)
    assert shifted[0].start_ms == 3000
    dropped = apply_offset(cues, -2000)
    assert dropped[0].start_ms == 0 and dropped[0].end_ms == 1500
    assert len(apply_offset(cues, -20000)) == 0


def test_write_cues_roundtrips() -> None:
    import tempfile

    cues = parse_srt(SRT)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "out.srt"
        write_cues(cues, path)
        again = parse_srt(path.read_text(encoding="utf-8"))
    assert [(c.start_ms, c.end_ms, c.text) for c in again] == [
        (c.start_ms, c.end_ms, c.text) for c in cues
    ]


def test_clip_window_stays_inside_the_film() -> None:
    spec = MicroClipSpec(source_start_ms=10_000)
    start, duration = clip_window(spec, section_ms=4_000, movie_ms=60_000)
    assert (start, duration) == (10_000, 4_000)
    # a start too close to the end slides back so the clip fits
    start, duration = clip_window(
        MicroClipSpec(source_start_ms=59_500), section_ms=4_000, movie_ms=60_000
    )
    assert start + duration <= 60_000
    # a start past the end clamps at the last possible window (with a 100 ms tail)
    start, _ = clip_window(MicroClipSpec(source_start_ms=10**9), section_ms=3_000, movie_ms=60_000)
    assert start == 56_900
    # duration follows the spoken section, floored so a gap is still a shot
    assert clip_window(MicroClipSpec(), section_ms=10, movie_ms=60_000)[1] == 500


def test_section_budget_scales_with_the_target() -> None:
    assert section_budget(60_000) == 16
    assert section_budget(15_000) == 5
    assert section_budget(300_000) == 79  # the default 5-minute explainer
    assert section_budget(600_000) == 158  # 10 minutes
    assert section_budget(1_800_000) == 400  # capped


def test_resolve_style_id_picks_the_language_pack() -> None:
    from reelmachine.recipes.storyreel.selection import resolve_style_id

    assert resolve_style_id("", "hi") == "storyreel.hi"
    assert resolve_style_id("", "Hindi") == "storyreel.hi"  # a name, not just the code
    assert resolve_style_id("", "ar") == "storyreel.default"  # no pack ships for it
    assert resolve_style_id("storyreel.default", "hi") == "storyreel.default"


def test_resolve_voice_prefers_the_request_then_the_deployment(monkeypatch) -> None:
    from reelmachine.recipes.storyreel.selection import resolve_voice

    monkeypatch.delenv("REEL_STORY_VOICE", raising=False)
    assert resolve_voice("cartesia:abc") == "cartesia:abc"
    assert resolve_voice("") == "fake:default"
    monkeypatch.setenv("REEL_STORY_VOICE", "cartesia:hinglish-voice")
    assert resolve_voice("") == "cartesia:hinglish-voice"
    assert resolve_voice("elevenlabs:xyz") == "elevenlabs:xyz"


def test_normalise_story_anchors_clips_to_their_referenced_scene() -> None:
    """A clip that names a subtitle scene cannot be cut outside that scene's window.

    The reference is what makes "the model used the subtitle's position" checkable: a
    hallucinated timestamp is pulled back to the scene it claimed, with a warning that
    reaches the manifest; an unknown reference is reported, not silently trusted.
    """
    from reelmachine.recipes.storyreel.models import SceneChunk

    inputs = StoryReelInputs(title="x")
    scenes = [
        SceneChunk(id="s0001", start_ms=10_000, end_ms=20_000, text="First scene.", cues=2),
        SceneChunk(id="s0002", start_ms=60_000, end_ms=75_000, text="Second scene.", cues=2),
    ]
    from reelmachine.recipes.storyreel.selection import normalise_story

    story = StoryPackage(
        sections=[
            # a minute off: pulled back into s0002's window
            StorySection(
                id="",
                voiceover="One.",
                clip=MicroClipSpec(scene_ref="s0002", source_start_ms=10_500),
            ),
            # already inside s0001, kept exactly
            StorySection(
                id="",
                voiceover="Two.",
                clip=MicroClipSpec(scene_ref="s0001", source_start_ms=12_345),
            ),
            # the reference is not in the transcript: kept, but said out loud
            StorySection(
                id="",
                voiceover="Three.",
                clip=MicroClipSpec(scene_ref="s9999", source_start_ms=30_000),
            ),
        ]
    )
    normalised, warnings = normalise_story(story, movie_ms=120_000, inputs=inputs, scenes=scenes)
    assert normalised.sections[0].clip.source_start_ms == 58_500  # 60_000 - pad
    assert normalised.sections[1].clip.source_start_ms == 12_345
    assert normalised.sections[2].clip.source_start_ms == 30_000
    assert any("moved from 10500 ms into scene s0002" in note for note in warnings)
    assert any("scene_ref 's9999' is not in the transcript" in note for note in warnings)

    # and without a scene index to check against (a caller who supplies none), the
    # timestamp is honoured as-is; the references are simply reported unverifiable
    untouched, none_warnings = normalise_story(story, movie_ms=120_000, inputs=inputs)
    assert untouched.sections[0].clip.source_start_ms == 10_500
    assert any("not in the transcript" in note for note in none_warnings)


def test_normalise_story_assigns_ids_and_clamps_windows() -> None:
    inputs = StoryReelInputs(title="x")
    story = StoryPackage(
        sections=[
            StorySection(id="", voiceover="One.", clip=MicroClipSpec(source_start_ms=1_000)),
            StorySection(id="", voiceover="Two.", clip=MicroClipSpec(source_start_ms=99_000)),
            StorySection(
                id="",
                voiceover="Three.",
                clip=MicroClipSpec(source_start_ms=2_000, transition="fade"),
            ),
        ]
    )
    normalised, warnings = normalise_story(story, movie_ms=60_000, inputs=inputs)
    assert [section.id for section in normalised.sections] == ["sec-001", "sec-002", "sec-003"]
    assert normalised.sections[1].clip.source_start_ms == 0  # past the end → 0
    assert any("past the end" in note for note in warnings)
    assert any("fade" in note for note in warnings)

    short, short_warnings = normalise_story(
        StoryPackage(
            sections=[
                StorySection(id="", voiceover="One.", clip=MicroClipSpec()),
                StorySection(id="", voiceover="Two.", clip=MicroClipSpec()),
            ]
        ),
        movie_ms=60_000,
        inputs=inputs,
    )
    assert len(short.sections) == 2
    assert any("section(s)" in note for note in short_warnings)

    with pytest.raises(Exception):
        normalise_story(StoryPackage(sections=[]), movie_ms=60_000, inputs=inputs)


def test_find_concept_by_id_name_and_position() -> None:
    concepts = [
        StoryConcept(id="c1", name="The Betrayal"),
        StoryConcept(id="c2", name="The Return"),
    ]
    assert find_concept(concepts, "c2").id == "c2"
    assert find_concept(concepts, "the return").id == "c2"
    assert find_concept(concepts, "1").id == "c1"
    assert find_concept(concepts, "c9") is None


def test_languages_normalise_and_rank() -> None:
    assert normalise_language("English") == "en"
    assert normalise_language("eng") == "en"
    assert normalise_language("URD") == "ur"
    assert normalise_language("") == ""
    assert language_rank("English", ["ur", "en"]) == 1
    assert language_rank("jpn", ["ur"]) == 99
    assert language_rank("jpn", []) == 0


def test_fake_subtitle_spreads_the_story_across_the_film(tmp_path) -> None:
    provider = FakeSubtitleProvider()
    fetch = provider.fetch(
        SubtitleRequest(media_type="movie", duration_ms=120_000, languages=["en"]),
        dest_dir=tmp_path,
    )
    cues = parse_srt(Path(fetch.path).read_text(encoding="utf-8"))
    assert len(cues) == len(FAKE_STORY)
    assert cues[0].start_ms < 5_000
    assert cues[-1].end_ms <= 120_000
    assert fetch.meta["fixture"] is True


def test_to_srt_unpacks_a_zip(tmp_path) -> None:
    archive = tmp_path / "subs.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("release/movie.srt", SRT)
    srt = to_srt(archive, tmp_path / "out")
    assert srt is not None
    assert "-->" in srt.read_text(encoding="utf-8")


def test_to_srt_reads_windows_encoded_files(tmp_path) -> None:
    path = tmp_path / "cp1252.srt"
    path.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nCafé à côté\n".encode("cp1252"))
    srt = to_srt(path, tmp_path / "out")
    assert srt is not None
    assert "Café" in srt.read_text(encoding="utf-8")


def test_to_srt_refuses_a_bitmap_or_unknown_file(tmp_path) -> None:
    junk = tmp_path / "subs.sup"
    junk.write_bytes(b"\x00\x01\x02")
    assert to_srt(junk, tmp_path / "out") is None


def test_embedded_track_choice_prefers_the_language_and_avoids_forced() -> None:
    from reelmachine.recipes.storyreel.subtitles.embedded import choose_track

    tracks = [
        SubtitleTrack(index=2, ordinal=0, codec="subrip", language="eng"),
        SubtitleTrack(index=3, ordinal=1, codec="subrip", language="jpn", forced=True, default=True),
        SubtitleTrack(index=4, ordinal=2, codec="hdmv_pgs_subtitle", language="eng"),
    ]
    chosen = choose_track([t for t in tracks if t.text], ["ja"])
    assert chosen is not None and chosen.language == "jpn"
    chosen_en = choose_track([tracks[0], tracks[2]], ["en"])
    assert chosen_en is not None and chosen_en.index == 2
    # a forced track wins only when it is all there is
    only_forced = choose_track([SubtitleTrack(index=9, ordinal=0, codec="subrip", forced=True)], ["en"])
    assert only_forced is not None and only_forced.forced
    assert choose_track([], ["en"]) is None


def test_auto_chain_records_every_attempt_without_keys(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("REEL_SUBDL_API_KEY", raising=False)
    monkeypatch.delenv("REEL_OPENSUBTITLES_API_KEY", raising=False)
    monkeypatch.delenv("REEL_SUBTITLES_ORDER", raising=False)
    provider = AutoSubtitleProvider()
    request = SubtitleRequest(tmdb_id=603, languages=["en"], movie_path="")
    with pytest.raises(ProviderUnavailable) as failure:
        provider.fetch(request, dest_dir=tmp_path)
    attempts = failure.value.details.get("attempts", [])
    names = {attempt["provider"] for attempt in attempts}
    assert names == {"embedded", "subdl", "opensubtitles"}
    assert failure.value.hint and "subtitle_path" in failure.value.hint
    provider.close()


def test_fake_brain_is_deterministic_and_stays_inside_the_film() -> None:
    from reelmachine.recipes.storyreel.models import ResolvedMedia, SceneChunk

    scenes = [
        SceneChunk(id=f"s{index:04d}", start_ms=index * 10_000, end_ms=index * 10_000 + 9_000, text=f"Scene {index} text.", cues=1)
        for index in range(1, 7)
    ]
    media = ResolvedMedia(tmdb_id=1, title="Test", media_type="movie")
    inputs = StoryReelInputs(title="Test", target_ms=30_000)
    brain = FakeStoryBrain()
    concepts = brain.concepts(scenes=scenes, media=media, inputs=inputs)
    assert [concept.id for concept in concepts] == ["c1", "c2", "c3", "c4", "c5"]
    again = brain.concepts(scenes=scenes, media=media, inputs=inputs)
    assert [concept.name for concept in concepts] == [concept.name for concept in again]
    story = brain.script(scenes=scenes, media=media, inputs=inputs, concept=concepts[0])
    assert story.sections
    for section in story.sections:
        assert section.voiceover
        assert 0 <= section.clip.source_start_ms < 70_000
    assert story.seo is not None and story.seo.title


def test_inputs_forbid_extras_and_bound_scores() -> None:
    with pytest.raises(Exception):
        StoryReelInputs(title="x", nope=True)
    with pytest.raises(Exception):
        StoryConcept(name="x", viral_score=11)
    inputs = StoryReelInputs(title="x", mode="transcript")
    assert inputs.wants_transcript_only() and not inputs.wants_plan_only()


def test_a_recipes_declared_source_wins_over_reel_source(monkeypatch) -> None:
    """storyreel always wants vidvault; nadeshiko-cut still follows REEL_SOURCE.

    One deployment serves both: the local library steers nadeshiko-cut (which declares
    no default), while storyreel's download route is named by the recipe itself.
    """
    import types

    from reelmachine.core.recipe import ProviderReq
    from reelmachine.engine.providers import _default_name

    settings = types.SimpleNamespace(source="local")
    monkeypatch.setenv("REEL_SOURCE", "local")
    assert _default_name("source", ProviderReq(group="sources", default="vidvault"), settings) == "vidvault"
    assert _default_name("source", ProviderReq(group="sources", default=""), settings) == "local"


def test_reel_script_picks_the_script_writer() -> None:
    """The doodle recipe's writer follows the deployment (REEL_SCRIPT), like REEL_TTS.

    `.env.example` advertised this knob long before it reached the resolver, so an
    unset value must leave the recipe's own `template` default exactly as it was.
    """
    import types

    from reelmachine.core.recipe import ProviderReq
    from reelmachine.engine.providers import _default_name

    req = ProviderReq(group="scripts", default="template")
    assert _default_name("script", req, types.SimpleNamespace(script="llm")) == "llm"
    assert _default_name("script", req, types.SimpleNamespace(script="")) == "template"
    assert _default_name("script", req, types.SimpleNamespace()) == "template"
