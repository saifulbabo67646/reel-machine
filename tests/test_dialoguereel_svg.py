import base64
import re

import pytest

from reelmachine.core.errors import InvalidInput
from reelmachine.recipes.dialoguereel.library import builtin_names, load_builtin
from reelmachine.recipes.dialoguereel.svg import MAX_BYTES, OPTIONAL, REQUIRED, check_svg, inline_svg, part_id, recolour

PATH = "cast.characters[0].source.svg"
BUILTINS = [entry["name"] for entry in builtin_names()]


def minimal_svg(extra: str = "", *, view_box: str = "0 0 400 400", drop: str = "") -> str:
    groups = "".join(f'<g id="{name}"><circle cx="200" cy="200" r="10"/></g>' for name in REQUIRED if name != drop)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}">{groups}{extra}</svg>'


def test_minimal_svg_is_valid_and_has_no_optional_groups():
    assert check_svg(minimal_svg(), path=PATH) == []


@pytest.mark.parametrize("name", BUILTINS)
def test_each_builtin_meets_the_contract_and_has_every_optional_group(name):
    assert set(check_svg(load_builtin(name, {}), path="x")) == set(OPTIONAL)


@pytest.mark.parametrize("name", BUILTINS)
def test_each_builtin_rests_neutral_with_alternates_hidden(name):
    svg = load_builtin(name, {})
    for hidden in ["eyes-closed", "mouth-open", "mouth-smile", "eyes-happy", "eyes-surprised", "sweat", "sparkle"]:
        assert re.search(rf'<g id="{hidden}"[^>]*display="none"', svg), hidden
    for shown in ["eyes-open", "mouth-closed", "blush"]:
        assert not re.search(rf'<g id="{shown}"[^>]*display="none"', svg), shown
    assert len(svg.encode()) <= MAX_BYTES


@pytest.mark.parametrize("arm", ["arm-left", "arm-right"])
def test_builtin_arms_carry_a_pivot(arm):
    for name in BUILTINS:
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
    ('<style>.a{fill:#fff}</style>', "style"), ('<STYLE>.a{}</STYLE>', "STYLE"),
    ('<image HREF="file:///etc/x.png"/>', "image"), ('<use HREF="https://e.com/a.svg#x"/>', "HREF"),
    ('<use XLINK:HREF="https://e.com/a.svg#x" xmlns:XLINK="http://www.w3.org/1999/xlink"/>', "xlink:href"),
    ('<use xl:href="https://e.com/a.svg#x" xmlns:xl="http://www.w3.org/1999/xlink"/>', "xlink:href"),
    ('<g Style="fill:\\75rl(https://e.com/x)"/>', "Style"), ('<g STYLE="fill:url(https://e.com/x)"/>', "STYLE"),
    ('<g OnLoad="x()"/>', "OnLoad"),
    ('<g fill="\\75rl(https://e.com/x)"/>', "fill"), ('<g filter="url(https://e.com/f.svg#f)"/>', "filter"),
    ('<g mask="URL(//e.com/m.svg#m)"/>', "mask"), ('<g cursor="url(https://e.com/c.png), auto"/>', "cursor"),
    ('<g clip-path="u\\rl(https://e.com/c)"/>', "clip-path"),
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
    with pytest.raises(InvalidInput) as err:
        check_svg(minimal_svg(extra='<g ID="body"/>'), path=PATH)
    assert "body" in err.value.message


def test_a_data_png_image_is_allowed():
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    assert check_svg(minimal_svg(extra=f'<image href="data:image/png;base64,{png}" width="10" height="10"/>'),
                     path=PATH) == []


def test_fragment_refs_and_gradient_urls_are_allowed():
    extra = ('<defs><linearGradient id="g"><stop offset="0"/></linearGradient></defs>'
             '<use href="#body" style="fill:url( #g )" fill="URL(#g)" filter="url(\'#g\')"/>')
    assert check_svg(minimal_svg(extra=extra), path=PATH) == []


def test_inline_svg_keeps_two_characters_apart():
    a, b = inline_svg(load_builtin("cupcake", {}), "kid"), inline_svg(load_builtin("mug", {}), "mom")
    ids = re.findall(r'id="([^"]+)"', a + b)
    assert len(ids) == len(set(ids)) and 'id="c_kid.mouth-open"' in a
    assert all(ref.startswith("c_kid.") for ref in re.findall(r"url\(#([^)]+)\)", a))
    assert all(ref.startswith("#c_kid.") for ref in re.findall(r'href="([^"]+)"', a))


def test_inline_svg_rewrites_hrefs_and_drops_the_xlink_prefix():
    svg = minimal_svg(extra='<g id="a"/><use href="#a"/><use xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="#a"/>')
    out = inline_svg(svg, "p")
    assert '<g id="c_p.a" />' in out and out.count('<use href="#c_p.a" />') == 2
    assert "xlink" not in out and "ns0" not in out and out.startswith('<svg xmlns="http://www.w3.org/2000/svg" ')


def test_inline_svg_scopes_entity_encoded_fragment_refs():
    svg = minimal_svg(extra='<use href="&#35;body"/><g fill="url(&#35;body)"/>')
    out = inline_svg(svg, "mom")
    assert 'href="#c_mom.body"' in out and 'fill="url(#c_mom.body)"' in out and "&#35;" not in out


