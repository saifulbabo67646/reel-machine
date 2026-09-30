"""Config parsing — the `.env` reader and defensive enum coercion.

Both of these were real bugs: an inline comment in `.env.example` was being
kept as part of the value, so `REEL_ASPECT=vertical  # vertical | square` set
the aspect to that whole string and silently rendered a landscape reel.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reelmachine.config import ASPECTS, load_dotenv, normalise_aspect, parse_env_value


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("vertical", "vertical"),
        ("vertical   # vertical | square | original", "vertical"),
        ("vertical\t# tab comment", "vertical"),
        ("  spaced  ", "spaced"),
        ("", ""),
        ("a#b", "a#b"),                      # '#' with no leading space is data
        ('"a # b"', "a # b"),                # quoted, so kept
        ("'x # y'", "x # y"),
        ('"unterminated', "unterminated"),   # tolerate a missing closing quote
    ],
)
def test_parse_env_value(raw: str, expected: str) -> None:
    assert parse_env_value(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("vertical", "vertical"),
        ("VERTICAL", "vertical"),
        ("vertical          # vertical | original | square", "vertical"),
        ("square # note", "square"),
        ("original", "original"),
        ("", "vertical"),
        ("   ", "vertical"),
        ("bogus", "vertical"),       # never fall through to the landscape branch
        ("vertcal", "vertical"),
    ],
)
def test_normalise_aspect(raw: str, expected: str) -> None:
    assert normalise_aspect(raw) == expected


def test_normalise_aspect_default_is_valid() -> None:
    assert normalise_aspect("nonsense") in ASPECTS


def test_load_dotenv_handles_comments_quotes_and_blank_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "# a full-line comment",
                "",
                "REEL_ASPECT=vertical          # vertical | original | square",
                'REEL_FONT_JA="Hiragino Sans"',
                "REEL_FONT_EN='Helvetica'",
                "REEL_MAX_SEGMENTS=7",
                "NO_EQUALS_SIGN",
                "=novalue",
                "REEL_HLS_TEMPLATE=https://host/a?x=1#frag",
            ]
        ),
        encoding="utf-8",
    )
    for key in ("REEL_ASPECT", "REEL_FONT_JA", "REEL_FONT_EN", "REEL_MAX_SEGMENTS", "REEL_HLS_TEMPLATE"):
        monkeypatch.delenv(key, raising=False)

    parsed = load_dotenv(env)

    assert parsed["REEL_ASPECT"] == "vertical"
    assert parsed["REEL_FONT_JA"] == "Hiragino Sans"
    assert parsed["REEL_FONT_EN"] == "Helvetica"
    assert parsed["REEL_MAX_SEGMENTS"] == "7"
    # A URL fragment is not a comment.
    assert parsed["REEL_HLS_TEMPLATE"] == "https://host/a?x=1#frag"
    assert "" not in parsed and "NO_EQUALS_SIGN" not in parsed


def test_load_dotenv_does_not_override_real_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text("REEL_ASPECT=square\n", encoding="utf-8")
    monkeypatch.setenv("REEL_ASPECT", "vertical")

    load_dotenv(env)

    import os

    assert os.environ["REEL_ASPECT"] == "vertical"


def test_load_dotenv_missing_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_dotenv(tmp_path / "nope.env") == {}
