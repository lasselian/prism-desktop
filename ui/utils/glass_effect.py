import typing
from PyQt6.QtCore import Qt, QRect, QRectF
from PyQt6.QtGui import QPainter, QPainterPath, QPixmap, QColor, QPen


def draw_frosted_pill(
    painter: QPainter,
    pill_rect: typing.Union[QRect, QRectF],
    background_pixmap: QPixmap = None,
    bg_x_offset: float = 0,
    bg_y_offset: float = 0,
    corner_radius: int = 14,
    force_dark: bool = False,
) -> QColor:
    """
    Draws a frosted glass pill (or rounded rectangle) and returns the optimal
    contrasting text color (either dark color for light background or light color for dark background).

    If a background pixmap to blur over is provided, it extracts the crop at the pill's
    coordinatges (plus offsets), significantly blurs it by downscaling,
    computes average luminance to define tint/border/text colors, and then renders it.

    If no background pixmap is available, drawn as a solid semi-transparent dark pill.

    Args:
        painter: The QPainter currently being used to render the widget.
        pill_rect: The geometry of the pill / bubble.
        background_pixmap: The unblurred, scaled pixmap that sits completely behind the pill.
        bg_x_offset: The X offset from the widget's left to where the background pixmap starts drawing.
        bg_y_offset: The Y offset from the widget's top to where the background pixmap starts drawing.
        corner_radius: Border radius for the drawn rect (default 14 creates a pill if height is 28).

    Returns:
        QColor: The ideal contrasting text/icon color to use on top of this pill.
    """
    pill_path = QPainterPath()
    pill_path.addRoundedRect(QRectF(pill_rect), corner_radius, corner_radius)

    crops = _blurred_crop(background_pixmap, pill_rect, bg_x_offset, bg_y_offset)
    if crops is not None:
        small, blur = crops
        text_color, tint, border = _frost_colors(_average_luminance(small.toImage()), force_dark)
        painter.save()
        painter.setClipPath(pill_path)
        painter.drawPixmap(int(pill_rect.x()), int(pill_rect.y()), blur)
        painter.fillPath(pill_path, tint)
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(QRectF(pill_rect), corner_radius, corner_radius)
        painter.restore()
        return text_color

    # No artwork (or nothing to crop): an idle dark-transparent pill.
    painter.save()
    painter.fillPath(pill_path, QColor(0, 0, 0, 60))
    painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
    painter.drawRoundedRect(QRectF(pill_rect), corner_radius, corner_radius)
    painter.restore()
    return QColor(255, 255, 255, 220)  # light text for the dark fallback pill


def _blurred_crop(background_pixmap, pill_rect, bg_x_offset, bg_y_offset):
    """(tiny, blurred) copies of the background behind the pill, or None without one.

    Downscaling to a tenth and scaling back up is a cheap, heavy blur; the tiny
    copy is also what the luminance check reads.
    """
    if not background_pixmap or background_pixmap.isNull():
        return None
    pill_w = int(pill_rect.width())
    pill_h = int(pill_rect.height())
    src = QRect(int(bg_x_offset + pill_rect.x()), int(bg_y_offset + pill_rect.y()), pill_w, pill_h)
    crop = background_pixmap.copy(src)
    if crop.isNull():
        return None
    small = crop.scaled(
        max(1, int(pill_w * 0.1)),
        max(1, int(pill_h * 0.1)),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    blur = small.scaled(
        pill_w, pill_h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    return small, blur


def _average_luminance(img) -> float:
    pixels = [img.pixelColor(x, y) for y in range(img.height()) for x in range(img.width())]
    if not pixels:
        return 0
    n = len(pixels)
    return (
        0.2126 * sum(p.red() for p in pixels) / n
        + 0.7152 * sum(p.green() for p in pixels) / n
        + 0.0722 * sum(p.blue() for p in pixels) / n
    )


def _frost_colors(avg_lum: float, force_dark: bool):
    """(text, tint, border) colours that contrast with a background this bright."""
    if not force_dark and avg_lum > 128:
        # Light background: dark text, stronger frost over bright frames.
        return QColor(0, 0, 0, 235), QColor(255, 255, 255, 130), QColor(0, 0, 0, 50)
    # Dark background: light text, stronger dim for readability.
    return QColor(255, 255, 255, 250), QColor(0, 0, 0, 130), QColor(255, 255, 255, 70)
