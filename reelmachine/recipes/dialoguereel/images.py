"""Image characters: PNG poses generated from a description, or supplied by the caller."""

from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import socket
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from reelmachine.core.errors import InvalidInput, ProviderUnavailable, ReelError
from reelmachine.nadeshiko import QuotaLedger
from reelmachine.recipes.dialoguereel.models import ImageSource
from reelmachine.recipes.mapreel.stages import (
    IMAGE_MAX_BYTES,
    IMAGE_MAX_SIDE,
    IMAGE_SUFFIXES,
    _download,
    _normalise,
    _write_atomic,
)

POSES = ("neutral", "talk", "happy", "surprised")
STYLE = ("A single cute kawaii sticker character, full body, centred, facing the viewer, thick white outline, "
         "flat pastel colours with one soft shade, big head, simple dot eyes, small mouth, pink blush cheeks, "
         "transparent background, no text. The character: ")
POSE_PROMPTS = {
    "talk": "The same character, identical in every way, with its mouth open as if talking.",
    "happy": "The same character, identical in every way, smiling happily with closed ^ ^ eyes.",
    "surprised": "The same character, identical in every way, surprised: wide eyes and a small round open mouth.",
}
LOCAL_REFUSED = "local files are only read for local runs; pass an https URL"
_SIZE = "1024x1024"


class Generator(Protocol):
    name: str

    def generate(self, prompt: str) -> bytes: ...

    def edit(self, image: bytes, prompt: str) -> bytes: ...


class OpenAIImages:
    """OpenAI's image API (gpt-image-1): transparent 1024 px PNGs."""

    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-image-1", client: httpx.Client | None = None,
                 base_url: str = "https://api.openai.com"):
        self._key = api_key
        self.model = model
        self.client = client or httpx.Client(timeout=180)
        self.base_url = base_url.rstrip("/")

    def _post(self, route: str, **kwargs) -> bytes:
        try:
            response = self.client.post(f"{self.base_url}{route}",
                                        headers={"Authorization": f"Bearer {self._key}"}, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"openai could not be reached ({type(exc).__name__})") from None
        if response.status_code != 200:
            hint = {401: "check REEL_OPENAI_API_KEY", 403: "check REEL_OPENAI_API_KEY and the model's access",
                    429: "openai's rate limit or quota; try again later"}.get(response.status_code, "")
            raise ProviderUnavailable(f"openai refused the image request (HTTP {response.status_code})", hint=hint)
        try:
            return base64.b64decode(response.json()["data"][0]["b64_json"])
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderUnavailable("openai returned no image") from None

    def generate(self, prompt: str) -> bytes:
        return self._post("/v1/images/generations", json={
            "model": self.model, "prompt": prompt, "size": _SIZE, "background": "transparent", "n": 1,
            "output_format": "png"})

    def edit(self, image: bytes, prompt: str) -> bytes:
        return self._post("/v1/images/edits", files={"image": ("neutral.png", image, "image/png")},
                          data={"model": self.model, "prompt": prompt, "size": _SIZE, "background": "transparent"})


class FakeImages:
    """Deterministic 512 px stand-ins, drawn locally: no network, no quota."""

    name = "fake"

    def _draw(self, prompt: str, *, talking: bool) -> bytes:
        from PIL import Image, ImageDraw

        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((96, 64, 416, 448), fill=(digest[0], digest[1], digest[2], 255), outline=(255, 255, 255, 255),
                     width=12)
        draw.ellipse((190, 220, 214, 244), fill=(40, 40, 40, 255))
        draw.ellipse((298, 220, 322, 244), fill=(40, 40, 40, 255))
        if talking:
            draw.ellipse((226, 290, 286, 350), fill=(90, 20, 30, 255))
        else:
            draw.line((230, 310, 282, 310), fill=(40, 40, 40, 255), width=8)
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()

    def generate(self, prompt: str) -> bytes:
        return self._draw(prompt, talking=False)

    def edit(self, image: bytes, prompt: str) -> bytes:
        return self._draw(prompt, talking="talking" in prompt)


def generator_for(provider: str, settings) -> Generator:
    if provider == "fake":
        return FakeImages()
    if provider == "openai":
        if not settings.openai_api_key:
            raise ProviderUnavailable("openai image generation is not configured", hint="set REEL_OPENAI_API_KEY")
        return OpenAIImages(settings.openai_api_key, settings.openai_image_model or "gpt-image-1")
    raise ProviderUnavailable(f"no image provider {provider!r}")


