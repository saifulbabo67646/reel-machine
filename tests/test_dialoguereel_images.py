import base64
import io
import json

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from reelmachine.config import Settings
from reelmachine.core.errors import InvalidInput, ProviderUnavailable
from reelmachine.engine.executor import ledger_total
from reelmachine.recipes.dialoguereel import images
from reelmachine.recipes.dialoguereel.models import DialogueReelInputs, ImageSource


def _png(size=(64, 64), box=None) -> bytes:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    if box:
        img.paste((200, 50, 50, 255), box)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


PNG = _png(box=(8, 8, 56, 56))


def test_image_source_forms():
    ImageSource(kind="image", provider="openai", description="a cheerful onigiri")
    ImageSource(kind="image", poses={"neutral": "https://x/n.png"})
    for bad in ({"kind": "image"}, {"kind": "image", "provider": "openai"},
                {"kind": "image", "poses": {"talk": "a.png"}},
                {"kind": "image", "provider": "fake", "description": "x", "poses": {"neutral": "a.png"}},
                {"kind": "image", "poses": {"neutral": "a.png"}, "variant": 2},
                {"kind": "image", "description": "x"},
                {"kind": "image", "provider": "fake", "description": "x", "variant": 100},
                {"kind": "image", "poses": {"neutral": "a.png", "angry": "b.png"}}):
        with pytest.raises(ValidationError):
            ImageSource.model_validate(bad)


def test_a_cast_accepts_an_image_source():
    chars = [{"id": "kid", "name": "Rice", "side": "left",
              "source": {"kind": "image", "provider": "fake", "description": "an onigiri"}},
             {"id": "mom", "name": "Mug", "side": "right", "source": {"kind": "builtin", "name": "mug"}}]
    lines = [{"speaker": "kid", "text": "はい", "reading": "hai", "meaning": "yes"}] * 3
    inputs = DialogueReelInputs.model_validate({"title": "t", "cast": {"characters": chars}, "lines": lines})
    assert isinstance(inputs.cast.characters[0].source, ImageSource)


def _transport(seen, status=200):
    def handler(request):
        seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "bad key sk-test"}})
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
    return httpx.MockTransport(handler)


def test_openai_request_shape():
    seen = []
    gen = images.OpenAIImages("sk-test", client=httpx.Client(transport=_transport(seen)))
    assert gen.generate("p") == PNG and gen.edit(PNG, "q") == PNG
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/images/generations" and body["background"] == "transparent"
    assert body == {"model": "gpt-image-1", "prompt": "p", "size": "1024x1024", "background": "transparent",
                    "n": 1, "output_format": "png"}
    assert seen[0].headers["authorization"] == "Bearer sk-test"
    assert seen[1].url.path == "/v1/images/edits" and b'name="image"' in seen[1].content
    assert b'name="prompt"' in seen[1].content and seen[1].headers["authorization"] == "Bearer sk-test"


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_openai_refusal_names_provider_not_key(status):
    gen = images.OpenAIImages("sk-test", client=httpx.Client(transport=_transport([], status)))
    with pytest.raises(ProviderUnavailable) as exc:
        gen.generate("p")
    assert "openai" in exc.value.message and str(status) in exc.value.message
    assert "sk-test" not in exc.value.message + exc.value.hint + json.dumps(exc.value.details)


def test_a_bad_request_suggests_rephrasing():
    gen = images.OpenAIImages("sk-test", client=httpx.Client(transport=_transport([], 400)))
    with pytest.raises(ProviderUnavailable) as exc:
        gen.generate("p")
    assert "safety" in exc.value.hint and "rephrase" in exc.value.hint


def test_missing_key_names_the_setting():
    with pytest.raises(ProviderUnavailable) as exc:
        images.generator_for("openai", Settings(openai_api_key=""))
    assert "REEL_OPENAI_API_KEY" in (exc.value.message + exc.value.hint)
    assert images.generator_for("fake", Settings()).name == "fake"
    assert images.generator_for("openai", Settings(openai_api_key="sk")).name == "openai"


