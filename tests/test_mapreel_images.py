"""mapreel's images stage: Commons fetches, PNG normalising and credits; a bad image is only ever a badge."""

import dataclasses
import io
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from reelmachine.config import get_settings
from reelmachine.core.errors import InvalidInput
from reelmachine.recipes.mapreel import commons, geodata, stages
from reelmachine.recipes.mapreel.models import GeoOutput, MapReelInputs, ResolvedSegment


class _Token:
    def raise_if_cancelled(self):
        return None


def _bytes(fmt, size, mode="RGB", colour=(200, 30, 30)):
    out = io.BytesIO()
    Image.new(mode, size, colour).save(out, format=fmt)
    return out.getvalue()


JPEG = _bytes("JPEG", (3000, 1500))
ALPHA_PNG = _bytes("PNG", (64, 64), "RGBA", (10, 200, 10, 128))
WEBP = _bytes("WEBP", (1600, 900))
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'


def _settings(tmp_path):
    return dataclasses.replace(get_settings(), workdir=tmp_path / "work", outdir=tmp_path / "out",
                               shared_cache=tmp_path / "shared")


def _payload(images):
    inputs = MapReelInputs.model_validate({"segments": [{
        "kind": "country", "country": "ITA", "narration": "Italy.",
        "items": [{"name": f"item{i}", "image": ref} for i, ref in enumerate(images)]}]})
    seg = ResolvedSegment(id="seg-1", index=0, kind="country", codes=["ITA"], names={"ITA": "Italy"},
                          colours={"ITA": "#8a2be2"})
    return GeoOutput(inputs=inputs, segments=[seg], world="w.jpg", layers={"seg-1": "d.jpg"},
                     detail_bboxes={"seg-1": [0, 0, 1, 1]}, frames={"seg-1": {"x": 0.5, "y": 0.5, "s": 2000}})


def _run(tmp_path, images, *, job="job"):
    ctx = SimpleNamespace(workdir=tmp_path / job, cancel=_Token(), config=_settings(tmp_path))
    return stages.ImagesStage().run(ctx, _payload(images))


def _hit(name, *, mime="image/png", width=800, height=600, licence="CC BY-SA 4.0", permitted=True):
    return commons.ImageHit(
        id=f"commons:File:{name}", title=name, thumb_url=f"https://upload.wikimedia.org/thumb/1024px-{name}.png",
        url=f"https://upload.wikimedia.org/{name}", width=width, height=height, mime=mime, licence=licence,
        licence_url="https://creativecommons.org/licenses/by-sa/4.0", attribution="Jane Doe",
        permitted=permitted, restrictions="trademarked")


@pytest.fixture
def web(monkeypatch):
    """A fake web: url -> (bytes, content type); every download is recorded."""
    served: dict[str, tuple[bytes, str]] = {}
    fetched: list[str] = []

    def download(ref, **kwargs):
        fetched.append(ref)
        if ref not in served:
            raise ValueError("404 Not Found")
        return served[ref]

    monkeypatch.setattr(stages, "_download", download)
    return SimpleNamespace(served=served, fetched=fetched)


@pytest.fixture
def files(monkeypatch):
    """A fake Commons: name -> ImageHit; every lookup is recorded."""
    known: dict[str, commons.ImageHit] = {}
    asked: list[str] = []

    def info(file_id, *, width=1024, fetch=None):
        asked.append(file_id)
        assert width == 1024
        name = file_id.removeprefix("commons:File:")
        if name not in known:
            raise InvalidInput(f"Commons has no file {name!r}", hint="search again")
        return known[name]

    monkeypatch.setattr(commons, "info", info)
    return SimpleNamespace(known=known, asked=asked)


def test_images_are_normalised_to_png_within_1024(tmp_path, web):
    web.served["https://x.test/big.jpg"] = (JPEG, "image/jpeg")
    web.served["https://x.test/a.webp"] = (WEBP, "image/webp")
    small = tmp_path / "small.png"
    small.write_bytes(ALPHA_PNG)
    out = _run(tmp_path, ["https://x.test/big.jpg", str(small), "https://x.test/a.webp"])
    assert out.warnings == []
    sizes = {}
    for key, path in out.images.items():
        path = Path(path)
        assert path.suffix == ".png" and path.parent == tmp_path / "job" / "images"
        with Image.open(path) as img:
            assert img.format == "PNG" and img.mode == "RGBA"
            sizes[key] = img.size
            if key == "seg-1/1":
                assert img.getpixel((5, 5)) == (10, 200, 10, 128)  # alpha kept
    assert sizes == {"seg-1/0": (1024, 512), "seg-1/1": (64, 64), "seg-1/2": (1024, 576)}
    again = _run(tmp_path, ["https://x.test/big.jpg"], job="job2")  # named by content
    assert Path(again.images["seg-1/0"]).name == Path(out.images["seg-1/0"]).name


