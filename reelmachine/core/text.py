"""Small text helpers shared across recipes and clients."""

from __future__ import annotations

import re
import unicodedata


def slugify(text: str, *, fallback: str = "reel") -> str:
    """A safe file-name stem from arbitrary text (CJK included)."""
    text = unicodedata.normalize("NFKC", text or "").strip()
    text = re.sub(r"[^\w\s\-]+", "", text, flags=re.UNICODE).strip()
    text = re.sub(r"[\s_]+", "-", text)
    return (text[:60] or fallback).strip("-") or fallback