def test_inline_svg_handles_an_agent_svg_with_spaced_and_uppercase_attributes():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400">'
           '<defs><linearGradient id = "g"><stop offset="0"/></linearGradient></defs>'
           "<g id ='body' fill = 'URL( #g )'/><g ID=\"eyes-open\"/><g id=\"eyes-closed\"/>"
           '<g id="mouth-closed"/><g id="mouth-open"/>'
           '<use href = "#body"/><use xlink:href= \'#body\' xmlns:xlink="http://www.w3.org/1999/xlink"/>'
           '<g data-id="keep"/></svg>')
    check_svg(svg, path=PATH)
    a, b = inline_svg(svg, "kid"), inline_svg(svg, "mom")
    assert 'id="c_kid.g"' in a and 'id="c_kid.body"' in a and 'ID="c_kid.eyes-open"' in a
    assert "URL( #c_kid.g )" in a and a.count('href="#c_kid.body"') == 2
    assert 'data-id="keep"' in a
    ids = re.findall(r"""(?<![\w-])id\s*=\s*["']([^"']+)""", a + b, flags=re.IGNORECASE)
    assert len(ids) == 12 and len(set(ids)) == 12


def test_inline_svg_escapes_what_the_html_parser_would_read_as_markup():
    svg = minimal_svg(extra='<g data-x="&lt;img src=x onerror=alert(1)&gt;"/>'
                            '<text>&lt;/svg&gt;&lt;img src=x onerror=alert(1)&gt;</text>'
                            '<title>&lt;/title&gt;&lt;img onerror=alert(1)&gt;</title>')
    out = inline_svg(svg, "kid")
    assert "<img" not in out and "</svg>" == out[-6:] and out.count("<svg") == 1
    assert 'data-x="&lt;img src=x onerror=alert(1)&gt;"' in out


@pytest.mark.parametrize("bad", [
    '<!--><img src="x" onerror="document.title=1">-->',  # an empty comment for HTML, a comment for XML
    "<!-- an editor note -->", "<![CDATA[<img src=x onerror=alert(1)>]]>", '<?xml-stylesheet href="x"?>',
])
def test_comments_cdata_and_processing_instructions_are_refused(bad):
    with pytest.raises(InvalidInput) as err:
        check_svg(minimal_svg(extra=bad), path=PATH)
    assert err.value.details["path"] == PATH and err.value.details["element"] in ("<!", "<?")
    with pytest.raises(InvalidInput):
        check_svg('<?xml version="1.0"?>' + minimal_svg(), path=PATH)


@pytest.mark.parametrize("extra", ['<g id="x" ID="y"/>', '<g fill="red" FILL="blue"/>',
                                   '<use href="#body" xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="#body"/>'])
def test_attributes_differing_only_in_case_or_namespace_are_refused(extra):
    with pytest.raises(InvalidInput) as err:
        check_svg(minimal_svg(extra=extra), path=PATH)
    assert "two" in err.value.message


def test_title_and_desc_hold_text_only():
    with pytest.raises(InvalidInput) as err:
        check_svg(minimal_svg(extra="<desc><a href='#body'><circle r='1'/></a></desc>"), path=PATH)
    assert err.value.details["element"] == "desc"
    assert check_svg(minimal_svg(extra="<title>a cupcake</title><desc>sweet</desc>"), path=PATH) == []


@pytest.mark.parametrize("a, b", [("kid", "mom"), ("a-b", "a"), ("a_b", "a"), ("a", "a__b"), ("char", "c")])
def test_part_ids_never_collide_across_characters_or_with_page_ids(a, b):
    page = {"root", "reel-data", f"char-{a}", f"char-{b}"}
    names = ["body", "b.x", "x", "b__x", "_b.x", "-x", "char-kid", "reel-data"]
    scoped_a = {part_id(a, n) for n in names}
    scoped_b = {part_id(b, n) for n in names}
    assert not scoped_a & scoped_b and not (scoped_a | scoped_b) & page


def test_recolour_extends_a_spaced_or_uppercase_root_style():
    for attr in ('style = "opacity:1"', "STYLE='opacity:1'"):
        svg = minimal_svg().replace("<svg ", f"<svg {attr} ", 1)
        out = recolour(svg, {"primary": "#123456"})
        assert out.lower().count("style") == 1 and "opacity:1;--primary:#123456" in out
        check_svg(out, path=PATH)


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
    assert len(builtin_names()) == 10 and set(names) == {
        "cupcake", "mug", "onigiri", "dango", "boba", "cat", "shiba", "penguin", "bunny", "kimchi-jar"}
    assert all(entry["description"] and entry["role"] for entry in names.values())
    assert {entry["role"] for entry in names.values()} <= {"kid", "parent", "friend", "teacher", "shopkeeper", "pet"}


@pytest.mark.parametrize("name", BUILTINS)
def test_each_builtin_recoloured_with_all_three_colours_still_passes(name):
    coloured = load_builtin(name, {"primary": "#123456", "secondary": "#abcdef", "accent": "#fedcba"})
    assert "--primary:#123456;--secondary:#abcdef;--accent:#fedcba" in coloured
    assert set(check_svg(coloured, path="x")) == set(OPTIONAL)


def test_every_pair_of_builtins_inlines_with_distinct_ids():
    inlined = {name: inline_svg(load_builtin(name, {}), f"c{i}") for i, name in enumerate(BUILTINS)}
    ids = {name: set(re.findall(r'\sid="([^"]+)"', svg)) for name, svg in inlined.items()}
    for a in BUILTINS:
        for b in BUILTINS:
            if a < b:
                assert not ids[a] & ids[b], (a, b)
    for name, svg in inlined.items():
        refs = set(re.findall(r'href="#([^"]+)"', svg)) | set(re.findall(r'url\(#([^)]+)\)', svg))
        assert refs <= ids[name], name


def test_unknown_builtin_lists_the_names():
    with pytest.raises(InvalidInput) as err:
        load_builtin("dragon", {})
    assert "cupcake" in err.value.message and "mug" in err.value.message


def test_builtin_name_is_not_a_path():
    with pytest.raises(InvalidInput):
        load_builtin("../index", {})
