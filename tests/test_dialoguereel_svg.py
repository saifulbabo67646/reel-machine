import base64
import re

import pytest

from reelmachine.core.errors import InvalidInput
from reelmachine.recipes.dialoguereel.library import builtin_names, load_builtin
from reelmachine.recipes.dialoguereel.svg import MAX_BYTES, OPTIONAL, REQUIRED, check_svg, recolour, scope_ids

PATH = "cast.characters[0].source.svg"


def minimal_svg(extra: str = "", *, view_box: str = "0 0 400 400", drop: str = "") -> str:
    groups = "".join(f'<g id="{name}"><circle cx="200" cy="200" r="10"/></g>' for name in REQUIRED if name != drop)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}">{groups}{extra}</svg>'


def test_minimal_svg_is_valid_and_has_no_optional_groups():
    assert check_svg(minimal_svg(), path=PATH) == []


@pytest.mark.parametrize("name", ["cupcake", "mug"])
def test_each_builtin_meets_the_contract_and_has_every_optional_group(name):
    assert set(check_svg(load_builtin(name, {}), path="x")) == set(OPTIONAL)


@pytest.mark.parametrize("name", ["cupcake", "mug"])
def test_each_builtin_rests_neutral_with_alternates_hidden(name):
    svg = load_builtin(name, {})
    for hidden in ["eyes-closed", "mouth-open", "mouth-smile", "eyes-happy", "eyes-surprised", "sweat", "sparkle"]:
        assert re.search(rf'<g id="{hidden}"[^>]*display="none"', svg), hidden
    for shown in ["eyes-open", "mouth-closed", "blush"]:
        assert not re.search(rf'<g id="{shown}"[^>]*display="none"', svg), shown
    assert len(svg.encode()) <= MAX_BYTES


@pytest.mark.parametrize("arm", ["arm-left", "arm-right"])
def test_builtin_arms_carry_a_pivot(arm):
    for name in ["cupcake", "mug"]:
        assert re.search(rf'<g id="{arm}"[^>]*data-pivot="\d+,\d+"', load_builtin(name, {}))


@pytest.mark.parametrize("bad, element", [
    ('<script>alert(1)</script>', "script"), ('<g onload="x()"/>', "onload"),
    ('<use href="https://e.com/a.svg#x"/>', "href"), ('<image href="file:///etc/passwd"/>', "image"),
    ('<foreignObject/>', "foreignObject"), ('<style>@import url(https://e.com/x.css);</style>', "style"),
    ('<animate attributeName="href"/>', "animate"),
])
def test_hostile_svg_is_refused_naming_the_element(bad, element):
    with pytest.raises(InvalidInput) as err:
        check_svg(minimal_svg(extra=bad), path="cast.characters[0].source.svg")
    assert err.value.details["element"] == element and err.value.details["path"].endswith("svg")


@pytest.mark.parametrize("bad, element", [
    ('<use xlink:href="http://e.com/a.svg#x" xmlns:xlink="http://www.w3.org/1999/xlink"/>', "xlink:href"),
    ('<a href="javascript:alert(1)"><circle r="1"/></a>', "href"),
    ('<g style="fill:url(https://e.com/x.png)"/>', "style"),
    ('<g style="background:url( #ok ) , url(x.png)"/>', "style"),
    ('<style>.a{fill:url(#g)} .b{background:url(\'https://e.com/x\')}</style>', "style"),
    ('<g style="x:javascript:alert(1)"/>', "style"),
    ('<g ONCLICK="x()"/>', "ONCLICK"),
    ('<iframe/>', "iframe"), ('<object/>', "object"), ('<embed/>', "embed"),
    ('<audio/>', "audio"), ('<video/>', "video"), ('<set attributeName="href" to="x"/>', "set"),
    ('<animateTransform/>', "animateTransform"), ('<animateMotion/>', "animateMotion"),
    ('<image href="data:image/svg+xml;base64,PHN2Zy8+"/>', "image"),
    ('<x:script xmlns:x="http://www.w3.org/2000/svg"/>', "script"),
])
def test_more_hostile_svg_is_refused(bad, element):
    with pytest.raises(InvalidInput) as err:
        check_svg(minimal_svg(extra=bad), path=PATH)
    assert err.value.details["element"] == element


