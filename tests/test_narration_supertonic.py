import base64
import io
import json
import wave

import httpx
import pytest

from reelmachine.core.errors import ProviderUnavailable, VoiceUnavailable
from reelmachine.core.registry import Registry
from reelmachine.recipes.doodle.narration.supertonic import SupertonicNarration, parse_supertonic

VOICES = {"data": [{"id": "F1", "name": "F1"}, {"id": "luna", "name": "Luna"}]}


def _wav(ms: int, rate: int = 44100) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * (rate * ms // 1000))
    return buffer.getvalue()


def _speech(audio: bytes) -> dict:
    return {
        "audio_base64": base64.b64encode(audio).decode(),
        "format": "wav",
        "duration_ms": 1600,
        "voice": "luna",
        "timing_source": "estimated",
        "words": [{"text": "Hello", "start_ms": 240, "end_ms": 500}, {"text": "world.", "start_ms": 500, "end_ms": 1280}],
    }


def _provider(handler, tmp_path, **kwargs) -> SupertonicNarration:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SupertonicNarration(client=client, base_url="http://gpu-box:8787", workdir=tmp_path, **kwargs)


def test_registered_as_a_narration_provider():
    assert Registry().load("narration", "supertonic") is SupertonicNarration


def test_parse_reads_audio_and_word_timings():
    audio, spans = parse_supertonic(_speech(b"RIFF-bytes"))
    assert audio == b"RIFF-bytes"
    assert [(s.text, s.start_ms, s.end_ms) for s in spans] == [("Hello", 240, 500), ("world.", 500, 1280)]


def test_a_keyless_local_server_needs_no_key(monkeypatch, tmp_path):
    monkeypatch.delenv("SUPERTONIC_API_KEY", raising=False)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/voices":
            return httpx.Response(200, json=VOICES)
        return httpx.Response(200, json=_speech(_wav(1600)))

    provider = _provider(handler, tmp_path)
    assert provider.missing() == []
    speech = provider.synthesize("Hello world.", voice="supertonic:Luna", language="ja")
    body = json.loads(seen[-1].content)
    assert seen[-1].url.path == "/v1/tts/timestamps"
    assert body == {"text": "Hello world.", "voice": "luna", "language": "ja", "response_format": "wav"}
    assert "authorization" not in seen[-1].headers
    assert speech.voice == "supertonic:luna"
    assert speech.path.suffix == ".wav" and speech.path.read_bytes()[:4] == b"RIFF"
    assert [w.text for w in speech.segments[0].words] == ["Hello", "world."]
    # the whole clip, trailing breath included, not just the last word's end
    assert speech.duration_ms == 1600 and speech.segments[0].end_ms == 1600


def test_the_key_is_sent_as_a_bearer_token(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERTONIC_API_KEY", "secret")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/voices":
            return httpx.Response(200, json=VOICES)
        return httpx.Response(200, json=_speech(_wav(100)))

    _provider(handler, tmp_path).synthesize("hi", voice="supertonic:F1")
    assert all(r.headers["authorization"] == "Bearer secret" for r in seen)


def test_base_url_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("REEL_SUPERTONIC_URL", "http://127.0.0.1:9999/")
    assert SupertonicNarration().base_url == "http://127.0.0.1:9999"
    monkeypatch.delenv("REEL_SUPERTONIC_URL")
    assert SupertonicNarration().base_url == "http://127.0.0.1:8787"


def test_an_unknown_voice_fails_loudly(tmp_path):
    provider = _provider(lambda request: httpx.Response(200, json=VOICES), tmp_path)
    with pytest.raises(VoiceUnavailable) as caught:
        provider.synthesize("hi", voice="supertonic:nobody")
    assert "nobody" in str(caught.value)


def test_a_server_refusal_names_the_servers_reason(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/voices":
            return httpx.Response(200, json=VOICES)
        return httpx.Response(400, json={"error": {"message": "Language 'xx' is not supported", "code": "unsupported_language"}})

    with pytest.raises(VoiceUnavailable) as caught:
        _provider(handler, tmp_path).synthesize("hi", voice="supertonic:F1", language="xx")
    assert "Language 'xx' is not supported" in str(caught.value)


def test_a_wrong_key_says_to_check_the_key(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API key", "code": "invalid_api_key"}})

    with pytest.raises(VoiceUnavailable) as caught:
        _provider(handler, tmp_path).synthesize("hi", voice="supertonic:F1")
    assert "SUPERTONIC_API_KEY" in caught.value.hint


def test_a_server_that_is_down_is_unavailable_not_a_crash(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ProviderUnavailable) as caught:
        _provider(handler, tmp_path).synthesize("hi", voice="supertonic:F1")
    assert "REEL_SUPERTONIC_URL" in caught.value.hint
    assert "gpu-box:8787" in str(caught.value)
