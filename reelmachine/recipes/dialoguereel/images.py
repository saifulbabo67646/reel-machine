"""Image characters: PNG poses generated from a description, or supplied by the caller."""

from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import socket
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from reelmachine.core.errors import InvalidInput, ProviderUnavailable, ReelError
from reelmachine.nadeshiko import QuotaLedger
from reelmachine.recipes.dialoguereel.models import ImageSource
from reelmachine.recipes.mapreel.geodata import USER_AGENT
from reelmachine.recipes.mapreel.stages import (
    IMAGE_DEADLINE_S,
    IMAGE_MAX_BYTES,
    IMAGE_MAX_SIDE,
    IMAGE_SUFFIXES,
    IMAGE_TYPES,
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
        self._owned = client is None
        self.client = client or httpx.Client(timeout=180)
        self.base_url = base_url.rstrip("/")

    def close(self) -> None:
        if self._owned:
            self.client.close()

    def __enter__(self) -> OpenAIImages:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _post(self, route: str, **kwargs) -> bytes:
        try:
            response = self.client.post(f"{self.base_url}{route}",
                                        headers={"Authorization": f"Bearer {self._key}"}, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"openai could not be reached ({type(exc).__name__})") from None
        if response.status_code != 200:
            hint = {400: "the provider's safety system may have refused the description; rephrase it",
                    401: "check REEL_OPENAI_API_KEY", 403: "check REEL_OPENAI_API_KEY and the model's access",
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


def trim_png(png: bytes, max_side: int = IMAGE_MAX_SIDE) -> bytes:
    """Crop to the alpha bounding box, at most `max_side` on the longest side."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as img:
        img = img.convert("RGBA")
    box = img.getchannel("A").getbbox()
    if box:
        img = img.crop(box)
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


MAX_REDIRECTS = 3


class _Refused(Exception):
    """A remote fetch refused with fixed text: never the URL, a redirect target or an exception's words."""


def _fetch_client() -> httpx.Client:
    return httpx.Client(timeout=30, follow_redirects=False, trust_env=False)


def _public_address(host: str, port: int | None) -> str:
    """The host's first address, provided every address it resolves to is global."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        raise _Refused("cannot resolve the host") from None
    addresses = [info[4][0].split("%", 1)[0] for info in infos]
    if not addresses or not all(ipaddress.ip_address(a).is_global for a in addresses):
        raise _Refused("not a public address")
    return addresses[0]


def _guarded_download(ref: str) -> tuple[bytes, str]:
    """An http(s) image for a remote caller: every hop resolved once, public-only and pinned to that address."""
    url = httpx.URL(ref)
    with _fetch_client() as client:
        for _ in range(MAX_REDIRECTS + 1):
            if url.scheme not in ("http", "https") or not url.raw_host:
                raise _Refused("not an http(s) URL")
            host = url.raw_host.decode("ascii")
            address = _public_address(host, url.port or (443 if url.scheme == "https" else 80))
            extensions = {"sni_hostname": host} if url.scheme == "https" else {}
            try:
                with client.stream("GET", url.copy_with(host=address), extensions=extensions,
                                   headers={"Host": url.netloc.decode("ascii"), "User-Agent": USER_AGENT}) as response:
                    if response.is_redirect:
                        url = url.join(response.headers["location"])
                        continue
                    if response.status_code != 200:
                        raise _Refused(f"HTTP {response.status_code}")
                    kind = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if kind not in IMAGE_TYPES:
                        raise _Refused("not an image")
                    chunks, size, deadline = [], 0, time.monotonic() + IMAGE_DEADLINE_S
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > IMAGE_MAX_BYTES:
                            raise _Refused("larger than 8 MB")
                        if time.monotonic() > deadline:
                            raise _Refused("the download took too long")
                        chunks.append(chunk)
                    return b"".join(chunks), kind
            except (httpx.HTTPError, httpx.InvalidURL):
                raise _Refused("could not connect") from None
    raise _Refused("too many redirects")


def read_image(ref: str, *, allow_local: bool, path: str, what: str = "pose image", trim: bool = True,
               max_side: int = IMAGE_MAX_SIDE) -> bytes:
    """A raster image from an http(s) URL, or a local file for local runs: normalised to a PNG at most `max_side`
    on its longest side and, with `trim`, cropped to its alpha box. `what` names it in errors ("pose image")."""
    noun = what.removesuffix(" image")       # "pose image" -> "pose", "image" -> "image"
    article = "an" if noun[:1] in "aeiou" else "a"
    if urlsplit(ref).scheme.lower() in ("http", "https"):
        try:
            data, kind = _download(ref) if allow_local else _guarded_download(ref)
        except (_Refused, ValueError, httpx.HTTPError) as exc:
            raise InvalidInput(f"could not fetch the {what} ({exc})", details={"path": path}) from None
        if kind == "image/svg+xml":
            raise InvalidInput(f"SVG {noun}s are not drawn; give a PNG", details={"path": path})
    else:
        if not allow_local:
            raise InvalidInput(LOCAL_REFUSED, details={"path": path})
        file = Path(ref).expanduser()
        suffix = file.suffix.lower()
        if suffix == ".svg" or suffix not in IMAGE_SUFFIXES:
            raise InvalidInput(f"{article} {noun} is a PNG, JPEG, WebP or GIF file, not {suffix or 'no suffix'}",
                               details={"path": path})
        try:
            if file.stat().st_size > IMAGE_MAX_BYTES:
                raise InvalidInput(f"the {what} is larger than 8 MB", details={"path": path})
            data = file.read_bytes()
        except OSError as exc:
            raise InvalidInput(f"cannot read the {what} ({exc.strerror or exc})", details={"path": path}) from None
    try:
        png = _normalise(data, max_side)
        return trim_png(png, max_side) if trim else png
    except ValueError as exc:
        raise InvalidInput(str(exc), details={"path": path}) from None


def read_pose(ref: str, *, allow_local: bool, path: str) -> bytes:
    """A pose PNG from an http(s) URL, or a local file for local runs: normalised and trimmed."""
    return read_image(ref, allow_local=allow_local, path=path)


def quota_ledger(caller_root: Path, provider: str) -> QuotaLedger:
    return QuotaLedger(Path(caller_root) / "quota" / f"{provider}.json")


def make_poses(source: ImageSource, folder: Path, *, settings, allow_local: bool, ledger: QuotaLedger | None,
               path: str) -> tuple[dict[str, Path], list[str]]:
    """Write `<folder>/<pose>.png` (reusing files already there): the pose paths and the warnings."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if source.provider:
        if all((folder / f"{pose}.png").exists() for pose in POSES):
            return {pose: folder / f"{pose}.png" for pose in POSES}, []
        generator = generator_for(source.provider, settings)
        try:
            return _generate_poses(source, folder, generator, ledger, path)
        finally:
            close = getattr(generator, "close", None)
            if close:
                close()
    neutral = folder / "neutral.png"
    if not neutral.exists():
        _write_atomic(neutral, read_pose(source.poses["neutral"], allow_local=allow_local,
                                         path=f"{path}.poses.neutral"))
    paths, warnings = {"neutral": neutral}, []
    for pose in POSES[1:]:
        target = folder / f"{pose}.png"
        if not target.exists():
            if pose not in source.poses:
                continue
            try:
                data = read_pose(source.poses[pose], allow_local=allow_local, path=f"{path}.poses.{pose}")
            except InvalidInput as exc:
                warnings.append(f"{path}: the {pose} pose failed ({exc.message}); using neutral")
                continue
            _write_atomic(target, data)
        paths[pose] = target
    return paths, warnings


def _generate_poses(source: ImageSource, folder: Path, generator: Generator, ledger: QuotaLedger | None,
                    path: str) -> tuple[dict[str, Path], list[str]]:
    def call(method: str, *args) -> bytes:
        data = getattr(generator, method)(*args)
        if ledger is not None:
            ledger.record(1)
        try:
            return _normalise(data)
        except ValueError as exc:
            raise ProviderUnavailable(f"{source.provider} returned an unreadable image ({exc})") from None

    neutral, raw = folder / "neutral.png", folder / "neutral.raw.png"
    if not neutral.exists():
        prompt = STYLE + source.description + (f" (take {source.variant})" if source.variant else "")
        base = call("generate", prompt)
        _write_atomic(raw, base)
        _write_atomic(neutral, trim_png(base))
    else:
        base = (raw if raw.exists() else neutral).read_bytes()
    paths, warnings = {"neutral": neutral}, []
    for pose in POSES[1:]:
        target = folder / f"{pose}.png"
        if not target.exists():
            try:
                data = trim_png(call("edit", base, POSE_PROMPTS[pose]))
            except ReelError as exc:
                warnings.append(f"{path}: the {pose} pose failed ({exc.message}); using neutral")
                continue
            _write_atomic(target, data)
        paths[pose] = target
    return paths, warnings
