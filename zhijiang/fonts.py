"""System fonts for rasterized Chinese course text."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import ImageFont


def _candidates(bold: bool) -> list[tuple[Path, int]]:
    if sys.platform == "win32":
        directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        names = ("msyhbd.ttc", "simhei.ttf", "msyh.ttc") if bold else ("msyh.ttc", "simhei.ttf")
        return [(directory / name, 0) for name in names]
    if sys.platform == "darwin":
        directory = Path("/System/Library/Fonts")
        return [
            (directory / "Hiragino Sans GB.ttc", 2 if bold else 0),
            (directory / ("STHeiti Medium.ttc" if bold else "STHeiti Light.ttc"), 1),
        ]

    weights = ("Bold", "Regular") if bold else ("Regular",)
    return [
        (directory / f"NotoSansCJK-{name}.ttc", 2)
        for directory in (Path("/usr/share/fonts/opentype/noto"), Path("/usr/share/fonts/truetype/noto"))
        for name in weights
    ] + [(Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"), 0)]


def chinese_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | None:
    for path, index in _candidates(bold):
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size, index=index)
            except OSError:
                continue
    return None
