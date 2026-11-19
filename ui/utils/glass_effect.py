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

    if background_pixmap and not background_pixmap.isNull():
        # Map pill rect to the background pixmap coordinates
        pill_src_x = bg_x_offset + pill_rect.x()
        pill_src_y = bg_y_offset + pill_rect.y()
        pill_w = int(pill_rect.width())
        pill_h = int(pill_rect.height())

        valid_src = QRect(int(pill_src_x), int(pill_src_y), pill_w, pill_h)
        bg_crop = background_pixmap.copy(valid_src)

        if not bg_crop.isNull():
            # Blur Simulation: Downscale -> Upscale
            # A tiny scale factor creates heavy optical blur when blown back up
            sm = bg_crop.scaled(
                max(1, int(pill_w * 0.1)),
                max(1, int(pill_h * 0.1)),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            blur = sm.scaled(
                pill_w, pill_h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
            )

            # Fast Luminance Check
            img = sm.toImage()
            rs = gs = bs = cnt = 0
            for sy in range(img.height()):
                for sx in range(img.width()):
                    pc = img.pixelColor(sx, sy)
                    rs += pc.red()
                    gs += pc.green()
                    bs += pc.blue()
                    cnt += 1

            avg_lum = (0.2126 * (rs / cnt) + 0.7152 * (gs / cnt) + 0.0722 * (bs / cnt)) if cnt > 0 else 0

            # Determine contrasting colors based on brightness
            if not force_dark and avg_lum > 128:
                # Background is LIGHT -> Use dark text/icons
                text_color = QColor(0, 0, 0, 235)
                bg_tint_color = QColor(255, 255, 255, 130)  # Stronger frost over bright frames
                border_color = QColor(0, 0, 0, 50)
            else:
                # Background is DARK -> Use light text/icons
                text_color = QColor(255, 255, 255, 250)
                bg_tint_color = QColor(0, 0, 0, 130)  # Stronger dim for readability
                border_color = QColor(255, 255, 255, 70)

            # Render frosted glass slice
            painter.save()
            painter.setClipPath(pill_path)
            painter.drawPixmap(int(pill_rect.x()), int(pill_rect.y()), blur)
            painter.fillPath(pill_path, bg_tint_color)  # Apply color tint overlay

            # Render glass border
            painter.setPen(QPen(border_color, 1))
            painter.drawRoundedRect(QRectF(pill_rect), corner_radius, corner_radius)
            painter.restore()

            return text_color

    # Fallback (No Artwork / Failed render)
    # Renders an idle dark-transparent pill
    painter.save()
    painter.fillPath(pill_path, QColor(0, 0, 0, 60))
    painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
    painter.drawRoundedRect(QRectF(pill_rect), corner_radius, corner_radius)
    painter.restore()

    return QColor(255, 255, 255, 220)  # Default light text for dark fallback pill