def test_a_commons_image_is_fetched_once_and_credited(tmp_path, web, files):
    files.known["Nike.png"] = _hit("Nike.png")
    files.known["Map.svg"] = _hit("Map.svg", mime="image/svg+xml")
    files.known["Huge.jpg"] = _hit("Huge.jpg", mime="image/jpeg", width=4000, height=2000)
    web.served["https://upload.wikimedia.org/Nike.png"] = (ALPHA_PNG, "image/png")
    web.served["https://upload.wikimedia.org/thumb/1024px-Map.svg.png"] = (_bytes("PNG", (1024, 700)), "image/png")
    web.served["https://upload.wikimedia.org/thumb/1024px-Huge.jpg.png"] = (_bytes("PNG", (1024, 512)), "image/png")
    refs = ["commons:File:Nike.png", "commons:File:Map.svg", "commons:File:Huge.jpg"]
    out = _run(tmp_path, refs)
    assert out.warnings == [] and all(out.images.values())
    # an SVG and an original over 2048 px come as the 1024 px PNG thumb; anything else as the original
    assert web.fetched == ["https://upload.wikimedia.org/Nike.png",
                           "https://upload.wikimedia.org/thumb/1024px-Map.svg.png",
                           "https://upload.wikimedia.org/thumb/1024px-Huge.jpg.png"]
    assert out.image_credits["seg-1/0"] == {
        "name": "item0", "source": "commons", "id_or_path": "commons:File:Nike.png", "licence": "CC BY-SA 4.0",
        "licence_url": "https://creativecommons.org/licenses/by-sa/4.0", "attribution": "Jane Doe",
        "restrictions": "trademarked"}
    assert set(out.image_credits) == {"seg-1/0", "seg-1/1", "seg-1/2"}
    assert list((geodata.geo_cache(_settings(tmp_path)) / "images").glob("*.png"))

    web.fetched.clear()
    files.asked.clear()
    again = _run(tmp_path, refs, job="job2")  # a second reel reuses the shared cache
    assert web.fetched == [] and files.asked == []
    assert again.image_credits == out.image_credits and again.warnings == []
    for key in out.images:
        assert Path(again.images[key]).read_bytes() == Path(out.images[key]).read_bytes()
        assert Path(again.images[key]).parent == tmp_path / "job2" / "images"


def test_a_non_free_commons_file_becomes_a_badge_with_a_warning(tmp_path, web, files):
    files.known["Logo.png"] = _hit("Logo.png", licence="Fair use", permitted=False)
    out = _run(tmp_path, ["commons:File:Logo.png"])
    assert out.images == {"seg-1/0": None} and out.image_credits == {}
    assert web.fetched == []
    assert len(out.warnings) == 1
    warning = out.warnings[0]
    assert "seg-1" in warning and "item0" in warning and "Fair use" in warning and "badge" in warning


def test_a_caller_image_is_marked_caller_supplied(tmp_path, web):
    path = tmp_path / "logo.png"
    path.write_bytes(ALPHA_PNG)
    web.served["https://x.test/a.webp"] = (WEBP, "image/webp")
    out = _run(tmp_path, [str(path), "https://x.test/a.webp"])
    assert out.image_credits == {
        "seg-1/0": {"name": "item0", "source": "caller", "id_or_path": str(path), "licence": "caller-supplied",
                    "licence_url": None, "attribution": "supplied by the script author", "restrictions": None},
        "seg-1/1": {"name": "item1", "source": "caller", "id_or_path": "https://x.test/a.webp",
                    "licence": "caller-supplied", "licence_url": None,
                    "attribution": "supplied by the script author", "restrictions": None},
    }


def test_a_broken_image_never_fails_the_job(tmp_path, monkeypatch, files):
    class NotFound:
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            request = httpx.Request("GET", "https://x.test/gone.png")
            raise httpx.HTTPStatusError("404 Not Found", request=request,
                                        response=httpx.Response(404, request=request))

    class Garbage:
        headers = {"content-type": "image/png"}

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"\x89PNG not really a png"

    class Svg(Garbage):
        headers = {"content-type": "image/svg+xml"}

        def iter_bytes(self):
            yield SVG

    responses = {"https://x.test/gone.png": NotFound(), "https://x.test/junk.png": Garbage(),
                 "https://x.test/a.svg": Svg()}

    @contextmanager
    def stream(method, url, **kwargs):
        yield responses[url]

    monkeypatch.setattr(stages.httpx, "stream", stream)
    svg = tmp_path / "logo.svg"
    svg.write_bytes(SVG)
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"garbage")
    refs = ["https://x.test/gone.png", "https://x.test/junk.png", "https://x.test/a.svg", str(svg), str(junk),
            "commons:File:Missing.png"]
    out = _run(tmp_path, refs)
    assert out.images == {f"seg-1/{j}": None for j in range(len(refs))}
    assert out.image_credits == {}
    assert len(out.warnings) == len(refs)
    for j, warning in enumerate(out.warnings):
        assert warning.startswith(f"seg-1 item{j}:") and "badge" in warning
    assert "404" in out.warnings[0]
    assert "SVG" in out.warnings[2] and "SVG" in out.warnings[3]
    assert [p for p in (tmp_path / "job").rglob("*") if p.is_file()] == []  # nothing half-made is left


