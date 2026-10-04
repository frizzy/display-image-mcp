import io

import pytest
from PIL import Image
from starlette.testclient import TestClient

from display_image_mcp.http_host import build_app
from display_image_mcp.profiles import Profile, load_profiles
from display_image_mcp.render import RenderError, Renderer, card_elements
from display_image_mcp.server import Config, Service, build_server
from display_image_mcp.store import ImageStore


@pytest.fixture
def svc(tmp_path):
    return Service(Config(tmp_path, None, "127.0.0.1", 8099, "http://pi:8099", None))


def decode(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_mono_bmp_is_one_bit_with_exact_size(svc):
    p = svc.profile("waveshare-2in9-v2")
    r = Renderer(p)
    img = decode(r.encode(r.render(card_elements(p, "HI", ["a line"], "foot"))))
    assert img.format == "BMP" and img.mode == "1" and img.size == (296, 128)
    assert set(img.convert("L").tobytes()) <= {0, 255}


def test_tricolor_snaps_to_palette(svc):
    p = svc.profile("waveshare-2in9-b")
    r = Renderer(p)
    out = decode(r.encode(r.render([{"type": "rect", "x": 0, "y": 0, "w": 10, "h": 10, "fill": "#ff3030"}]))).convert("RGB")
    assert out.getpixel((2, 2)) == (255, 0, 0)
    assert out.getpixel((50, 50)) == (255, 255, 255)


def test_rgb_png(svc):
    p = svc.profile("st7789-240x240")
    r = Renderer(p)
    img = decode(r.encode(r.render([{"type": "rect", "x": 0, "y": 0, "w": 5, "h": 5, "fill": "#123456"}])))
    assert img.format == "PNG" and img.convert("RGB").getpixel((1, 1)) == (0x12, 0x34, 0x56)


def test_gray4_levels(svc):
    p = Profile("g", 20, 20, "gray4")
    r = Renderer(p)
    img = decode(r.encode(r.render([{"type": "rect", "x": 0, "y": 0, "w": 20, "h": 20, "fill": "#777777"}])))
    assert set(img.tobytes()) <= {0, 85, 170, 255}


def test_text_is_truncated_not_overflowing(svc):
    p = svc.profile("ssd1306-128x64")
    r = Renderer(p)
    img = r.render([{"type": "text", "text": "x" * 200, "x": 0, "y": 0, "size": 12, "max_width": 60}])
    cols = [x for x in range(img.width) if any(img.getpixel((x, y)) == 0 for y in range(img.height))]
    assert max(cols) < 64


def test_bad_elements_raise(svc):
    r = Renderer(svc.profile("ssd1306-128x64"))
    with pytest.raises(RenderError):
        r.render([{"type": "blob"}])
    with pytest.raises(RenderError):
        r.render([{"type": "rect", "x": 0}])
    with pytest.raises(RenderError):
        r.render([{"type": "line", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "color": "mauve"}])


def test_overwrite_and_changed_flag(svc):
    a = svc.render("ssd1306-128x64", "main", card_elements(svc.profile("ssd1306-128x64"), "A", []), "white", "")
    same = svc.render("ssd1306-128x64", "main", card_elements(svc.profile("ssd1306-128x64"), "A", []), "white", "")
    diff = svc.render("ssd1306-128x64", "main", card_elements(svc.profile("ssd1306-128x64"), "B", []), "white", "")
    assert a["changed"] and not same["changed"] and diff["changed"]
    assert same["updated_at"] == a["updated_at"]
    assert len(svc.store.list("ssd1306-128x64")) == 1  # overwritten, not duplicated
    assert diff["url"] == "http://pi:8099/images/ssd1306-128x64/main.bmp"


def test_labels_are_independent_and_validated(svc):
    svc.render("ssd1306-128x64", "one", [], "white", "")
    svc.render("ssd1306-128x64", "two", [], "black", "")
    assert [m["label"] for m in svc.store.list("ssd1306-128x64")] == ["one", "two"]
    with pytest.raises(ValueError):
        svc.render("ssd1306-128x64", "../evil", [], "white", "")
    with pytest.raises(ValueError):
        svc.render("nope", "x", [], "white", "")
    assert svc.store.delete("ssd1306-128x64", "one") and not svc.store.delete("ssd1306-128x64", "one")


def test_http_host(svc):
    svc.render("ssd1306-128x64", "main", [], "white", "hello")
    c = TestClient(build_app(svc.store, svc.profiles, "http://pi:8099"))
    r = c.get("/images/ssd1306-128x64/main.bmp")
    assert r.status_code == 200 and r.headers["content-type"] == "image/bmp" and r.content[:2] == b"BM"
    assert c.get("/images/ssd1306-128x64/main").status_code == 200  # extension optional
    assert c.get("/images/ssd1306-128x64/main", headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    d = c.get("/images/ssd1306-128x64").json()
    assert d["profile"]["width"] == 128 and d["images"][0]["url"] == "http://pi:8099/images/ssd1306-128x64/main.bmp"
    assert list(c.get("/images").json()) == ["ssd1306-128x64"]
    assert c.get("/images/ssd1306-128x64/missing").status_code == 404
    assert c.get("/images/nope").status_code == 404
    assert c.get("/healthz").text == "ok"
    assert c.get("/images/ssd1306-128x64/..%2Fx").status_code == 404


def test_custom_profiles_file(tmp_path):
    f = tmp_path / "p.toml"
    f.write_text('[profiles.my-lcd]\nwidth = 64\nheight = 32\ncolor_mode = "gray4"\nformat = "png"\n')
    assert load_profiles(f)["my-lcd"].width == 64
    f.write_text('[profiles.bad]\nwidth = 0\nheight = 1\n')
    with pytest.raises(ValueError):
        load_profiles(f)


@pytest.mark.anyio
async def test_mcp_tools_registered(svc):
    tools = {t.name for t in (await build_server(svc).list_tools())}
    assert {"list_profiles", "render_card", "render_image", "list_images", "delete_image"} <= tools


def test_bearer_auth(svc):
    from display_image_mcp.server import BearerAuth

    async def ok(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"hi"})

    c = TestClient(BearerAuth(ok, "s3cret"))
    assert c.get("/").status_code == 401
    assert c.get("/", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/", headers={"Authorization": "Basic s3cret"}).status_code == 401
    assert c.get("/", headers={"Authorization": "Bearer s3cret"}).text == "hi"


# -- layout ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402
import json  # noqa: E402

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.json"))


def layout(svc, root, profile="waveshare-2in9-v2"):
    p = svc.profile(profile)
    return Renderer(p).render_layout(root)


def text_cols(img):
    g = img.convert("L")
    return [x for x in range(g.width) if any(g.getpixel((x, y)) == 0 for y in range(g.height))]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_examples_render_without_warnings(svc, path):
    img, warnings = layout(svc, json.loads(path.read_text()))
    assert warnings == [] and img.size == (296, 128)
    assert 0 in set(img.convert("L").tobytes())  # something was drawn


def test_examples_exist():
    assert {p.stem for p in EXAMPLES} >= {"dashboard", "alert", "agenda"}


def test_flex_splits_leftover_space_by_weight(svc):
    root = {"type": "row", "children": [
        {"type": "rect", "w": 20, "fill": "black"},
        {"type": "rect", "flex": 1, "fill": "black"},
        {"type": "rect", "flex": 3, "fill": "white", "outline": "black"}]}
    img, w = layout(svc, root)
    assert w == []
    # leftover 276 -> 69 and 207; the white box with the outline starts at x=20+69
    g = img.convert("L")
    assert g.getpixel((10, 64)) == 0 and g.getpixel((60, 64)) == 0
    assert g.getpixel((89, 64)) == 0  # outline of third box
    assert g.getpixel((150, 64)) == 255


def test_fit_text_grows_to_fill_its_box(svc):
    root = {"type": "column", "children": [{"type": "text", "text": "21", "size": "fit", "flex": 1}]}
    img, w = layout(svc, root)
    small = layout(svc, {"type": "column", "children": [{"type": "text", "text": "21", "size": 12, "flex": 1}]})[0]
    assert w == [] and max(text_cols(img)) > max(text_cols(small)) * 2


def test_truncation_and_overflow_warn(svc):
    _, w = layout(svc, {"type": "column", "children": [
        {"type": "text", "text": "this is far too long for one line in a narrow box " * 3, "size": 14, "w": 100}]})
    assert any("truncated" in x for x in w)
    _, w = layout(svc, {"type": "column", "children": [{"type": "rect", "h": 100}, {"type": "rect", "h": 100}]})
    assert any("children need" in x for x in w)
    _, w = layout(svc, {"type": "column", "children": [{"type": "rect", "h": 128}, {"type": "spacer", "flex": 1}]})
    assert any("no room" in x for x in w)


def test_wrapping_with_max_lines(svc):
    _, w = layout(svc, {"type": "column", "children": [
        {"type": "text", "text": "a few words that need two lines to fit in this box", "size": 12, "w": 120, "max_lines": 3}]})
    assert w == []


def test_justify_and_align(svc):
    root = {"type": "row", "justify": "center", "align": "center", "children": [
        {"type": "rect", "w": 40, "h": 20, "fill": "black"}]}
    g = layout(svc, root)[0].convert("L")
    assert g.getpixel((148, 64)) == 0 and g.getpixel((10, 64)) == 255 and g.getpixel((148, 10)) == 255


def test_layout_validation(svc):
    for bad in ({"type": "blob"}, "text", {"type": "column", "children": [{"type": "text"}, 5]}):
        with pytest.raises(RenderError):
            layout(svc, bad)
    deep = {"type": "spacer"}
    for _ in range(9):
        deep = {"type": "column", "children": [deep]}
    with pytest.raises(RenderError, match="deeper"):
        layout(svc, deep)
    wide = {"type": "row", "children": [{"type": "spacer"} for _ in range(121)]}
    with pytest.raises(RenderError, match="more than"):
        layout(svc, wide)


def test_render_layout_tool_and_store(svc):
    out = svc.render_layout("waveshare-2in9-v2", "main", json.loads(EXAMPLES[0].read_text()), "white", "")
    assert out["warnings"] == [] and out["url"].endswith("/images/waveshare-2in9-v2/main.bmp")
    again = svc.render_layout("waveshare-2in9-v2", "main", json.loads(EXAMPLES[0].read_text()), "white", "")
    assert again["changed"] is False


@pytest.mark.anyio
async def test_mcp_tools_include_layout(svc):
    tools = {t.name for t in (await build_server(svc).list_tools())}
    assert "render_layout" in tools
