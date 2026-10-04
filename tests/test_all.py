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
    assert tools == {"list_profiles", "render_card", "render_image", "list_images", "delete_image"}
