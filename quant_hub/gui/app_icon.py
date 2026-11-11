"""The application icon: a blue α in a thin ring on a near-black rounded tile.

A Windows shortcut can only take its icon from an .ico (or .exe/.dll) file,
so the icon ships as ``assets/quanthub.ico`` and the app loads that same
file for its window and taskbar icon. This module paints it with QPainter
from the theme palette and writes it; rerun it after a palette change:

    py -3.12 -m quant_hub.gui.app_icon

The layout copies the ML Cookbook's β icon, measured off its 256 px image
and kept as fractions of the side ``s``: a tile inset 0.02 s with a 0.023 s
accent border and 0.15 s corners, a 0.016 s ring of radius 0.31 s, and the
glyph's ink 0.32 s wide, centered (α is wider and shorter than β, so it is
sized by width, not by the font's line height). At 24 px and below the ring
would be a smudge, so those sizes drop it and let the α fill the tile.

An .ico is a 6-byte header (reserved 0, type 1, image count), one 16-byte
directory entry per image (width, height — 0 meaning 256 —, palette size,
reserved, planes, bits per pixel, byte length, file offset), then the image
data. Windows Vista and later accept PNG-encoded images there, so each size
is stored as a PNG; Qt's own ICO writer only stores one image per file.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QTransform

from quant_hub.gui.theme import PALETTE

ICO_PATH = Path(__file__).parent / "assets" / "quanthub.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)

# The taskbar identity. Without an explicit one Windows files the window
# under pythonw.exe and shows Python's icon on the taskbar; the desktop
# shortcut carries the same ID so it and the running window are one button.
APP_USER_MODEL_ID = "QuantHub.QuantHub"

GLYPH = "α"
GLYPH_FONT = "Cambria Math"   # ships with Windows; the face of the β
_DETAIL_MIN = 32              # smallest side that still draws the ring


def render(side: int) -> QImage:
    """Paint the icon into a transparent ``side`` x ``side`` image."""
    if side < 16:
        raise ValueError(f"icon side must be at least 16 px, got {side}")
    image = QImage(side, side, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    s = float(side)
    detail = side >= _DETAIL_MIN

    border = max(1.0, 0.023 * s)
    inset = 0.02 * s + border / 2
    painter.setPen(QPen(QColor(PALETTE.accent), border))
    painter.setBrush(QColor(PALETTE.page))
    painter.drawRoundedRect(QRectF(inset, inset, s - 2 * inset, s - 2 * inset),
                            0.15 * s, 0.15 * s)

    center = QPointF(s / 2, s / 2)
    if detail:
        painter.setPen(QPen(QColor(PALETTE.accent_soft_border), max(1.0, 0.016 * s)))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, 0.31 * s, 0.31 * s)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(PALETTE.series_light))
    painter.drawPath(_glyph(center, (0.32 if detail else 0.62) * s))
    painter.end()
    return image


def _glyph(center: QPointF, width: float) -> QPainterPath:
    """The α outline scaled to ``width`` and centered on its ink, not on the
    font's baseline box (α has no ascender or descender to balance it)."""
    path = QPainterPath()
    path.addText(0, 0, QFont(GLYPH_FONT, 100), GLYPH)
    ink = path.boundingRect()
    if ink.isEmpty():       # no font at all (bare offscreen platform)
        return path
    k = width / ink.width()
    return QTransform(k, 0, 0, k, center.x() - k * ink.center().x(),
                      center.y() - k * ink.center().y()).map(path)


def ico_bytes(images: list[QImage]) -> bytes:
    """Pack square images (16-256 px) into one .ico, each stored as a PNG."""
    pngs = []
    for image in images:
        if image.width() != image.height() or not 1 <= image.width() <= 256:
            raise ValueError(f"ico images must be square and at most 256 px, "
                             f"got {image.width()}x{image.height()}")
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QBuffer.WriteOnly)
        image.save(buffer, "PNG")
        pngs.append(bytes(data))
    entries, offset = [], 6 + 16 * len(images)
    for image, png in zip(images, pngs):
        side = image.width() % 256
        entries.append(struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset))
        offset += len(png)
    return struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(pngs)


def write_ico(path: Path = ICO_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ico_bytes([render(side) for side in SIZES]))
    return path


def app_icon() -> QIcon:
    """The shipped icon, every size (an empty icon if the file is missing)."""
    return QIcon(str(ICO_PATH))


if __name__ == "__main__":
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication(sys.argv)     # fonts need a GUI application
    print(f"wrote {write_ico()}")
