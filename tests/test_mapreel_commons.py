import os

import pytest

from reelmachine.core.errors import InvalidInput, ProviderUnavailable
from reelmachine.recipes.mapreel import commons


def _page(n, name, *, mime="image/svg+xml", licence="CC BY-SA 4.0", artist="<a href='x'>Jane  <b>Doe</b></a>", **extra):
    meta = {
        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
        "Artist": {"value": artist},
        "Restrictions": {"value": "trademarked"},
    }
    if licence is not None:
        meta["LicenseShortName"] = {"value": licence}
    meta.update(extra)
    return str(n), {
        "pageid": n, "ns": 6, "title": f"File:{name}", "index": n,
        "imageinfo": [{
            "url": f"https://upload.wikimedia.org/{name}",
            "thumburl": f"https://upload.wikimedia.org/thumb/320px-{name}.png",
            "width": 800, "height": 600, "mime": mime, "extmetadata": meta,
        }],
    }


def _api(*pages):
    return {"query": {"pages": dict(pages)}}


def _fake(payload, seen=None):
    def fetch(params):
        if seen is not None:
            seen.append(params)
        return payload
    return fetch


def test_find_images_returns_ids_licences_and_attribution():
    hits = commons.search("nike", fetch=_fake(_api(_page(1, "Nike.svg"))))
    hit = hits[0]
    assert hit.id == "commons:File:Nike.svg"
    assert hit.title == "Nike.svg"
    assert hit.licence == "CC BY-SA 4.0" and hit.permitted
    assert hit.attribution == "Jane Doe"
    assert hit.restrictions == "trademarked"
    assert hit.thumb_url.endswith(".png") and hit.url.endswith("Nike.svg")
    assert (hit.width, hit.height, hit.mime) == (800, 600, "image/svg+xml")


def test_find_images_hides_files_without_a_free_licence():
    payload = _api(
        _page(1, "ok.png", mime="image/png"),
        _page(2, "none.png", mime="image/png", licence=None),
        _page(3, "fair.png", mime="image/png", licence="Fair use"),
        _page(4, "nonfree.png", mime="image/png", NonFree={"value": "true"}),
        _page(5, "doc.pdf", mime="application/pdf"),
    )
    assert [h.title for h in commons.search("x", fetch=_fake(payload))] == ["ok.png"]
    every = commons.search("x", include_nonfree=True, fetch=_fake(payload))
    assert [h.title for h in every] == ["ok.png", "none.png", "fair.png", "nonfree.png"]
    assert [h.permitted for h in every] == [True, False, False, False]


def test_logo_kind_biases_the_query_and_prefers_vector():
    seen = []
    payload = _api(_page(1, "a.jpg", mime="image/jpeg"), _page(2, "b.svg"), _page(3, "c.png", mime="image/png"))
    hits = commons.search("Nike", kind="logo", fetch=_fake(payload, seen))
    assert seen[0]["gsrsearch"] == "Nike logo" and seen[0]["gsrnamespace"] == 6
    assert seen[0]["iiurlwidth"] == 320
    assert [h.title for h in hits] == ["b.svg", "c.png", "a.jpg"]
    photos = commons.search("Nike", kind="photo", fetch=_fake(payload))
    assert [h.title for h in photos] == ["a.jpg", "c.png"]
    assert commons.search("Italy", kind="flag", fetch=_fake(payload, seen))
    assert seen[-1]["gsrsearch"] == "Italy flag"


def test_limit_is_clamped():
    seen = []
    payload = _api(*[_page(i, f"f{i}.png", mime="image/png") for i in range(1, 8)])
    assert len(commons.search("x", limit=3, fetch=_fake(payload, seen))) == 3
    assert seen[0]["gsrlimit"] == 6
    commons.search("x", limit=500, fetch=_fake(payload, seen))
    assert seen[1]["gsrlimit"] == 50
    assert len(commons.search("x", limit=0, fetch=_fake(payload))) == 1


def test_commons_failure_is_a_clear_provider_error(monkeypatch):
    import urllib.error

    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        raise urllib.error.URLError("down")

    monkeypatch.setattr(commons.urllib.request, "urlopen", boom)
    with pytest.raises(ProviderUnavailable) as err:
        commons.search("x")
    assert "Wikimedia Commons did not answer" in err.value.message and err.value.hint
    assert len(calls) == 2  # one retry


def test_user_agent_names_the_project(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"query": {"pages": {}}}'

    def fake(request, timeout):
        seen["ua"], seen["timeout"] = request.get_header("User-agent"), timeout
        return Resp()

    monkeypatch.setattr(commons.urllib.request, "urlopen", fake)
    assert commons.search("x") == []
    assert seen == {"ua": "reel-machine/mapreel (https://github.com/saifulbabo67646/reel-machine)", "timeout": 20}


def test_info_reads_one_file_and_refuses_a_missing_one():
    seen = []
    hit = commons.info("commons:File:Nike.svg", fetch=_fake(_api(_page(1, "Nike.svg")), seen))
    assert hit.id == "commons:File:Nike.svg"
    assert seen[0]["titles"] == "File:Nike.svg" and seen[0]["iiurlwidth"] == 1024
    commons.info("commons:File:Nike.svg", width=2000, fetch=_fake(_api(_page(1, "Nike.svg")), seen))
    assert seen[1]["iiurlwidth"] == 2000
    missing = {"query": {"pages": {"-1": {"ns": 6, "title": "File:Nope.png", "missing": ""}}}}
    with pytest.raises(InvalidInput):
        commons.info("commons:File:Nope.png", fetch=_fake(missing))


def test_free_pattern():
    for name in ("CC0", "CC BY 4.0", "CC-BY-SA-3.0", "Public domain", "PD-old-70", "GFDL", "Attribution"):
        assert commons.FREE.match(name), name
    assert not commons.FREE.match("Fair use")


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("REEL_NET_TESTS") != "1", reason="needs the network; set REEL_NET_TESTS=1")
def test_real_commons_has_a_permitted_nike_logo():
    hits = commons.search("Nike logo", kind="logo")
    assert any(h.permitted for h in hits)
