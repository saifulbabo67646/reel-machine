"""Style packs carry licence and provenance; third-party IP is never hard-coded."""

from __future__ import annotations

import json

import pytest

from reelmachine.core.style import StyleNotFound, bundled_styles, get_style, load_style_pack


def test_bundled_styles_all_declare_licence_and_provenance() -> None:
    packs = bundled_styles()
    assert {"quranic.parchment", "quranic.midnight", "doodle.default", "doodle.mono"} <= set(packs)
    for pack in packs.values():
        assert pack.licence.name, f"{pack.id} has no licence"
        assert pack.provenance.provider, f"{pack.id} has no provenance"
        assert pack.id != ""


def test_get_style_and_unknown_style() -> None:
    assert get_style("doodle.default").render_modes == ["stroke", "program"]
    with pytest.raises(StyleNotFound):
        get_style("nope")


def test_default_packs_do_not_carry_third_party_visual_ip() -> None:
    # the study reference's "small black character" style and other third-party IP must
    # only ever arrive through a style pack that declares it — never in our defaults
    banned = ("xiaohei", "小黑", "nano banana", "ian-xiaohei")
    blob = json.dumps(
        {pid: pack.model_dump() for pid, pack in bundled_styles().items()}, ensure_ascii=False
    ).lower()
    for token in banned:
        assert token not in blob


def test_style_requires_a_licence() -> None:
    from pydantic import ValidationError

    from reelmachine.core.style import StylePack

    with pytest.raises(ValidationError):
        StylePack(id="x")  # type: ignore[call-arg]
