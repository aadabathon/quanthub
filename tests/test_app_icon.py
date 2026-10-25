"""The app icon: the shipped .ico holds every size, and the writer packs a
valid .ico that Qt reads back image by image."""

import os
import struct

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QBuffer, QByteArray  # noqa: E402
from PySide6.QtGui import QColor, QImage, QImageReader  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quant_hub.gui.app_icon import ICO_PATH, SIZES, app_icon, ico_bytes, render  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    # A QApplication, not a QGuiApplication: later GUI tests reuse it.
    return QApplication.instance() or QApplication(["test"])


def _directory(data: bytes) -> list[tuple[int, int, int]]:
    """(side, length, offset) per .ico directory entry."""
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1)
    entries = []
    for i in range(count):
        width, height, _, _, planes, bpp, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * i)
        assert width == height and (planes, bpp) == (1, 32)
        entries.append((width or 256, length, offset))
    return entries


def test_shipped_ico_holds_every_size_as_png():
    data = ICO_PATH.read_bytes()
    entries = _directory(data)
    assert [side for side, _, _ in entries] == list(SIZES)
    for side, length, offset in entries:
        png = data[offset:offset + length]
        assert png.startswith(b"\x89PNG")
        image = QImage.fromData(png)
        assert (image.width(), image.height()) == (side, side)
    assert sorted(s.width() for s in app_icon().availableSizes()) == list(SIZES)


def test_ico_bytes_round_trips_through_qt():
    images = []
    for side, color in ((16, "#ff0000"), (48, "#00ff00"), (256, "#0000ff")):
        image = QImage(side, side, QImage.Format_ARGB32)
        image.fill(QColor(color))
        images.append(image)
    data = QByteArray(ico_bytes(images))
    buffer = QBuffer(data)
    buffer.open(QBuffer.ReadOnly)
    reader = QImageReader(buffer, b"ico")
    assert reader.imageCount() == 3
    read = []
    for _ in range(3):
        image = reader.read()
        read.append((image.width(), image.pixelColor(0, 0).name()))
        reader.jumpToNextImage()
    assert read == [(16, "#ff0000"), (48, "#00ff00"), (256, "#0000ff")]


def test_ico_bytes_rejects_non_square_or_oversized_images():
    with pytest.raises(ValueError):
        ico_bytes([QImage(32, 16, QImage.Format_ARGB32)])
    with pytest.raises(ValueError):
        ico_bytes([QImage(512, 512, QImage.Format_ARGB32)])


@pytest.mark.parametrize("side", [16, 32, 256])
def test_render_is_a_tile_with_transparent_corners(side):
    image = render(side)
    assert (image.width(), image.height()) == (side, side)
    assert image.pixelColor(0, 0).alpha() == 0                     # outside the rounded tile
    assert image.pixelColor(side // 2, side // 8).alpha() == 255   # inside it


def test_render_rejects_tiny_sides():
    with pytest.raises(ValueError):
        render(8)