def test_settings_read_the_openai_keys(monkeypatch):
    from reelmachine.config import get_settings
    monkeypatch.setenv("REEL_OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("REEL_OPENAI_IMAGE_MODEL", "gpt-image-2")
    get_settings.cache_clear()
    try:
        s = get_settings()
    finally:
        get_settings.cache_clear()
    assert s.openai_api_key == "sk-env" and s.openai_image_model == "gpt-image-2"


def test_fake_images_are_deterministic():
    fake = images.FakeImages()
    a, b = fake.generate("x"), fake.generate("x")
    assert a == b and a != fake.generate("y")
    with Image.open(io.BytesIO(a)) as img:
        assert img.size == (512, 512) and img.mode == "RGBA" and img.getpixel((0, 0))[3] == 0
    assert fake.edit(a, "mouth open as if talking") != fake.edit(a, "smiling")


class _Counting:
    name = "openai"

    def __init__(self, fail=None):
        self.calls, self.prompts, self.fail = 0, [], fail
        self.fake = images.FakeImages()

    def generate(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        return self.fake.generate(prompt)

    def edit(self, image, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        if self.fail and self.fail in prompt:
            raise ProviderUnavailable("openai refused the image (HTTP 400)")
        return self.fake.edit(image, prompt)


def test_make_poses_fake_and_ledger(tmp_path, monkeypatch):
    gen = _Counting()
    monkeypatch.setattr(images, "generator_for", lambda provider, settings: gen)
    src = ImageSource(kind="image", provider="openai", description="a cheerful onigiri", variant=3)
    ledger = images.quota_ledger(tmp_path, "openai")
    assert ledger.path == tmp_path / "quota" / "openai.json"
    paths, warnings = images.make_poses(src, tmp_path / "c", settings=Settings(), allow_local=True,
                                        ledger=ledger, path="p")
    assert set(paths) == set(images.POSES) and warnings == [] and ledger_total(ledger.path) == 4
    assert all(p.exists() and p.name == f"{k}.png" for k, p in paths.items())
    assert gen.prompts[0] == images.STYLE + "a cheerful onigiri (take 3)"
    assert gen.prompts[1:] == [images.POSE_PROMPTS[p] for p in images.POSES[1:]]
    images.make_poses(src, tmp_path / "c", settings=Settings(), allow_local=True, ledger=ledger, path="p")
    assert gen.calls == 4 and ledger_total(ledger.path) == 4


def test_make_poses_with_the_fake_provider(tmp_path):
    src = ImageSource(kind="image", provider="fake", description="a mug")
    paths, warnings = images.make_poses(src, tmp_path / "c", settings=Settings(), allow_local=False,
                                        ledger=None, path="p")
    assert set(paths) == set(images.POSES) and warnings == []


def test_failed_pose_falls_back(tmp_path, monkeypatch):
    gen = _Counting(fail="smiling")
    monkeypatch.setattr(images, "generator_for", lambda provider, settings: gen)
    src = ImageSource(kind="image", provider="openai", description="x")
    ledger = images.quota_ledger(tmp_path, "openai")
    paths, warnings = images.make_poses(src, tmp_path / "c", settings=Settings(), allow_local=True,
                                        ledger=ledger, path="cast.characters[0].source")
    assert "happy" not in paths and set(paths) == {"neutral", "talk", "surprised"}
    assert any("the happy pose failed" in w and w.endswith("; using neutral") for w in warnings)
    assert ledger_total(ledger.path) == 3


def test_a_failed_neutral_is_an_error(tmp_path, monkeypatch):
    class Broken(_Counting):
        def generate(self, prompt):
            raise ProviderUnavailable("openai refused the image (HTTP 401)")
    monkeypatch.setattr(images, "generator_for", lambda provider, settings: Broken())
    src = ImageSource(kind="image", provider="openai", description="x")
    with pytest.raises(ProviderUnavailable):
        images.make_poses(src, tmp_path / "c", settings=Settings(), allow_local=True, ledger=None, path="p")


def test_supplied_poses(tmp_path):
    (tmp_path / "n.png").write_bytes(PNG)
    (tmp_path / "bad.png").write_bytes(b"not a png")
    src = ImageSource(kind="image", poses={"neutral": str(tmp_path / "n.png"), "happy": str(tmp_path / "bad.png")})
    paths, warnings = images.make_poses(src, tmp_path / "c", settings=Settings(), allow_local=True,
                                        ledger=None, path="p")
    assert set(paths) == {"neutral"} and len(warnings) == 1 and "p: the happy pose failed" in warnings[0]
    with Image.open(paths["neutral"]) as img:
        assert img.size == (48, 48)
    missing = ImageSource(kind="image", poses={"neutral": str(tmp_path / "gone.png")})
    with pytest.raises(InvalidInput) as exc:
        images.make_poses(missing, tmp_path / "d", settings=Settings(), allow_local=True, ledger=None, path="p")
    assert exc.value.details["path"] == "p.poses.neutral"


def test_trim_png_crops_to_alpha():
    trimmed = images.trim_png(_png((400, 400), box=(150, 100, 250, 150)))
    with Image.open(io.BytesIO(trimmed)) as img:
        assert img.size == (100, 50)


def test_remote_caller_refuses_private_and_local_poses():
    for ref in ("http://127.0.0.1/a.png", "http://169.254.169.254/a.png", "http://10.0.0.5/a.png",
                "/etc/passwd", "a.png"):
        with pytest.raises(InvalidInput) as exc:
            images.read_pose(ref, allow_local=False, path="cast.characters[0].source.poses.neutral")
        assert exc.value.details["path"].endswith("poses.neutral")


def test_remote_caller_refuses_a_host_resolving_privately(monkeypatch):
    monkeypatch.setattr(images.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0)), (2, 1, 6, "", ("10.1.1.1", 0))])
    with pytest.raises(InvalidInput):
        images.read_pose("https://example.com/a.png", allow_local=False, path="p")


