"""Declarative layout: nested row/column containers with flex sizing and fit-to-box text.

The caller describes *what goes where relative to its neighbours* and this module does the
measuring and pixel maths. Anything that does not fit is reported in `warnings` rather than
silently clipped, so a caller (an LLM, say) can correct itself.
"""

from __future__ import annotations

from typing import Any

from PIL import ImageDraw

from .render import RenderError, Renderer

MAX_DEPTH = 8
MAX_NODES = 120
CONTAINERS = ("row", "column")
LEAVES = ("text", "rect", "progress", "spacer")


def _pad(value: Any) -> tuple[int, int, int, int]:
    """padding as int, [vertical, horizontal] or [top, right, bottom, left] -> (top, right, bottom, left)."""
    if value is None:
        return (0, 0, 0, 0)
    if isinstance(value, (int, float)):
        v = int(value)
        return (v, v, v, v)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (int(value[0]), int(value[1]), int(value[0]), int(value[1]))
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return tuple(int(v) for v in value)  # type: ignore[return-value]
    raise RenderError(f"padding must be a number, [vertical, horizontal] or [top, right, bottom, left], got {value!r}")


class Layout:
    def __init__(self, renderer: Renderer, draw: ImageDraw.ImageDraw):
        self.r = renderer
        self.draw = draw
        self.warnings: list[str] = []
        self._nodes = 0

    # -- public ----------------------------------------------------------------
    def run(self, root: dict, width: int, height: int) -> list[str]:
        self._validate(root, "root", 1)
        self._layout(root, 0, 0, width, height, "root")
        return self.warnings

    # -- validation -------------------------------------------------------------
    def _validate(self, node: Any, path: str, depth: int) -> None:
        if not isinstance(node, dict):
            raise RenderError(f"{path}: expected an object, got {type(node).__name__}")
        if depth > MAX_DEPTH:
            raise RenderError(f"{path}: layout is nested deeper than {MAX_DEPTH} levels")
        self._nodes += 1
        if self._nodes > MAX_NODES:
            raise RenderError(f"layout has more than {MAX_NODES} elements")
        kind = node.get("type")
        if kind in CONTAINERS:
            for i, child in enumerate(node.get("children", [])):
                self._validate(child, f"{path}.children[{i}]", depth + 1)
        elif kind not in LEAVES:
            raise RenderError(f"{path}: unknown type {kind!r} (row, column, text, rect, progress, spacer)")

    # -- text helpers -------------------------------------------------------------
    def _line_height(self, size: int) -> int:
        return int(size * 1.2)

    def _width(self, text: str, size: int, bold: bool) -> float:
        return self.draw.textlength(text, font=self.r.font(size)) + (1 if bold else 0)

    def _lines(self, text: str, size: int, bold: bool, width: int, max_lines: int) -> list[str]:
        """All lines the text needs at this width (not yet limited to max_lines)."""
        if max_lines <= 1:
            return [" ".join(str(text).split())] if str(text).strip() else [""]
        out: list[str] = []
        for para in str(text).split("\n"):
            current = ""
            for word in para.split():
                trial = f"{current} {word}".strip()
                if self._width(trial, size, bold) <= width or not current:
                    current = trial
                else:
                    out.append(current)
                    current = word
            out.append(current)
        return out

    def _fit_size(self, node: dict, w: int, h: int) -> int:
        """Largest font size in [min_size, max_size] whose text fits the w x h box."""
        lo = int(node.get("min_size", 8))
        explicit = node.get("size")
        hi = int(node.get("max_size", explicit if isinstance(explicit, (int, float)) else min(h, 96)))
        hi = max(hi, lo)
        bold, max_lines = bool(node.get("bold")), int(node.get("max_lines", 1))
        for size in range(hi, lo - 1, -1):
            lines = self._lines(node.get("text", ""), size, bold, w, max_lines)
            if (len(lines) <= max_lines and len(lines) * self._line_height(size) <= h
                    and all(self._width(ln, size, bold) <= w for ln in lines)):
                return size
        return lo

    def _text_size(self, node: dict) -> int:
        """Size used when measuring. Shrink-to-fit text measures small so it only grows when given flex room."""
        size = node.get("size", 12)
        return int(node.get("min_size", 8)) if size == "fit" else int(size)

    # -- measuring --------------------------------------------------------------------
    def _measure(self, node: dict, avail_w: int) -> tuple[int, int]:
        """Natural (width, height) of a node when given up to avail_w pixels of width."""
        kind = node["type"]
        ew, eh = node.get("w"), node.get("h")
        if ew is not None:
            avail_w = int(ew)
        if kind == "text":
            size = self._text_size(node)
            bold = bool(node.get("bold"))
            max_lines = int(node.get("max_lines", 1))
            lines = self._lines(node.get("text", ""), size, bold, avail_w, max_lines)[:max_lines]
            nat_w = min(int(max((self._width(ln, size, bold) for ln in lines), default=0)) + 1, avail_w)
            nat_h = len(lines) * self._line_height(size)
        elif kind in ("rect", "progress", "spacer"):
            nat_w, nat_h = 0, (8 if kind == "progress" else 0)
        else:
            top, right, bottom, left = _pad(node.get("padding"))
            b = int(node.get("border", 0))
            inner_w = max(avail_w - left - right - 2 * b, 0)
            gap = int(node.get("gap", 0))
            kids = node.get("children", [])
            sizes = [self._measure(c, inner_w) for c in kids]
            if kind == "column":
                nat_w = max((s[0] for s in sizes), default=0)
                nat_h = sum(s[1] for s in sizes) + gap * max(len(kids) - 1, 0)
            else:
                nat_w = sum(s[0] for s in sizes) + gap * max(len(kids) - 1, 0)
                nat_h = max((s[1] for s in sizes), default=0)
            nat_w += left + right + 2 * b
            nat_h += top + bottom + 2 * b
        return (int(ew) if ew is not None else nat_w, int(eh) if eh is not None else nat_h)

    # -- layout + drawing -----------------------------------------------------------------
    def _layout(self, node: dict, x: int, y: int, w: int, h: int, path: str) -> None:
        if w <= 0 or h <= 0:
            self.warnings.append(f"{path} ({node['type']}) got no room ({w}x{h}px) and was not drawn")
            return
        if x < 0 or y < 0 or x + w > self.r.profile.width or y + h > self.r.profile.height:
            self.warnings.append(f"{path} ({node['type']}) extends beyond the {self.r.profile.width}x"
                                 f"{self.r.profile.height} canvas")
        kind = node["type"]
        if kind == "text":
            self._draw_text(node, x, y, w, h, path)
        elif kind == "rect":
            self._draw_box(node, x, y, w, h)
        elif kind == "progress":
            c = self.r.color(node.get("color", "black"))
            value = min(max(float(node.get("value", 0)), 0.0), 1.0)
            self.draw.rectangle([x, y, x + w - 1, y + h - 1], outline=c, width=1)
            inner = int((w - 4) * value)
            if inner > 0 and h > 4:
                self.draw.rectangle([x + 2, y + 2, x + 1 + inner, y + h - 3], fill=c)
        elif kind in CONTAINERS:
            self._layout_container(node, x, y, w, h, path)
        # spacer draws nothing

    def _draw_box(self, node: dict, x: int, y: int, w: int, h: int) -> None:
        fill = self.r.color(node["fill"]) if node.get("fill") is not None else None
        outline = self.r.color(node.get("outline", "black")) if node.get("outline") is not None else None
        if fill is not None or outline is not None:
            self.draw.rectangle([x, y, x + w - 1, y + h - 1], fill=fill, outline=outline,
                                width=int(node.get("outline_width", 1)))

    def _draw_text(self, node: dict, x: int, y: int, w: int, h: int, path: str) -> None:
        text = str(node.get("text", ""))
        bold = bool(node.get("bold"))
        max_lines = int(node.get("max_lines", 1))
        shrink = node.get("size") == "fit" or node.get("fit") == "shrink"
        size = self._fit_size(node, w, h) if shrink else int(node.get("size", 12))
        font = self.r.font(size)
        lh = self._line_height(size)
        lines = self._lines(text, size, bold, w, max_lines)
        shown = lines[:max_lines]
        clipped = len(lines) > max_lines or any(self._width(ln, size, bold) > w for ln in shown)
        if clipped:
            shown = [self.r._fit(self.draw, ln + (" …" if (i == len(shown) - 1 and len(lines) > max_lines) else ""),
                                 font, w, bold) for i, ln in enumerate(shown)]
            self.warnings.append(f"{path} text was truncated to fit {w}x{h}px: {text[:40]!r}"
                                 + (" (try a smaller size, fit:'shrink', or more max_lines)" if not shrink else ""))
        if len(shown) * lh > h + 1:
            self.warnings.append(f"{path} text is taller ({len(shown) * lh}px) than its box ({h}px)")
        align = node.get("align", "left")
        valign = node.get("valign", "top")
        top = y + {"top": 0, "middle": (h - len(shown) * lh) // 2, "bottom": h - len(shown) * lh}.get(valign, 0)
        color = self.r.color(node.get("color", "black"))
        for i, line in enumerate(shown):
            tw = self._width(line, size, bold)
            lx = x + {"left": 0, "center": int((w - tw) // 2), "right": int(w - tw)}.get(align, 0)
            for dx in ((0, 1) if bold else (0,)):
                self.draw.text((lx + dx, top + i * lh), line, font=font, fill=color, anchor="la")

    def _layout_container(self, node: dict, x: int, y: int, w: int, h: int, path: str) -> None:
        if node.get("fill") is not None:
            self._draw_box(node, x, y, w, h)
        b = int(node.get("border", 0))
        if b:
            self.draw.rectangle([x, y, x + w - 1, y + h - 1],
                                outline=self.r.color(node.get("border_color", "black")), width=b)
        top, right, bottom, left = _pad(node.get("padding"))
        ix, iy = x + left + b, y + top + b
        iw, ih = w - left - right - 2 * b, h - top - bottom - 2 * b
        kids = node.get("children", [])
        if not kids:
            return
        vertical = node["type"] == "column"
        gap = int(node.get("gap", 0))
        align = node.get("align", "stretch")
        justify = node.get("justify", "start")
        main_avail = ih if vertical else iw
        cross_avail = iw if vertical else ih

        # 1. cross size (column: width) and fixed main size per child; flex children get main later
        cross: list[int] = []
        main: list[int | None] = []
        for c in kids:
            flex = float(c.get("flex", 0))
            ckey, mkey = ("w", "h") if vertical else ("h", "w")
            if c.get(ckey) is not None:
                cross_size = int(c[ckey])
            elif align == "stretch":
                cross_size = cross_avail
            else:
                cross_size = None  # decided after main is known (needs measuring)
            if c.get(mkey) is not None:
                m: int | None = int(c[mkey])
            elif flex > 0:
                m = None
            elif vertical:
                m = self._measure(c, cross_size if cross_size is not None else iw)[1]
            else:
                m = self._measure(c, iw)[0]
            cross.append(cross_size if cross_size is not None else -1)
            main.append(m)

        # 2. share leftover main space between flex children
        fixed = sum(m for m in main if m is not None) + gap * (len(kids) - 1)
        flex_total = sum(float(c.get("flex", 0)) for c, m in zip(kids, main) if m is None)
        leftover = main_avail - fixed
        if leftover < 0:
            self.warnings.append(f"{path} ({node['type']}) children need {fixed}px but only {main_avail}px "
                                 f"is available along the {'height' if vertical else 'width'}")
        sizes: list[int] = []
        remaining = max(leftover, 0)
        flex_left = flex_total
        for c, m in zip(kids, main):
            if m is None:
                share = int(round(remaining * float(c["flex"]) / flex_left)) if flex_left else 0
                remaining -= share
                flex_left -= float(c["flex"])
                sizes.append(share)
            else:
                sizes.append(m)

        # 3. cross sizes that depended on measuring
        for i, c in enumerate(kids):
            if cross[i] == -1:
                nat = self._measure(c, sizes[i] if not vertical else iw)
                cross[i] = min(nat[0] if vertical else nat[1], cross_avail)

        # 4. position
        used = sum(sizes) + gap * (len(kids) - 1)
        extra = max(main_avail - used, 0)
        has_flex = any(m is None for m in main)
        pos = 0
        step_gap = gap
        if not has_flex:
            if justify == "center":
                pos = extra // 2
            elif justify == "end":
                pos = extra
            elif justify == "space-between" and len(kids) > 1:
                step_gap = gap + extra // (len(kids) - 1)
        for i, c in enumerate(kids):
            offset = {"start": 0, "stretch": 0, "center": (cross_avail - cross[i]) // 2,
                      "end": cross_avail - cross[i]}.get(align, 0)
            cpath = f"{path}.children[{i}]"
            if vertical:
                self._layout(c, ix + offset, iy + pos, cross[i], sizes[i], cpath)
            else:
                self._layout(c, ix + pos, iy + offset, sizes[i], cross[i], cpath)
            pos += sizes[i] + step_gap