def test_doctype_and_entities_are_refused():
    with pytest.raises(InvalidInput) as err:
        check_svg('<!DOCTYPE svg [<!ENTITY a "b">]>' + minimal_svg(), path=PATH)
    assert err.value.details["path"] == PATH
    with pytest.raises(InvalidInput):
        check_svg('<!entity a "b">' + minimal_svg(), path=PATH)


def test_missing_required_group_is_named():
    with pytest.raises(InvalidInput) as err:
        check_svg(minimal_svg(drop="mouth-open"), path=PATH)
    assert "mouth-open" in err.value.message


def test_wrong_viewbox_and_oversize_are_refused():
    with pytest.raises(InvalidInput) as err:
        check_svg(minimal_svg(view_box="0 0 100 100"), path=PATH)
    assert "viewBox" in err.value.message
    with pytest.raises(InvalidInput) as err:
        check_svg(minimal_svg(extra="<!--" + "x" * 60_001 + "-->"), path=PATH)
    assert "60" in err.value.message


def test_non_svg_root_and_malformed_xml_are_refused():
    with pytest.raises(InvalidInput):
        check_svg('<html viewBox="0 0 400 400"/>', path=PATH)
    with pytest.raises(InvalidInput):
        check_svg("<svg viewBox='0 0 400 400'><g>", path=PATH)


def test_duplicate_ids_are_refused():
    with pytest.raises(InvalidInput) as err:
        check_svg(minimal_svg(extra='<g id="body"/>'), path=PATH)
    assert "body" in err.value.message


def test_a_data_png_image_is_allowed():
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    assert check_svg(minimal_svg(extra=f'<image href="data:image/png;base64,{png}" width="10" height="10"/>'),
                     path=PATH) == []


def test_fragment_refs_and_gradient_urls_are_allowed():
    extra = ('<defs><linearGradient id="g"><stop offset="0"/></linearGradient></defs>'
             '<style>.a{fill:url(#g)}</style><use href="#body" style="fill:url( #g )"/>')
    assert check_svg(minimal_svg(extra=extra), path=PATH) == []


def test_scope_ids_keeps_two_characters_apart():
    a, b = scope_ids(load_builtin("cupcake", {}), "kid"), scope_ids(load_builtin("mug", {}), "mom")
    ids = re.findall(r'id="([^"]+)"', a + b)
    assert len(ids) == len(set(ids)) and 'id="kid-mouth-open"' in a
    assert all(ref.startswith("kid-") for ref in re.findall(r"url\(#([^)]+)\)", a))


def test_scope_ids_rewrites_hrefs():
    svg = '<svg><g id="a"/><use href="#a"/><use xlink:href="#a"/></svg>'
    assert scope_ids(svg, "p") == '<svg><g id="p-a"/><use href="#p-a"/><use xlink:href="#p-a"/></svg>'


def test_recolour_sets_the_css_variables():
    assert "--primary:#123456" in recolour(load_builtin("mug", {}), {"primary": "#123456"})


def test_recolour_without_colours_is_unchanged_and_survives_the_check():
    svg = load_builtin("cupcake", {})
    coloured = recolour(svg, {"primary": "#123456", "secondary": "#abcdef", "accent": "#000000"})
    assert recolour(svg, {}) == svg
    assert "--secondary:#abcdef" in coloured and "--accent:#000000" in coloured
    check_svg(coloured, path="x")


def test_recolour_ignores_unknown_keys_and_bad_values():
    svg = minimal_svg()
    assert recolour(svg, {"evil": "red", "primary": "red;}x{"}) == svg


def test_builtin_names_lists_the_library():
    names = {entry["name"]: entry for entry in builtin_names()}
    assert {"cupcake", "mug"} <= set(names)
    assert all(entry["description"] and entry["role"] for entry in names.values())


def test_unknown_builtin_lists_the_names():
    with pytest.raises(InvalidInput) as err:
        load_builtin("dragon", {})
    assert "cupcake" in err.value.message and "mug" in err.value.message


def test_builtin_name_is_not_a_path():
    with pytest.raises(InvalidInput):
        load_builtin("../index", {})