def _remote(monkeypatch, routes, resolve):
    """Serve `routes[(host header, path)]` and resolve hostnames via `resolve`; returns (requests, lookups)."""
    seen, lookups = [], []

    def handler(request):
        seen.append(request)
        return routes[(request.headers["host"], request.url.path)]

    def getaddrinfo(host, port, *args, **kwargs):
        lookups.append(host)
        return [(2, 1, 6, "", (resolve[host], port))]

    monkeypatch.setattr(images.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(images, "_fetch_client", lambda: httpx.Client(transport=httpx.MockTransport(handler),
                                                                      follow_redirects=False, trust_env=False))
    return seen, lookups


def _png_response(data):
    return httpx.Response(200, content=data, headers={"content-type": "image/png"})


def _redirect(to):
    return httpx.Response(302, headers={"location": to})


def test_a_downloaded_pose_is_pinned_and_normalised(monkeypatch):
    seen, lookups = _remote(monkeypatch, {
        ("a.example", "/a.png"): _redirect("https://b.example/b.png"),
        ("b.example", "/b.png"): _png_response(_png((40, 40), box=(0, 0, 10, 20)))},
        {"a.example": "93.184.216.34", "b.example": "93.184.216.35"})
    data = images.read_pose("https://a.example/a.png", allow_local=False, path="p")
    with Image.open(io.BytesIO(data)) as img:
        assert img.size == (10, 20)
    assert [r.url.host for r in seen] == ["93.184.216.34", "93.184.216.35"]
    assert [r.headers["host"] for r in seen] == ["a.example", "b.example"]
    assert seen[0].extensions["sni_hostname"] == "a.example" and lookups == ["a.example", "b.example"]


@pytest.mark.parametrize("target, reason", [
    ("http://10.0.0.1/x.png", "not a public address"),
    ("http://inside.example/x.png", "not a public address"),
    ("file:///etc/passwd", "not an http(s) URL"),
    ("ftp://a.example/x.png", "not an http(s) URL")])
def test_a_redirect_is_checked_on_every_hop(monkeypatch, target, reason):
    seen, _ = _remote(monkeypatch, {("a.example", "/a.png"): _redirect(target)},
                      {"a.example": "93.184.216.34", "inside.example": "192.168.1.9", "10.0.0.1": "10.0.0.1"})
    with pytest.raises(InvalidInput) as exc:
        images.read_pose("https://a.example/a.png", allow_local=False, path="p")
    assert exc.value.message == f"could not fetch the pose image ({reason})" and len(seen) == 1


def test_too_many_redirects(monkeypatch):
    routes = {("a.example", f"/{i}"): _redirect(f"/{i + 1}") for i in range(5)}
    seen, lookups = _remote(monkeypatch, routes, {"a.example": "93.184.216.34"})
    with pytest.raises(InvalidInput) as exc:
        images.read_pose("https://a.example/0", allow_local=False, path="p")
    assert exc.value.message == "could not fetch the pose image (too many redirects)"
    assert len(seen) == 4 and len(lookups) == 4


@pytest.mark.parametrize("response, reason", [
    (httpx.Response(404, text="secret.example/x"), "HTTP 404"),
    (httpx.Response(200, text="<html>", headers={"content-type": "text/html"}), "not an image"),
    (httpx.Response(200, content=b"<svg/>", headers={"content-type": "image/svg+xml"}), None)])
def test_remote_errors_are_fixed_text(monkeypatch, response, reason):
    _remote(monkeypatch, {("a.example", "/a"): response}, {"a.example": "93.184.216.34"})
    with pytest.raises(InvalidInput) as exc:
        images.read_pose("https://a.example/a", allow_local=False, path="p")
    if reason:
        assert exc.value.message == f"could not fetch the pose image ({reason})"
    assert "a.example" not in exc.value.message and exc.value.details == {"path": "p"}


def test_local_caller_reads_a_png(tmp_path):
    (tmp_path / "a.png").write_bytes(_png((300, 300), box=(0, 0, 30, 60)))
    with Image.open(io.BytesIO(images.read_pose(str(tmp_path / "a.png"), allow_local=True, path="p"))) as img:
        assert img.size == (30, 60) and img.mode == "RGBA"
    (tmp_path / "a.svg").write_bytes(b"<svg/>")
    (tmp_path / "a.txt").write_bytes(PNG)
    for name in ("a.svg", "a.txt", "gone.png"):
        with pytest.raises(InvalidInput) as exc:
            images.read_pose(str(tmp_path / name), allow_local=True, path="p")
        assert exc.value.details["path"] == "p"


def test_a_missing_key_is_an_error_even_when_neutral_exists(tmp_path):
    folder = tmp_path / "c"
    folder.mkdir()
    (folder / "neutral.png").write_bytes(PNG)
    src = ImageSource(kind="image", provider="openai", description="x")
    with pytest.raises(ProviderUnavailable) as exc:
        images.make_poses(src, folder, settings=Settings(openai_api_key=""), allow_local=True, ledger=None, path="p")
    assert "REEL_OPENAI_API_KEY" in exc.value.hint


def test_edits_start_from_the_untrimmed_neutral_and_the_generator_is_closed(tmp_path, monkeypatch):
    class Recording(_Counting):
        closed = 0
        sizes = []

        def edit(self, image, prompt):
            with Image.open(io.BytesIO(image)) as img:
                self.sizes.append(img.size)
            return super().edit(image, prompt)

        def close(self):
            self.closed += 1

    gen = Recording()
    monkeypatch.setattr(images, "generator_for", lambda provider, settings: gen)
    src = ImageSource(kind="image", provider="openai", description="x")
    folder = tmp_path / "c"
    images.make_poses(src, folder, settings=Settings(), allow_local=True, ledger=None, path="p")
    (folder / "talk.png").unlink()
    images.make_poses(src, folder, settings=Settings(), allow_local=True, ledger=None, path="p")
    assert gen.sizes == [(512, 512)] * 4 and gen.closed == 2
    with Image.open(folder / "neutral.png") as img:
        assert img.size != (512, 512)


def test_openai_closes_only_its_own_client():
    client = httpx.Client(transport=_transport([]))
    with images.OpenAIImages("sk-test", client=client):
        pass
    assert not client.is_closed
    own = images.OpenAIImages("sk-test")
    own.close()
    assert own.client.is_closed


def test_a_download_sends_a_user_agent_with_a_contact_url(monkeypatch):
    # Wikimedia answers 403 to a bare UA; one naming the project and a contact URL is let through
    from reelmachine import __version__
    from reelmachine.recipes.mapreel.geodata import USER_AGENT

    assert USER_AGENT == f"reel-machine/{__version__} (+https://github.com/saifulbabo67646/reel-machine)"
    seen, _ = _remote(monkeypatch, {("a.example", "/a.png"): _png_response(_png((40, 40), box=(0, 0, 10, 20)))},
                      {"a.example": "93.184.216.34"})
    images.read_pose("https://a.example/a.png", allow_local=False, path="p")
    assert seen[0].headers["user-agent"] == USER_AGENT
