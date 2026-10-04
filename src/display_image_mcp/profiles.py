"""Display profiles: resolution, colour depth and output format for a kind of display."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Colour modes and the palette (RGB) each one can actually show.
PALETTES: dict[str, list[tuple[int, int, int]]] = {
    "mono": [(0, 0, 0), (255, 255, 255)],
    "gray4": [(0, 0, 0), (85, 85, 85), (170, 170, 170), (255, 255, 255)],
    "gray16": [(i * 17, i * 17, i * 17) for i in range(16)],
    "tricolor_red": [(0, 0, 0), (255, 255, 255), (255, 0, 0)],
    "tricolor_yellow": [(0, 0, 0), (255, 255, 255), (255, 255, 0)],
    "rgb": [],  # full colour, no fixed palette
}
FORMATS = ("bmp", "png")


@dataclass(frozen=True)
class Profile:
    name: str
    width: int
    height: int
    color_mode: str = "mono"
    format: str = "bmp"
    description: str = ""

    def __post_init__(self) -> None:
        if not NAME_RE.match(self.name):
            raise ValueError(f"invalid profile name {self.name!r} (lowercase letters, digits, - and _)")
        if self.width < 1 or self.height < 1:
            raise ValueError(f"profile {self.name}: width and height must be positive")
        if self.color_mode not in PALETTES:
            raise ValueError(f"profile {self.name}: color_mode must be one of {sorted(PALETTES)}")
        if self.format not in FORMATS:
            raise ValueError(f"profile {self.name}: format must be one of {FORMATS}")

    @property
    def palette(self) -> list[tuple[int, int, int]]:
        return PALETTES[self.color_mode]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "color_mode": self.color_mode,
            "colors": ["#%02x%02x%02x" % c for c in self.palette] or "full colour",
            "format": self.format,
            "description": self.description,
        }


def _p(name: str, w: int, h: int, mode: str, desc: str, fmt: str = "bmp") -> Profile:
    return Profile(name, w, h, mode, fmt, desc)


BUILTIN_PROFILES: list[Profile] = [
    _p("waveshare-2in9-v2", 296, 128, "mono", "Waveshare 2.9\" e-paper V2, landscape"),
    _p("waveshare-2in9-b", 296, 128, "tricolor_red", "Waveshare 2.9\" e-paper B (black/white/red), landscape"),
    _p("waveshare-2in13-v3", 250, 122, "mono", "Waveshare 2.13\" e-paper V3, landscape"),
    _p("waveshare-4in2", 400, 300, "mono", "Waveshare 4.2\" e-paper"),
    _p("waveshare-7in5-v2", 800, 480, "mono", "Waveshare 7.5\" e-paper V2"),
    _p("ssd1306-128x64", 128, 64, "mono", "SSD1306 OLED 128x64"),
    _p("st7789-240x240", 240, 240, "rgb", "ST7789 TFT 240x240", "png"),
]


def load_profiles(extra_file: Path | None = None) -> dict[str, Profile]:
    """Built-in profiles, overridden/extended by a TOML file of [profiles.<name>] tables."""
    profiles = {p.name: p for p in BUILTIN_PROFILES}
    if extra_file is not None:
        data = tomllib.loads(extra_file.read_text())
        for name, spec in data.get("profiles", {}).items():
            profiles[name] = Profile(name=name, **spec)
    return profiles
