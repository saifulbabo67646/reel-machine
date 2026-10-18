"""Section strings shown on the reel, in the viewer's (meaning) language."""

from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "dialogue": "Dialogue",
        "listen": "👂 Listen first — how much did you catch?",
        "bridge": "Now with subtitles",
        "recap": "Words from this video",
        "quiz": "How do you say:",
        "cta": "Comment your answer 👇",
        "follow": "Follow for daily {level}",
        "follow_plain": "Follow for more",
    },
    "ja": {
        "dialogue": "会話",
        "listen": "👂 まずは聞いてみよう — どれくらい分かった？",
        "bridge": "字幕つきでもう一度",
        "recap": "この動画のことば",
        "quiz": "なんて言う？",
        "cta": "答えをコメントしてね 👇",
        "follow": "毎日の{level}はフォローしてね",
        "follow_plain": "フォローしてね",
    },
    "ko": {
        "dialogue": "대화",
        "listen": "👂 먼저 들어봐요 — 얼마나 들렸나요?",
        "bridge": "이제 자막과 함께",
        "recap": "이 영상의 단어",
        "quiz": "어떻게 말할까요?",
        "cta": "정답을 댓글로 남겨요 👇",
        "follow": "매일 {level} 팔로우하세요",
        "follow_plain": "팔로우하세요",
    },
    "es": {
        "dialogue": "Diálogo",
        "listen": "👂 Escucha primero — ¿cuánto entendiste?",
        "bridge": "Ahora con subtítulos",
        "recap": "Palabras de este video",
        "quiz": "¿Cómo se dice?",
        "cta": "Comenta tu respuesta 👇",
        "follow": "Sígueme para {level} a diario",
        "follow_plain": "Sígueme para más",
    },
    "zh": {
        "dialogue": "对话",
        "listen": "👂 先听一听 — 你听懂了多少？",
        "bridge": "现在带字幕",
        "recap": "本视频的单词",
        "quiz": "怎么说？",
        "cta": "在评论区写下答案 👇",
        "follow": "关注我，每天学{level}",
        "follow_plain": "关注我看更多",
    },
}


def strings_for(meaning_language: str, level: str) -> dict[str, str]:
    """The section strings for `meaning_language` (primary subtag, else English); `follow` carries the level."""
    primary = meaning_language.strip().lower().replace("_", "-").split("-")[0]
    table = STRINGS.get(primary, STRINGS["en"])
    out = {k: v for k, v in table.items() if k not in ("follow", "follow_plain")}
    out["follow"] = table["follow"].format(level=level) if level else table["follow_plain"]
    return out
