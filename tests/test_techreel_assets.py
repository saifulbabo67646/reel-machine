"""techreel's agent SVG and caller images: the guarded fetch, the SVG safety check, the assets stage and the
project that carries the files."""

import dataclasses
import hashlib
import io
import re

import pytest
from PIL import Image

from reelmachine.config import get_settings
from reelmachine.core.assets import AssetStore
from reelmachine.core.errors import InvalidInput
from reelmachine.core.job import Job, JobRequest
from reelmachine.core.stage import StageContext, StaticProviders
from reelmachine.recipes.dialoguereel import images
from reelmachine.recipes.techreel import assets, compose
from reelmachine.recipes.techreel.markup import units
from reelmachine.recipes.techreel.models import ScriptOutput, TechReelInputs
from reelmachine.recipes.techreel.schedule import schedule
from reelmachine.recipes.techreel.stages import AssetsStage

DRAWING = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
           '<defs><linearGradient id="a"><stop offset="0" stop-color="#fff"/></linearGradient></defs>'
           '<rect id="body" width="100" height="60" fill="url(#a)"/></svg>')


def _png(size=(64, 48), colour=(30, 120, 200, 255)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", size, colour).save(out, format="PNG")
    return out.getvalue()


def _inputs(visuals, hero=None, **kw):
    beats = [{"lines": [{"say": "one two three"}, {"say": "four five"}], "visuals": visuals}]
    return TechReelInputs.model_validate({"title": "t", "beats": beats, **({"hero": hero} if hero else {}), **kw})


def _doc(inputs, names=None):
    plan = schedule(inputs.beats, [[600, 500]])
    times = [[[(k * 100, (k + 1) * 100) for k in range(len(units(ln.caption, inputs.language)))]
              for ln in b.lines] for b in inputs.beats]
    return compose.reel_document(inputs, plan, times, names or {})


def _name(src):
    return f"img-{hashlib.sha256(src.encode()).hexdigest()[:16]}.png"


# ------------------------------------------------------------ images


def test_a_local_image_is_written_under_its_hash(tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(_png((2000, 1000), (0, 0, 0, 0)))     # transparent margins are kept: no alpha trim
    logo = tmp_path / "logo.jpg"
    Image.new("RGB", (40, 30), (200, 10, 10)).save(logo, format="JPEG")
    inputs = _inputs([{"kind": "image", "src": str(shot)}, {"kind": "image", "src": str(shot)}],
                     hero={"kind": "image", "src": str(logo)})
    names = assets.fetch_images(inputs, tmp_path / "out", allow_local=True)
    assert names == {str(shot): _name(str(shot)), str(logo): _name(str(logo))}
    with Image.open(tmp_path / "out" / names[str(shot)]) as img:
        assert img.format == "PNG" and img.size == (1600, 800)
    with Image.open(tmp_path / "out" / names[str(logo)]) as img:
        assert img.format == "PNG" and img.size == (40, 30)


def test_no_images_writes_nothing(tmp_path):
    assert assets.fetch_images(_inputs([{"kind": "title", "text": "x"}]), tmp_path / "out", allow_local=True) == {}


def test_a_local_path_is_refused_for_a_remote_caller(tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(_png())
    inputs = _inputs([{"kind": "title", "text": "x"}, {"kind": "image", "src": str(shot)}])
    with pytest.raises(InvalidInput) as exc:
        assets.fetch_images(inputs, tmp_path / "out", allow_local=False)
    assert exc.value.details["path"] == "beats[0].visuals[1].src"
    assert exc.value.message == images.LOCAL_REFUSED
    with pytest.raises(InvalidInput) as exc:
        assets.fetch_images(_inputs([], hero={"kind": "image", "src": "logo.png"}), tmp_path / "o", allow_local=False)
    assert exc.value.details["path"] == "hero.src"


def test_a_private_address_is_refused_in_guarded_mode(tmp_path, monkeypatch):
    lookups = []

    def getaddrinfo(host, port, *a, **k):
        lookups.append(host)
        return [(2, 1, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(images.socket, "getaddrinfo", getaddrinfo)
    inputs = _inputs([{"kind": "image", "src": "http://127.0.0.1/x.png"}])
    with pytest.raises(InvalidInput) as exc:
        assets.fetch_images(inputs, tmp_path / "out", allow_local=False)
    assert exc.value.message == "could not fetch the image (not a public address)"
    assert exc.value.details == {"path": "beats[0].visuals[0].src"} and lookups == ["127.0.0.1"]
    assert not list((tmp_path / "out").glob("*.png"))


def test_read_image_names_its_noun_and_can_keep_margins(tmp_path):
    framed = tmp_path / "f.png"
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    img.paste((255, 0, 0, 255), (40, 40, 60, 60))
    img.save(framed, format="PNG")
    kept = images.read_image(str(framed), allow_local=True, path="p", what="image", trim=False, max_side=50)
    with Image.open(io.BytesIO(kept)) as out:
        assert out.size == (50, 50)
    with Image.open(io.BytesIO(images.read_image(str(framed), allow_local=True, path="p"))) as out:
        assert out.size == (20, 20)                          # the default trims, as read_pose always has
    (tmp_path / "a.svg").write_text("<svg/>")
    with pytest.raises(InvalidInput) as exc:
        images.read_image(str(tmp_path / "a.svg"), allow_local=True, path="p", what="image")
    assert exc.value.message == "an image is a PNG, JPEG, WebP or GIF file, not .svg"
    with pytest.raises(InvalidInput) as exc:
        images.read_pose(str(tmp_path / "a.svg"), allow_local=True, path="p")
    assert exc.value.message == "a pose is a PNG, JPEG, WebP or GIF file, not .svg"


def test_check_sources_never_fetches(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "_guarded_download", lambda ref: pytest.fail("fetched"))
    monkeypatch.setattr(images, "_download", lambda ref: pytest.fail("fetched"))
    ok = _inputs([{"kind": "image", "src": "https://example.com/a.png"}])
    assets.check_sources(ok, allow_local=False)
    with pytest.raises(InvalidInput) as exc:
        assets.check_sources(_inputs([{"kind": "image", "src": "a.png"}]), allow_local=False)
    assert exc.value.details["path"] == "beats[0].visuals[0].src"
    assets.check_sources(_inputs([{"kind": "image", "src": "a.png"}]), allow_local=True)
    with pytest.raises(InvalidInput) as exc:
        assets.check_sources(_inputs([{"kind": "image", "src": "ftp://e.com/a.png"}]), allow_local=True)
    assert exc.value.details["path"] == "beats[0].visuals[0].src"


# ------------------------------------------------------------ svg


@pytest.mark.parametrize("bad, element", [
    ('<script>alert(1)</script>', "script"), ('<g onload="x()"/>', "onload"),
    ('<use href="https://e.com/a.svg#x"/>', "href"), ('<image href="file:///etc/passwd"/>', "image"),
    ('<foreignObject/>', "foreignObject"), ('<style>@import url(https://e.com/x.css);</style>', "style"),
    ('<g style="fill:url(https://e.com/x.png)"/>', "style"),
    ('<image href="data:image/svg+xml;base64,PHN2Zy8+"/>', "image"),
])
def test_unsafe_svg_is_refused_naming_its_path(bad, element):
    svg = DRAWING.replace("</svg>", bad + "</svg>")
    with pytest.raises(InvalidInput) as exc:
        assets.check_svgs(_inputs([{"kind": "title", "text": "x"}, {"kind": "svg", "svg": svg}]))
    assert exc.value.details == {"path": "beats[0].visuals[1].svg", "element": element}
    with pytest.raises(InvalidInput) as exc:
        assets.check_svgs(_inputs([], hero={"kind": "svg", "svg": svg}))
    assert exc.value.details["path"] == "hero.svg"


def test_a_drawing_is_refused_as_a_drawing_and_a_character_as_a_character():
    from reelmachine.recipes.dialoguereel import svg as dsvg

    bad = DRAWING.replace("</svg>", "<script/></svg>")
    with pytest.raises(InvalidInput) as exc:
        assets.check_svgs(_inputs([{"kind": "svg", "svg": bad}]))
    assert exc.value.message == "<script> is not allowed in a drawing" and "character" not in exc.value.hint
    with pytest.raises(InvalidInput) as exc:
        _doc(_inputs([{"kind": "svg", "svg": DRAWING.replace("</svg>", "<!-- x --></svg>")}]))
    assert "not allowed in a drawing" in exc.value.message
    with pytest.raises(InvalidInput) as exc:
        dsvg.check_svg(bad.replace('viewBox="0 0 100 60"', 'viewBox="0 0 400 400"'), path="p")
    assert exc.value.message == "<script> is not allowed in a character"


def test_any_safe_drawing_passes():
    png = 'data:image/png;base64,iVBORw0KGgo='
    assets.check_svgs(_inputs([{"kind": "svg", "svg": DRAWING.replace("</svg>", f'<image href="{png}"/></svg>')}],
                              hero={"kind": "svg", "svg": DRAWING}))
    for broken in ('<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>', '<div/>', "<svg"):
        with pytest.raises(InvalidInput):
            assets.check_svgs(_inputs([{"kind": "svg", "svg": broken}]))


def test_two_drawings_with_one_id_are_scoped_apart():
    inputs = _inputs([{"kind": "svg", "svg": DRAWING}, {"kind": "svg", "svg": DRAWING}],
                     hero={"kind": "svg", "svg": DRAWING})
    doc = _doc(inputs)
    first, second = (v["svg"] for v in doc["beats"][0]["visuals"])
    ids = [set(re.findall(r' id="([^"]+)"', s)) for s in (first, second, doc["hero"]["svg"])]
    assert ids[0] and not ids[0] & ids[1] and not ids[0] & ids[2] and not ids[1] & ids[2]
    assert "url(#a)" not in first and re.search(r'fill="url\(#[^"]+\.a\)"', first)
    assert "<script" not in first and first.startswith("<svg")


# ------------------------------------------------------------ the document and the project


def test_image_visuals_carry_their_file():
    inputs = _inputs([{"kind": "image", "src": "https://e.com/a.png", "highlight": [0.1, 0.2, 0.3, 0.4]}],
                     hero={"kind": "image", "src": "https://e.com/h.png"})
    names = {"https://e.com/a.png": "img-a.png", "https://e.com/h.png": "img-h.png"}
    doc = _doc(inputs, names)
    v = doc["beats"][0]["visuals"][0]
    assert v["file"] == "assets/img-a.png" and list(v["highlight"]) == [0.1, 0.2, 0.3, 0.4] and "src" not in v
    assert doc["hero"]["file"] == "assets/img-h.png" and "src" not in doc["hero"]


def test_an_image_with_no_file_is_an_error_not_a_blank():
    with pytest.raises(ValueError, match="https://e.com/a.png"):
        _doc(_inputs([{"kind": "image", "src": "https://e.com/a.png"}]))
    with pytest.raises(ValueError, match="h.png"):
        _doc(_inputs([], hero={"kind": "image", "src": "https://e.com/h.png"}))


def test_the_creator_avatar_is_fetched_like_an_image(tmp_path):
    face = tmp_path / "face.png"
    face.write_bytes(_png((300, 300)))
    inputs = _inputs([{"kind": "follow"}], creator={"name": "Aryan", "handle": "@aryan", "avatar": str(face)})
    names = assets.fetch_images(inputs, tmp_path / "out", allow_local=True)
    assert names == {str(face): _name(str(face))}
    doc = _doc(inputs, names)
    assert doc["creator"] == {"name": "Aryan", "handle": "@aryan", "avatar": f"assets/{_name(str(face))}"}
    assert compose.asset_files(doc) == [_name(str(face))]
    with pytest.raises(InvalidInput) as exc:
        assets.check_sources(inputs, allow_local=False)
    assert exc.value.details["path"] == "creator.avatar"
    with pytest.raises(ValueError):
        _doc(inputs)


def test_the_project_carries_its_assets_and_the_key_their_bytes(tmp_path):
    folder = tmp_path / "assets"
    folder.mkdir()
    (folder / "img-a.png").write_bytes(_png())
    (folder / "img-unused.png").write_bytes(_png())
    inputs = _inputs([{"kind": "image", "src": "https://e.com/a.png"}])
    doc = _doc(inputs, {"https://e.com/a.png": "img-a.png"})
    out = compose.write_project(doc, tmp_path / "proj", folder)
    assert sorted(p.name for p in (out / "assets").iterdir()) == ["img-a.png"]
    k1 = compose.project_key(doc, folder)
    assert compose.project_key(doc, folder) == k1
    (folder / "img-a.png").write_bytes(_png(colour=(1, 2, 3, 255)))
    assert compose.project_key(doc, folder) != k1
    (folder / "img-a.png").unlink()
    with pytest.raises(FileNotFoundError):
        compose.write_project(doc, tmp_path / "proj", folder)


# ------------------------------------------------------------ the stage


def _ctx(tmp_path, local_files=True):
    settings = dataclasses.replace(get_settings(), workdir=tmp_path / "w",
                                   shared_cache=tmp_path / "shared", local_files=local_files)
    return StageContext(job=Job(id="t", recipe="techreel", request=JobRequest(recipe="techreel")),
                        workdir=tmp_path / "stage", assets=AssetStore(tmp_path / "store"), config=settings,
                        providers=StaticProviders({}))


def test_the_assets_stage_fetches_once_per_url(tmp_path, monkeypatch):
    calls = []

    def fake_read(ref, **kw):
        calls.append((ref, kw["what"], kw["trim"], kw["max_side"]))
        return _png()

    monkeypatch.setattr(assets, "read_image", fake_read)
    inputs = _inputs([{"kind": "image", "src": "https://e.com/a.png"}, {"kind": "svg", "svg": DRAWING}])
    out = AssetsStage().run(_ctx(tmp_path), ScriptOutput(inputs=inputs))
    name = _name("https://e.com/a.png")
    assert out.images == {"https://e.com/a.png": name} and out.script.inputs == inputs
    assert (tmp_path / "stage" / "assets" / name).is_file() and out.folder == str(tmp_path / "stage" / "assets")
    assert calls == [("https://e.com/a.png", "image", False, 1600)]
    assert (tmp_path / "w" / "cache" / "cache" / "techreel" / "images" / name).is_file()
    AssetsStage().run(_ctx(tmp_path), ScriptOutput(inputs=inputs))
    assert len(calls) == 1                                    # the caller's cache served the second run


def test_the_assets_stage_refuses_unsafe_svg_and_remote_paths(tmp_path):
    bad = _inputs([{"kind": "svg", "svg": DRAWING.replace("</svg>", "<script/></svg>")}])
    with pytest.raises(InvalidInput) as exc:
        AssetsStage().run(_ctx(tmp_path), ScriptOutput(inputs=bad))
    assert exc.value.stage == "assets" and exc.value.details["path"] == "beats[0].visuals[0].svg"
    local = _inputs([{"kind": "image", "src": str(tmp_path / "x.png")}])
    with pytest.raises(InvalidInput):
        AssetsStage().run(_ctx(tmp_path, local_files=False), ScriptOutput(inputs=local))


def test_the_recipe_runs_assets_between_script_and_narration():
    from reelmachine.recipes.techreel.recipe import TechReelRecipe

    stages = TechReelRecipe.spec.stages
    assert [s.stage.id for s in stages][:3] == ["script", "assets", "narration"]
    assert stages[1].feeds == "script" and stages[2].feeds == "assets"


def test_the_probe_checks_drawings_and_sources_without_fetching(monkeypatch):
    from reelmachine.core.recipe import ProbeContext
    from reelmachine.recipes.techreel.recipe import TechReelRecipe

    monkeypatch.setattr(images, "_guarded_download", lambda ref: pytest.fail("fetched"))
    settings = dataclasses.replace(get_settings(), local_files=False)

    def check(inputs):
        report = TechReelRecipe().probe(inputs, ProbeContext(config=settings))
        return next(c for c in report.checks if c.name == "assets")

    good = check(_inputs([{"kind": "image", "src": "https://e.com/a.png"}, {"kind": "svg", "svg": DRAWING}]))
    assert good.ok and good.detail == "1 image to fetch"
    bad = check(_inputs([{"kind": "image", "src": "/tmp/a.png"}]))
    assert not bad.ok and bad.detail.startswith("beats[0].visuals[0].src: ")
    unsafe = check(_inputs([], hero={"kind": "svg", "svg": DRAWING.replace("</svg>", "<script/></svg>")}))
    assert not unsafe.ok and unsafe.detail.startswith("hero.svg: ")