def test_a_huge_image_is_refused_before_it_is_decoded(tmp_path, web, monkeypatch):
    out = io.BytesIO()
    Image.new("1", (7000, 7000), 1).save(out, format="PNG")  # tiny on disk, 49 M pixels
    assert len(out.getvalue()) < 1024 * 1024
    web.served["https://x.test/bomb.png"] = (out.getvalue(), "image/png")
    loaded = []
    real_load = Image.Image.load
    monkeypatch.setattr(Image.Image, "load", lambda self: loaded.append(self.size) or real_load(self))
    result = _run(tmp_path, ["https://x.test/bomb.png"])
    assert result.images == {"seg-1/0": None}
    assert len(result.warnings) == 1 and "item0" in result.warnings[0] and "badge" in result.warnings[0]
    assert "pixels" in result.warnings[0] and (7000, 7000) not in loaded


def test_a_big_jpeg_is_decoded_at_a_reduced_scale(tmp_path, web, monkeypatch):
    web.served["https://x.test/big.jpg"] = (JPEG, "image/jpeg")
    from PIL import JpegImagePlugin

    drafts = []
    real_draft = JpegImagePlugin.JpegImageFile.draft
    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", lambda self, mode, size: drafts.append((mode, size))
                        or real_draft(self, mode, size))
    out = _run(tmp_path, ["https://x.test/big.jpg"])
    assert out.warnings == [] and drafts == [("RGB", (1024, 1024))]
    with Image.open(out.images["seg-1/0"]) as img:
        assert max(img.size) == 1024


def test_an_original_over_8_mb_falls_back_to_the_thumb(tmp_path, monkeypatch, files):
    files.known["Big.png"] = _hit("Big.png", width=1800, height=900)
    fetched = []

    def download(ref, **kwargs):
        fetched.append(ref)
        if ref == "https://upload.wikimedia.org/Big.png":
            raise stages._TooLarge("larger than 8 MB")
        return ALPHA_PNG, "image/png"

    monkeypatch.setattr(stages, "_download", download)
    out = _run(tmp_path, ["commons:File:Big.png"])
    assert out.warnings == [] and out.images["seg-1/0"]
    assert fetched == ["https://upload.wikimedia.org/Big.png", "https://upload.wikimedia.org/thumb/1024px-Big.png.png"]


def test_a_damaged_cache_entry_is_fetched_again(tmp_path, web, files):
    files.known["Nike.png"] = _hit("Nike.png")
    web.served["https://upload.wikimedia.org/Nike.png"] = (ALPHA_PNG, "image/png")
    first = _run(tmp_path, ["commons:File:Nike.png"])
    (meta,) = (geodata.geo_cache(_settings(tmp_path)) / "images").glob("*.json")
    meta.write_text("{not json", encoding="utf-8")
    files.asked.clear()
    again = _run(tmp_path, ["commons:File:Nike.png"], job="job2")
    assert files.asked == ["commons:File:Nike.png"]
    assert again.warnings == [] and again.image_credits == first.image_credits


def test_a_local_file_over_8_mb_is_a_badge(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "IMAGE_MAX_BYTES", 32)
    path = tmp_path / "logo.png"
    path.write_bytes(ALPHA_PNG)
    out = _run(tmp_path, [str(path)])
    assert out.images == {"seg-1/0": None} and "8 MB" in out.warnings[0]


def test_a_slow_download_is_cut_off(tmp_path, monkeypatch):
    class Slow:
        headers = {"content-type": "image/png"}

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            while True:
                yield b"x"

    @contextmanager
    def stream(method, url, **kwargs):
        yield Slow()

    monkeypatch.setattr(stages.httpx, "stream", stream)
    monkeypatch.setattr(stages, "IMAGE_DEADLINE_S", 0.05)
    out = _run(tmp_path, ["https://x.test/slow.png"])
    assert out.images == {"seg-1/0": None} and "too long" in out.warnings[0]


def test_without_pillow_every_image_is_a_badge_and_nothing_is_fetched(tmp_path, web, files, monkeypatch):
    monkeypatch.setattr(stages, "_have_pillow", lambda: False)
    path = tmp_path / "logo.png"
    path.write_bytes(ALPHA_PNG)
    out = _run(tmp_path, ["https://x.test/a.png", "commons:File:Nike.png", str(path)])
    assert out.images == {f"seg-1/{j}": None for j in range(3)}
    assert web.fetched == [] and files.asked == []
    assert len(out.warnings) == 3 and all("Pillow" in w and "badge" in w for w in out.warnings)
