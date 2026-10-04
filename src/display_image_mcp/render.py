"""Render a declarative element list into an image that matches a display profile."""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .profiles import Profile

NAMED_COLORS = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "yellow": (255, 255, 0),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "dark_gray": (85, 85, 85),
    "light_gray": (170, 170, 170),
}


class RenderError(ValueError):
    """The element spec was invalid."""


def parse_color(value: Any) -> tuple[int, int, int]:
    if isinstance(value, str):
        v = value.strip().lower()
        if v in NAMED_COLORS:
            return NAMED_COLORS[v]
        if len(v) == 7 and v[0] == "#":
            try:
                return int(v[1:3], 16), int(v[3:5], 16), int(v[5:7], 16)
            except ValueError:
                pass
    raise RenderError(f"unknown colour {value!r}: use a name ({', '.join(NAMED_COLORS)}) or #rrggbb")


def nearest(color: tuple[int, int, int], palette: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    if not palette:
        return color
    return min(palette, key=lambda p: sum((a - b) ** 2 for a, b in zip(p, color)))


class Renderer:
    def __init__(self, profile: Profile, font_path: str | None = None):
        self.profile = profile
        self.font_path = font_path
        self._fonts: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    def font(self, size: int):
        if size not in self._fonts:
            if self.font_path:
                self._fonts[size] = ImageFont.truetype(self.font_path, size)
            else:
                self._fonts[size] = ImageFont.load_default(size)
        return self._fonts[size]

    def color(self, value: Any) -> tuple[int, int, int]:
        return nearest(parse_color(value), self.profile.palette)

    # -- text helpers ------------------------------------------------------
    def _fit(self, draw: ImageDraw.ImageDraw, text: str, font, max_width: int, bold: bool) -> str:
        sw = 1 if bold else 0
        if draw.textlength(text, font=font) + sw <= max_width:
            return text
        while text and draw.textlength(text + "…", font=font) + sw > max_width:
            text = text[:-1]
        return text.rstrip() + "…"

    def _wrap(self, draw, text: str, font, max_width: int, max_lines: int, bold: bool) -> list[str]:
        lines: list[str] = []
        current = ""
        for word in text.split():
            trial = f"{current} {word}".strip()
            if draw.textlength(trial, font=font) <= max_width or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self._fit(draw, lines[-1] + " …", font, max_width, bold)
        return [self._fit(draw, ln, font, max_width, bold) for ln in lines]

    # -- element drawing -----------------------------------------------------
    def draw_element(self, draw: ImageDraw.ImageDraw, el: dict) -> None:
        kind = el.get("type")
        try:
            if kind == "text":
                self._text(draw, el)
            elif kind == "rect":
                fill = self.color(el["fill"]) if el.get("fill") is not None else None
                outline = self.color(el["outline"]) if el.get("outline") is not None else None
                x, y, w, h = el["x"], el["y"], el["w"], el["h"]
                draw.rectangle([x, y, x + w - 1, y + h - 1], fill=fill, outline=outline, width=el.get("width", 1))
            elif kind == "line":
                draw.line(
                    [el["x1"], el["y1"], el["x2"], el["y2"]],
                    fill=self.color(el.get("color", "black")),
                    width=el.get("width", 1),
                )
            elif kind == "progress":
                x, y, w, h = el["x"], el["y"], el["w"], el["h"]
                value = min(max(float(el["value"]), 0.0), 1.0)
                c = self.color(el.get("color", "black"))
                draw.rectangle([x, y, x + w - 1, y + h - 1], outline=c, width=1)
                inner = int((w - 4) * value)
                if inner > 0:
                    draw.rectangle([x + 2, y + 2, x + 1 + inner, y + h - 3], fill=c)
            else:
                raise RenderError(f"unknown element type {kind!r} (text, rect, line, progress)")
        except KeyError as exc:
            raise RenderError(f"{kind} element is missing {exc.args[0]!r}") from None

    def _text(self, draw: ImageDraw.ImageDraw, el: dict) -> None:
        size = int(el.get("size", 12))
        font = self.font(size)
        bold = bool(el.get("bold", False))
        color = self.color(el.get("color", "black"))
        anchor = el.get("anchor", "la")
        x, y = el["x"], el["y"]
        text = str(el["text"])
        max_width = el.get("max_width")
        if max_width and el.get("max_lines", 1) > 1:
            lines = self._wrap(draw, text, font, max_width, int(el["max_lines"]), bold)
        elif max_width:
            lines = [self._fit(draw, text, font, max_width, bold)]
        else:
            lines = [text]
        line_height = int(size * 1.2)
        for i, line in enumerate(lines):
            # Bold = the same glyphs drawn twice, 1px apart: stays legible at small sizes where
            # Pillow's stroke_width would clog the counters.
            for dx in ((0, 1) if bold else (0,)):
                draw.text((x + dx, y + i * line_height), line, font=font, fill=color, anchor=anchor)

    # -- whole image -----------------------------------------------------------
    def render(self, elements: list[dict], background: str = "white") -> Image.Image:
        p = self.profile
        canvas = Image.new("RGB", (p.width, p.height), self.color(background))
        draw = ImageDraw.Draw(canvas)
        # Crisp, un-antialiased glyphs on 1-bit-ish displays; smooth text where there are grays/colour.
        draw.fontmode = "1" if p.color_mode in ("mono", "tricolor_red", "tricolor_yellow") else "L"
        for el in elements:
            self.draw_element(draw, el)
        return self._finish(canvas)

    def render_layout(self, root: dict, background: str = "white") -> tuple[Image.Image, list[str]]:
        """Lay out a row/column tree over the whole canvas. Returns the image and layout warnings."""
        from .layout import Layout  # layout imports this module

        p = self.profile
        canvas = Image.new("RGB", (p.width, p.height), self.color(background))
        draw = ImageDraw.Draw(canvas)
        draw.fontmode = "1" if p.color_mode in ("mono", "tricolor_red", "tricolor_yellow") else "L"
        warnings = Layout(self, draw).run(root, p.width, p.height)
        return self._finish(canvas), warnings

    def _finish(self, canvas: Image.Image) -> Image.Image:
        mode = self.profile.color_mode
        if mode == "rgb":
            return canvas
        if mode == "mono":
            return canvas.convert("L").point(lambda v: 255 if v >= 128 else 0).convert("1", dither=Image.Dither.NONE)
        if mode in ("gray4", "gray16"):
            levels = len(self.profile.palette)
            step = 255 / (levels - 1)
            return canvas.convert("L").point(lambda v: round(round(v / step) * step))
        # tricolor: indexed palette image
        pal_img = Image.new("P", (1, 1))
        flat = [c for rgb in self.profile.palette for c in rgb]
        pal_img.putpalette(flat + [0] * (768 - len(flat)))
        return canvas.quantize(palette=pal_img, dither=Image.Dither.NONE)

    def encode(self, img: Image.Image) -> bytes:
        buf = io.BytesIO()
        img.save(buf, format=self.profile.format.upper())
        return buf.getvalue()


def card_elements(profile: Profile, header: str, lines: list[str], footer: str = "",
                  invert_header: bool = True) -> list[dict]:
    """Elements for a simple card: header bar, body lines, footer. Sizes scale with the display height."""
    w, h = profile.width, profile.height
    scale = max(h / 128, 0.5)
    hs, bs, fs = int(14 * scale), int(16 * scale), int(10 * scale)
    bar = int(hs * 1.7)
    pad = max(int(6 * scale), 3)
    els: list[dict] = []
    if invert_header:
        els.append({"type": "rect", "x": 0, "y": 0, "w": w, "h": bar, "fill": "black"})
        els.append({"type": "text", "text": header, "x": pad, "y": bar // 2, "size": hs, "color": "white",
                    "bold": True, "anchor": "lm", "max_width": w - 2 * pad})
    else:
        els.append({"type": "text", "text": header, "x": pad, "y": bar // 2, "size": hs, "bold": True,
                    "anchor": "lm", "max_width": w - 2 * pad})
        els.append({"type": "line", "x1": 0, "y1": bar, "x2": w, "y2": bar, "width": 2})
    footer_h = int(fs * 1.8) if footer else 0
    y = bar + pad
    row = int(bs * 1.35)
    max_rows = max((h - y - footer_h - pad) // row, 0)
    for line in lines[:max_rows]:
        els.append({"type": "text", "text": line, "x": pad, "y": y, "size": bs, "max_width": w - 2 * pad})
        y += row
    if footer:
        els.append({"type": "line", "x1": pad, "y1": h - footer_h, "x2": w - pad, "y2": h - footer_h})
        els.append({"type": "text", "text": footer, "x": pad, "y": h - footer_h + 3, "size": fs,
                    "max_width": w - 2 * pad})
    return els