def trim_png(png: bytes) -> bytes:
    """Crop to the alpha bounding box, at most IMAGE_MAX_SIDE on the longest side."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as img:
        img = img.convert("RGBA")
    box = img.getchannel("A").getbbox()
    if box:
        img = img.crop(box)
    img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _clean(data: bytes) -> bytes:
    return trim_png(_normalise(data))


def _public_host(url: str, path: str) -> None:
    parts = urlsplit(url)
    if not parts.hostname:
        raise InvalidInput("the pose URL has no host", details={"path": path})
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        raise InvalidInput(f"cannot resolve {parts.hostname}", details={"path": path}) from None
    for info in infos:
        if not ipaddress.ip_address(info[4][0].split("%", 1)[0]).is_global:
            raise InvalidInput("the pose URL points at a private address", details={"path": path})


def read_pose(ref: str, *, allow_local: bool, path: str) -> bytes:
    """A pose PNG from an http(s) URL, or a local file for local runs: normalised and trimmed."""
    if urlsplit(ref).scheme.lower() in ("http", "https"):
        if not allow_local:
            _public_host(ref, path)
        try:
            data, kind = _download(ref)
        except (ValueError, httpx.HTTPError) as exc:
            raise InvalidInput(f"could not fetch the pose image ({exc})", details={"path": path}) from None
        if kind == "image/svg+xml":
            raise InvalidInput("SVG poses are not drawn; give a PNG", details={"path": path})
    else:
        if not allow_local:
            raise InvalidInput(LOCAL_REFUSED, details={"path": path})
        file = Path(ref).expanduser()
        suffix = file.suffix.lower()
        if suffix == ".svg" or suffix not in IMAGE_SUFFIXES:
            raise InvalidInput(f"a pose is a PNG, JPEG, WebP or GIF file, not {suffix or 'no suffix'}",
                               details={"path": path})
        try:
            if file.stat().st_size > IMAGE_MAX_BYTES:
                raise InvalidInput("the pose image is larger than 8 MB", details={"path": path})
            data = file.read_bytes()
        except OSError as exc:
            raise InvalidInput(f"cannot read the pose image ({exc.strerror or exc})", details={"path": path}) from None
    try:
        return _clean(data)
    except ValueError as exc:
        raise InvalidInput(str(exc), details={"path": path}) from None


def quota_ledger(caller_root: Path, provider: str) -> QuotaLedger:
    return QuotaLedger(Path(caller_root) / "quota" / f"{provider}.json")


def make_poses(source: ImageSource, folder: Path, *, settings, allow_local: bool, ledger: QuotaLedger | None,
               path: str) -> tuple[dict[str, Path], list[str]]:
    """Write `<folder>/<pose>.png` (reusing files already there): the pose paths and the warnings."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    generator: Generator | None = None

    def call(method: str, *args) -> bytes:
        nonlocal generator
        generator = generator or generator_for(source.provider, settings)
        data = getattr(generator, method)(*args)
        if ledger is not None:
            ledger.record(1)
        try:
            return _clean(data)
        except ValueError as exc:
            raise ProviderUnavailable(f"{source.provider} returned an unreadable image ({exc})") from None

    neutral = folder / "neutral.png"
    if not neutral.exists():
        if source.provider:
            prompt = STYLE + source.description + (f" (take {source.variant})" if source.variant else "")
            data = call("generate", prompt)
        else:
            data = read_pose(source.poses["neutral"], allow_local=allow_local, path=f"{path}.poses.neutral")
        _write_atomic(neutral, data)
    paths, warnings = {"neutral": neutral}, []
    base = neutral.read_bytes() if source.provider else b""
    for pose in POSES[1:]:
        target = folder / f"{pose}.png"
        if not target.exists():
            try:
                if source.provider:
                    data = call("edit", base, POSE_PROMPTS[pose])
                elif pose in source.poses:
                    data = read_pose(source.poses[pose], allow_local=allow_local, path=f"{path}.poses.{pose}")
                else:
                    continue
            except ReelError as exc:
                warnings.append(f"{path}: the {pose} pose failed ({exc.message}); using neutral")
                continue
            _write_atomic(target, data)
        paths[pose] = target
    return paths, warnings
