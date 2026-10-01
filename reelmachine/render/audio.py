"""Audio mastering profiles shared by recipes that master their own track."""

from __future__ import annotations

#: Profile name → ffmpeg filter (`None` leaves the audio untouched).
AUDIO_PROFILES: dict[str, str | None] = {
    "none": None,
    "studio": "loudnorm=I=-16:TP=-1.5:LRA=11",
    "mobile": "highpass=f=80,loudnorm=I=-14:TP=-1.0:LRA=9",
    "youtube": "loudnorm=I=-14:TP=-1.0:LRA=11",
    "tiktok": "loudnorm=I=-12:TP=-1.0:LRA=8",
}


def audio_filter(profile: str) -> str | None:
    return AUDIO_PROFILES.get(profile, AUDIO_PROFILES["studio"])
