import math
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from PyQt6.QtCore import Qt, QRect, QRectF, QPointF, QPropertyAnimation
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QBrush,
    QLinearGradient,
    QConicalGradient,
    QRadialGradient,
)
from ui.icons import get_icon, get_mdi_font, Icons
from core.utils import SYSTEM_FONT
from core.localization_manager import t
from core.temperature_utils import format_temperature
from ui.visuals.background_generator import BackgroundGenerator

_DEFAULT_SENSOR_COLOR = '#3c3c3c'


class DashboardButtonPainter:
    """Handles custom painting for DashboardButton."""

    @staticmethod
    def draw_bottom_bar(painter, rect, value, max_value, color, bar_height=4):
        """Draw a horizontal fill bar at the bottom of a rect.

        Reusable across entity types (3D printer progress, mower battery, etc.).

        Args:
            painter: Active QPainter instance.
            rect: QRectF or QRect bounding the area.
            value: Current fill value (e.g. progress or battery level).
            max_value: Value that represents 100 % fill.
            color: QColor for the filled portion.
            bar_height: Pixel height of the bar (default 4).
        """
        if value <= 0 or max_value <= 0:
            return
        fraction = min(float(value) / float(max_value), 1.0)
        bar_y = rect.y() + rect.height() - bar_height
        fill_w = rect.width() * fraction
        painter.fillRect(QRectF(rect.x(), bar_y, fill_w, bar_height), color)

    @staticmethod
    def draw_horizontal_bar_pill(
        painter, rect, fraction, fill_color, track_color, text=None, font=None, text_color=None
    ):
        pill_rect = QRectF(rect)
        radius = pill_rect.height() / 2.0

        pill_path = QPainterPath()
        pill_path.addRoundedRect(pill_rect, radius, radius)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawPath(pill_path)

        if fraction is not None:
            frac = max(0.0, min(1.0, float(fraction)))
            if frac > 0:
                fill_w = pill_rect.width() * frac
                fill_rect = QRectF(pill_rect.x(), pill_rect.y(), fill_w, pill_rect.height())
                grad = QLinearGradient(0, fill_rect.bottom(), 0, fill_rect.top())
                grad.setColorAt(0.0, fill_color)
                lighter = QColor(fill_color)
                lighter.setHsvF(
                    lighter.hsvHueF(), max(0.0, lighter.hsvSaturationF() - 0.05), min(1.0, lighter.valueF() + 0.12)
                )
                grad.setColorAt(1.0, lighter)
                painter.setClipPath(pill_path)
                painter.setBrush(QBrush(grad))
                painter.drawRect(fill_rect)
                painter.setClipping(False)

        if text:
            painter.setPen(text_color)
            painter.setFont(font)
            painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, text)

    @staticmethod
    def _perimeter_path(rect, corner_r):
        """Clockwise rounded-rect path starting at top-center (12 o'clock)."""
        x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
        cx = (x0 + x1) / 2.0
        cr = corner_r
        path = QPainterPath()
        path.moveTo(cx, y0)
        path.lineTo(x1 - cr, y0)
        path.arcTo(x1 - 2 * cr, y0, 2 * cr, 2 * cr, 90, -90)
        path.lineTo(x1, y1 - cr)
        path.arcTo(x1 - 2 * cr, y1 - 2 * cr, 2 * cr, 2 * cr, 0, -90)
        path.lineTo(x0 + cr, y1)
        path.arcTo(x0, y1 - 2 * cr, 2 * cr, 2 * cr, 270, -90)
        path.lineTo(x0, y0 + cr)
        path.arcTo(x0, y0, 2 * cr, 2 * cr, 180, -90)
        path.lineTo(cx, y0)
        return path

    @staticmethod
    def draw_perimeter_progress(painter, rect, fraction, fill_color, track_color, thickness=3, offset=8):
        """Draw a progress bar running around the button perimeter, inset by `offset` pixels."""
        inset_rect = QRectF(rect).adjusted(offset, offset, -offset, -offset)
        corner_r = max(8.0, 12.0 - offset)

        path = DashboardButtonPainter._perimeter_path(inset_rect, corner_r)
        total_len = path.length()
        if total_len <= 0:
            return

        track_pen = QPen(track_color, thickness)
        track_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(track_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        if fraction is None:
            return
        frac = max(0.0, min(1.0, float(fraction)))
        if frac <= 0:
            return

        filled_len = frac * total_len
        gap_len = max(0.001, total_len - filled_len)

        grad = QLinearGradient(inset_rect.topLeft(), inset_rect.bottomLeft())
        grad.setColorAt(0.0, fill_color)
        lighter = QColor(fill_color)
        lighter.setHsvF(
            lighter.hsvHueF(),
            max(0.0, lighter.hsvSaturationF() - 0.05),
            min(1.0, lighter.valueF() + 0.12),
        )
        grad.setColorAt(1.0, lighter)

        fill_pen = QPen(QBrush(grad), thickness)
        fill_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        # setDashPattern values are in pen-width units, so divide by thickness
        fill_pen.setDashPattern([filled_len / thickness, gap_len / thickness])
        painter.setPen(fill_pen)
        painter.drawPath(path)

    @staticmethod
    def draw_gauge_arc(painter, rect, fraction, fill_color, track_color, thickness=None):
        arc_rect = QRectF(rect)
        if thickness is None:
            thickness = max(6.0, min(arc_rect.width(), arc_rect.height()) / 10.0)

        inset = thickness / 2.0
        arc_rect.adjust(inset, inset, -inset, -inset)

        pen_track = QPen(track_color, thickness)
        pen_track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_track)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(arc_rect, 0, 180 * 16)

        if fraction is not None:
            frac = max(0.0, min(1.0, float(fraction)))
            if frac > 0:
                grad = QLinearGradient(0, arc_rect.bottom(), 0, arc_rect.top())
                grad.setColorAt(0.0, fill_color)
                lighter = QColor(fill_color)
                lighter.setHsvF(
                    lighter.hsvHueF(), max(0.0, lighter.hsvSaturationF() - 0.05), min(1.0, lighter.valueF() + 0.12)
                )
                grad.setColorAt(1.0, lighter)
                pen_fill = QPen(QBrush(grad), thickness)
                pen_fill.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen_fill)
                painter.drawArc(arc_rect, 180 * 16, -int(180 * 16 * frac))

    @staticmethod
    def _paint_lock_hold_ring(button):
        """Amber ring traced around the border while a lock's unlock-hold is in progress."""
        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        DashboardButtonPainter.draw_perimeter_progress(
            painter,
            QRectF(button.rect()),
            fraction=button._lock_hold_progress,
            fill_color=QColor('#F2A600'),
            track_color=QColor(255, 255, 255, 30),
            thickness=3,
            offset=6,
        )
        painter.end()

    @staticmethod
    def paint(button, event):
        """Main paint method."""
        cls = DashboardButtonPainter
        btn_type = button.config.get('type')
        cls._paint_type_content(button, btn_type)
        cls._paint_feedback(button, btn_type)
        if not button.config:
            cls._paint_empty_slot(button)
        if button._resize_handle_opacity > 0.01:
            cls._paint_resize_handle(button)
        cls._paint_bevel(button, btn_type)

    @staticmethod
    def _paint_type_content(button, btn_type):
        """What only some tile types draw themselves."""
        cls = DashboardButtonPainter
        if btn_type == 'media_player':
            cls._paint_media_player(button)
        elif btn_type == '3d_printer':
            cls._paint_3d_printer(button)
        elif btn_type == 'sun':
            cls._draw_sun_button(button)
        elif btn_type == 'widget' and button.config.get('display_style') in ('gauge', 'bar', 'perimeter'):
            cls._paint_widget_sensor(button)
        elif btn_type == 'lock' and getattr(button, '_lock_hold_progress', 0.0) > 0.0:
            cls._paint_lock_hold_ring(button)  # hold-to-unlock progress
        elif btn_type == 'camera':
            cls._paint_camera(button)

    @staticmethod
    def _paint_camera(button):
        """The latest camera frame, cropped to fill the tile, and the label pill over it."""
        cached = getattr(button, '_cached_display_pixmap', None)
        has_cached = bool(cached) and not cached.isNull()
        raw = getattr(button, '_last_camera_pixmap', None)
        # Without the cached rounded frame (first frame, or a resize race), scale the raw one.
        crop = None if has_cached or not raw else _cover_crop(raw, button.width(), button.height())

        if has_cached or crop:
            painter = QPainter(button)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(button.rect()), 12, 12)
            painter.setClipPath(path)  # also for the edge effects
            if has_cached:
                painter.drawPixmap(0, 0, cached)
            else:
                scaled, x, y = crop
                painter.drawPixmap(0, 0, scaled, x, y, button.width(), button.height())
            DashboardButtonPainter.draw_image_edge_effects(painter, QRectF(button.rect()), is_top_clamped=False)
            painter.end()

        label = button.config.get('label')
        if label:
            if has_cached:
                bg_pix, x_off, y_off = cached, 0, 0
            else:
                bg_pix, x_off, y_off = crop or (None, 0, 0)
            painter = QPainter(button)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            DashboardButtonPainter._draw_pill_label(painter, button.rect(), label, bg_pix, x_off, y_off)
            painter.end()

    @staticmethod
    def _paint_feedback(button, btn_type):
        """Transient overlays: script pulse, border animation, input_number blink and arrows, refresh dot."""
        cls = DashboardButtonPainter
        if button._pulse_opacity > 0.01:
            cls._paint_pulse(button)
        # Only draw special border if animating or if progress > 0
        if button.anim.state() == QPropertyAnimation.State.Running or button._anim_progress > 0.0:
            cls._paint_border_animation(button)
        if btn_type == 'input_number':
            if getattr(button, '_input_blink_opacity', 0.0) > 0.01:
                cls._paint_input_blink(button)
            if getattr(button, '_arrow_opacity', 0.0) > 0.01:
                cls._paint_input_arrows(button)
        # The opt-in refresh indicator comes last, so a full-tile flash at the
        # same moment (script pulse, input_number blink) never hides the dot.
        if getattr(button, '_refresh_flash_opacity', 0.0) > 0.01:
            cls._paint_refresh_indicator(button)

    @staticmethod
    def _paint_bevel(button, btn_type):
        """A subtle edge lip on every configured tile, so it reads as a glass/plastic tile."""
        if not button.config or btn_type == 'forbidden' or getattr(button, 'button_style', 'Gradient') != 'Gradient':
            return
        is_light = button.theme_manager and button.theme_manager.get_effective_theme() == 'light'
        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        DashboardButtonPainter.draw_button_bevel_edge(
            painter, QRectF(button.rect()), intensity_modifier=0.25, is_light=is_light
        )
        painter.end()

    @staticmethod
    def _paint_media_player(button):
        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = button.rect()
        is_playing = button._state == "playing"
        is_huge = button.span_x >= 2 and button.span_y >= 2  # 2x2+
        is_wide = button.span_x >= 2
        is_tall = button.span_y >= 2 and button.span_x < 2  # 1x2

        # Resolve accent color
        btn_color = button.config.get('color')
        if not btn_color and button.theme_manager:
            colors = button.theme_manager.get_colors()
            btn_color = colors.get('accent', '#4285F4')
        elif not btn_color:
            btn_color = "#4285F4"

        # --- Background ---
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), 12, 12)
        painter.setClipPath(clip_path)

        # Album art background (any size except 1x1, if enabled)
        has_art = button._album_art and not button._album_art.isNull()
        show_art = button.config.get('show_album_art', True)
        if (is_huge or is_tall or is_wide) and has_art and show_art:
            # Draw blurred/dimmed album art
            scaled = button._album_art.scaled(
                rect.width(),
                rect.height(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            # Center crop
            x_off = (scaled.width() - rect.width()) // 2
            y_off = (scaled.height() - rect.height()) // 2
            painter.drawPixmap(0, 0, scaled, x_off, y_off, rect.width(), rect.height())

            # Gradient overlay: user color at bottom -> transparent at top
            # FIX: Use explicit TopLeft/BottomLeft for full coverage
            grad = QLinearGradient(QPointF(rect.topLeft()), QPointF(rect.bottomLeft()))
            grad.setColorAt(0.0, QColor(0, 0, 0, 0))  # Top: transparent
            overlay_color = QColor(btn_color)
            overlay_color.setAlpha(255)
            grad.setColorAt(1.0, overlay_color)  # Bottom: user color
            painter.fillRect(rect, grad)
        else:
            # No Art -> Procedural Background

            # Generate deterministic seed based on media info or button slot
            media_state = button._media_state or {}
            attrs = media_state.get('attributes', {})

            title = attrs.get('media_title', '')
            artist = attrs.get('media_artist', '')

            if title or artist:
                # crc32, not hash(): str hashes change on every start, and the
                # same track should get the same background each time.
                seed = zlib.crc32(f"{title}{artist}".encode("utf-8"))
            else:
                seed = button.slot

            animated = button.config.get('animated_bg', True)
            w, h = rect.width(), rect.height()

            if animated:
                # --- Animated prismatic light field ---
                button._ensure_anim_bg_layers(seed)
                bg = BackgroundGenerator.render_frame(
                    w,
                    h,
                    button._anim_bg_layers,
                    button._anim_bg_frame,
                    tiny_pixmap=button._anim_bg_tiny,
                )
                painter.drawPixmap(0, 0, bg)
            else:
                # --- Static background (original behaviour) ---
                light_mode = bool(button.theme_manager and button.theme_manager.get_effective_theme() == 'light')
                bg_pixmap = BackgroundGenerator.generate(w, h, seed=seed, light_mode=light_mode)
                painter.drawPixmap(0, 0, bg_pixmap)

            # Gradient overlay (for text readability)
            grad = QLinearGradient(QPointF(rect.topLeft()), QPointF(rect.bottomLeft()))
            grad.setColorAt(0.0, QColor(0, 0, 0, 0))
            overlay_color = QColor(btn_color)
            overlay_color.setAlpha(200 if is_playing else 120)
            grad.setColorAt(1.0, overlay_color)
            painter.fillRect(rect, grad)

        # === PILL LABEL (Apple-like) ===
        # Always show Pill if Huge 2x2+
        label = button.config.get('label')
        if is_huge and label:
            if has_art and show_art:
                # Same crop offsets as the art drawn above, so the frosted pill
                # samples the part of the art that is actually behind it.
                DashboardButtonPainter._draw_pill_label(painter, rect, label, scaled, x_off, y_off)
            else:
                DashboardButtonPainter._draw_pill_label(painter, rect, label)

        # --- Icons ---
        is_playing = button._state == 'playing'
        is_paused = button._state == 'paused'

        play_icon = Icons.PLAY_PAUSE
        if is_playing:
            play_icon = Icons.PAUSE
        elif is_paused:
            play_icon = Icons.PLAY

        painter.setPen(QColor("white"))

        # Get media info
        attrs = button._media_state.get('attributes', {})
        title = attrs.get('media_title', '')
        artist = attrs.get('media_artist', '')

        if is_huge:
            # === 2x2: Now Playing Layout ===
            # Layout: album art top | transport controls middle | text bottom
            w = rect.width()
            h = rect.height()

            # Transport controls (center area)
            ctrl_h = 40
            ctrl_y = h // 2 - ctrl_h // 2
            painter.setFont(get_mdi_font(30))

            r_prev = QRect(0, ctrl_y, w // 3, ctrl_h)
            painter.drawText(r_prev, Qt.AlignmentFlag.AlignCenter, Icons.PREVIOUS)

            r_play = QRect(w // 3, ctrl_y, w // 3, ctrl_h)
            painter.drawText(r_play, Qt.AlignmentFlag.AlignCenter, play_icon)

            r_next = QRect((w // 3) * 2, ctrl_y, w // 3, ctrl_h)
            painter.drawText(r_next, Qt.AlignmentFlag.AlignCenter, Icons.NEXT)

            # === SONG INFO PILL (bottom area) ===
            if title or artist:
                # Build single-line display string: "Artist - Title"
                if artist and title:
                    info_text = f"{artist}  \u2013  {title}"
                else:
                    info_text = title or artist

                # Measure text to determine pill size
                info_font = QFont(SYSTEM_FONT, 8, QFont.Weight.DemiBold)
                info_fm = QFontMetrics(info_font)

                text_w = info_fm.horizontalAdvance(info_text)
                padding_h = 20  # Horizontal padding
                info_pill_w = min(text_w + padding_h * 2, w * 0.9)
                info_pill_h = 28  # Single line pill

                info_pill_x = (w - info_pill_w) / 2
                info_pill_y = h - info_pill_h - 8  # 8px from bottom
                info_pill_rect = QRectF(info_pill_x, info_pill_y, info_pill_w, info_pill_h)

                # --- Pill Background ---
                info_pill_path = QPainterPath()
                info_pill_path.addRoundedRect(info_pill_rect, 14, 14)

                if has_art and show_art:
                    # Blurred art background
                    from ui.utils.glass_effect import draw_frosted_pill

                    info_text_color = draw_frosted_pill(
                        painter, info_pill_rect, background_pixmap=scaled, bg_x_offset=x_off, bg_y_offset=y_off
                    )
                else:
                    # No art fallback
                    info_text_color = QColor(255, 255, 255, 220)
                    painter.save()
                    painter.fillPath(info_pill_path, QColor(0, 0, 0, 60))
                    pen = QPen(QColor(255, 255, 255, 30))
                    pen.setWidth(1)
                    painter.setPen(pen)
                    painter.drawRoundedRect(info_pill_rect, 14, 14)
                    painter.restore()

                # --- Draw Text ---
                painter.setFont(info_font)
                painter.setPen(info_text_color)
                max_text_area = info_pill_w - padding_h * 2
                fm = painter.fontMetrics()
                elided = fm.elidedText(info_text, Qt.TextElideMode.ElideRight, int(max_text_area))
                painter.drawText(info_pill_rect, Qt.AlignmentFlag.AlignCenter, elided)

        elif is_tall:
            # === 1x2: Tall Layout ===
            w = rect.width()
            h = rect.height()

            # Play/pause (upper area)
            painter.setFont(get_mdi_font(30))
            play_rect = QRect(0, 0, w, h // 2)
            painter.drawText(play_rect, Qt.AlignmentFlag.AlignCenter, play_icon)

            # Prev / Next (bottom area)
            ctrl_y = h // 2 + 8
            ctrl_h = h // 2 - 8
            painter.setFont(get_mdi_font(24))

            r_prev = QRect(0, ctrl_y, w // 2, ctrl_h)
            painter.drawText(r_prev, Qt.AlignmentFlag.AlignCenter, Icons.PREVIOUS)

            r_next = QRect(w // 2, ctrl_y, w // 2, ctrl_h)
            painter.drawText(r_next, Qt.AlignmentFlag.AlignCenter, Icons.NEXT)

        elif is_wide:
            # === 2x1: Wide Transport ===
            w = rect.width()
            h = rect.height()
            painter.setFont(get_mdi_font(28))

            r_prev = QRect(0, 0, w // 3, h)
            painter.drawText(r_prev, Qt.AlignmentFlag.AlignCenter, Icons.PREVIOUS)

            r_play = QRect(w // 3, 0, w // 3, h)
            painter.drawText(r_play, Qt.AlignmentFlag.AlignCenter, play_icon)

            r_next = QRect((w // 3) * 2, 0, w // 3, h)
            painter.drawText(r_next, Qt.AlignmentFlag.AlignCenter, Icons.NEXT)

        else:
            # === 1x1: Compact Play/Pause ===
            painter.setFont(get_mdi_font(28))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, play_icon)

        painter.end()

    @staticmethod
    def _paint_widget_sensor(button):
        style = button.config.get('display_style')
        if style not in ('gauge', 'bar', 'perimeter'):
            return

        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = button.rect()

        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), 12, 12)
        painter.setClipPath(clip_path)

        # Resolve colors
        accent_hex = '#4285F4'
        text_hex = '#ffffff'
        if button.theme_manager:
            colors = button.theme_manager.get_colors()
            accent_hex = colors.get('accent', accent_hex)
            text_hex = colors.get('text', text_hex)

        # User-picked color (from edit menu) drives the gauge/bar fill; fall back to theme accent.
        custom_color = button.config.get('color')
        # "#3c3c3c" is the sentinel for "default sensor color" — treat as unset.
        if custom_color and custom_color.lower() != _DEFAULT_SENSOR_COLOR:
            fill_color = QColor(custom_color)
        else:
            fill_color = QColor(accent_hex)
        text_color = QColor(text_hex)
        track_color = QColor(text_hex)
        track_color.setAlpha(40)

        fraction = getattr(button, '_sensor_fraction', None)
        gauge_fraction = getattr(button, '_gauge_fraction', fraction)
        text = getattr(button, '_sensor_text', '') or ''

        has_label = bool(button.config.get('label'))
        label_reserve = 24 if has_label else 0

        if style == 'bar':
            # Upper area holds the pill; lower area (if label exists) is left for the QLabel.
            top_pad = 8
            available_h = rect.height() - label_reserve - top_pad
            pill_h = int(max(12, min(available_h * 0.60, 24)))
            margin_x = 18
            pill_y = rect.y() + top_pad + (available_h - pill_h) / 2
            pill_rect = QRectF(
                rect.x() + margin_x,
                pill_y,
                rect.width() - 2 * margin_x,
                pill_h,
            )
            is_1x1 = button.span_x == 1 and button.span_y == 1
            font_size = max(6, min(9, int(pill_h * 0.4))) if is_1x1 else max(7, min(12, int(pill_h * 0.5)))
            DashboardButtonPainter.draw_horizontal_bar_pill(
                painter,
                pill_rect,
                fraction=fraction,
                fill_color=fill_color,
                track_color=track_color,
                text=text,
                font=QFont(SYSTEM_FONT, font_size, QFont.Weight.Bold if is_1x1 else QFont.Weight.Normal),
                text_color=text_color,
            )
        elif style == 'gauge':
            is_1x1 = button.span_x == 1 and button.span_y == 1
            is_tall_1col = button.span_x == 1 and button.span_y >= 2
            if is_tall_1col:
                # Tall single-column: arc centered in upper half, value + label below.
                label_text = button.config.get('label', '')
                top_pad = 16
                arc_size = int(min(rect.width() - 16, (rect.height() * 0.55)))
                arc_x = rect.x() + (rect.width() - arc_size) / 2
                arc_y = rect.y() + top_pad
                arc_rect = QRectF(arc_x, arc_y, arc_size, arc_size)
                DashboardButtonPainter.draw_gauge_arc(
                    painter,
                    arc_rect,
                    fraction=gauge_fraction,
                    fill_color=fill_color,
                    track_color=track_color,
                )
                baseline_y = arc_y + arc_size / 2
                remaining_h = rect.y() + rect.height() - baseline_y
                value_h = remaining_h * 0.5 if label_text else remaining_h
                value_rect = QRectF(rect.x(), baseline_y, rect.width(), value_h)
                painter.setPen(text_color)
                painter.setFont(QFont(SYSTEM_FONT, 13, QFont.Weight.Bold))
                painter.drawText(value_rect, Qt.AlignmentFlag.AlignCenter, text)
                if label_text:
                    label_rect = QRectF(rect.x(), baseline_y + value_h, rect.width(), remaining_h * 0.5)
                    label_color = QColor(text_color)
                    label_color.setAlpha(160)
                    painter.setPen(label_color)
                    painter.setFont(QFont(SYSTEM_FONT, 9, QFont.Weight.DemiBold))
                    painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label_text.upper())
            elif is_1x1:
                # 1x1 gauge: arc fills upper portion; value text drawn below the arc.
                value_strip_h = 20
                max_arc_w = int((rect.width() - 8) * 0.72)
                arc_size = max(min(max_arc_w, int((rect.width() - 8) * 0.72)), 24)
                # Center the arc+text block vertically with the arc sitting just above mid
                content_h = arc_size // 2 + value_strip_h + 4
                top_pad = max(6, (rect.height() - content_h) // 2)
                arc_x = rect.x() + (rect.width() - arc_size) / 2
                arc_y = rect.y() + top_pad
                arc_rect = QRectF(arc_x, arc_y, arc_size, arc_size)
                DashboardButtonPainter.draw_gauge_arc(
                    painter,
                    arc_rect,
                    fraction=gauge_fraction,
                    fill_color=fill_color,
                    track_color=track_color,
                )
                # Value text right under the arc's flat baseline (= vertical center of arc_rect).
                baseline_y = arc_y + arc_size / 2
                text_rect = QRectF(
                    rect.x(),
                    baseline_y,
                    rect.width(),
                    rect.y() + rect.height() - baseline_y,
                )
                painter.setPen(text_color)
                painter.setFont(QFont(SYSTEM_FONT, 11))
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)
            else:
                # 2x1+: arc on the left, QLabels on the right. Position arc_rect so that
                # the VISIBLE arc (top half of arc_rect) is vertically centered in the button.
                arc_size = min(rect.height() - 12, rect.width() // 2 - 10)
                arc_size = max(arc_size, 32)
                arc_x = rect.x() + 13
                # Visible arc spans [arc_y, arc_y + arc_size/2]; center its midpoint on rect center.
                arc_y = rect.y() + rect.height() / 2 - arc_size / 4
                arc_rect = QRectF(arc_x, arc_y, arc_size, arc_size)
                DashboardButtonPainter.draw_gauge_arc(
                    painter,
                    arc_rect,
                    fraction=gauge_fraction,
                    fill_color=fill_color,
                    track_color=track_color,
                )

        if style == 'perimeter':
            DashboardButtonPainter.draw_perimeter_progress(
                painter,
                QRectF(rect),
                fraction=getattr(button, '_perimeter_fraction', fraction),
                fill_color=fill_color,
                track_color=track_color,
            )

        painter.end()

    @staticmethod
    def _paint_3d_printer(button):
        view = _printer_view(button)
        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if button.span_x >= 2 and button.span_y >= 2:
            _paint_printer_huge(painter, button, view)
        elif button.span_x >= 2:
            _paint_printer_wide(painter, button, view)
        elif button.span_y >= 2:
            _paint_printer_tall(painter, button, view)
        else:
            _paint_printer_compact(painter, button, view)
        painter.end()

    @staticmethod
    def _paint_pulse(button):
        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Use custom color or accent
        c = QColor("#0078d4")
        if button.theme_manager:
            colors = button.theme_manager.get_colors()
            c = QColor(colors.get('accent', '#0078d4'))

        # Allow custom color override
        if button.config and 'color' in button.config:
            c = QColor(button.config['color'])

        c.setAlphaF(button._pulse_opacity)

        # Draw rounded rect overlay
        painter.setBrush(QBrush(c))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(button.rect()), 12, 12)
        painter.end()

    @staticmethod
    def _paint_refresh_indicator(button):
        """Small corner dot marking a live push from Home Assistant (opt-in).

        Deliberately ignores the tile's accent/custom color: unlike the
        script pulse (a full-tile wash, where matching the tile's color
        looks intentional), this dot has to stand out *against* the tile —
        including tiles whose custom color equals the theme accent, where a
        same-colored dot would be invisible. A white fill with a dark
        outline reads on light and dark tiles alike.
        """
        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        opacity = button._refresh_flash_opacity
        radius = 3.5
        margin = 8.0
        cx = button.rect().width() - margin - radius
        cy = margin + radius
        center = QPointF(cx, cy)

        outline = QColor(0, 0, 0)
        outline.setAlphaF(0.35 * opacity)
        pen = QPen(outline)
        pen.setWidthF(1.2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius + 0.6, radius + 0.6)

        fill = QColor(255, 255, 255)
        fill.setAlphaF(opacity)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(fill))
        painter.drawEllipse(center, radius, radius)

        painter.end()

    @staticmethod
    def _paint_input_blink(button):
        """Draw a quick white flash over the button when value changes."""
        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        c = QColor(255, 255, 255)
        c.setAlphaF(button._input_blink_opacity)

        painter.setBrush(QBrush(c))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(button.rect()), 12, 12)
        painter.end()

    @staticmethod
    def _paint_input_arrows(button):
        """Draw minimalist up/down arrows on hover."""
        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Determine color based on theme
        if button.theme_manager:
            colors = button.theme_manager.get_colors()
            base_text = colors.get('text', '#ffffff')
            is_light = base_text == '#1e1e1e'
            c = QColor(0, 0, 0) if is_light else QColor(255, 255, 255)
        else:
            c = QColor(255, 255, 255)

        c.setAlphaF(getattr(button, '_arrow_opacity', 0.0) * 0.6)
        painter.setPen(c)
        rect = button.rect()
        w, h = rect.width(), rect.height()

        if button.span_y == 1:
            if button.span_x == 1:
                # No arrows for 1x1 layout - relies purely on drag
                pass
            else:
                # Left and right vertical centers
                font_size = 20 if button.span_x == 1 else 24
                painter.setFont(get_mdi_font(font_size))

                padding = 12
                left_rect = QRectF(padding, 0, w * 0.25, h)
                painter.drawText(
                    left_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, Icons.CHEVRON_LEFT
                )

                right_rect = QRectF(w * 0.75 - padding, 0, w * 0.25, h)
                painter.drawText(
                    right_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, Icons.CHEVRON_RIGHT
                )
        else:
            painter.setFont(get_mdi_font(24))

            # Top quarter for Up Arrow
            up_rect = QRectF(0, 8, w, h * 0.25)
            painter.drawText(up_rect, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignTop, Icons.CHEVRON_UP)

            # Bottom quarter for Down Arrow
            down_rect = QRectF(0, h * 0.75 - 8, w, h * 0.25)
            painter.drawText(down_rect, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignBottom, Icons.CHEVRON_DOWN)

        painter.end()

    @staticmethod
    def _paint_border_animation(button):
        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Interactive 'Press' feedback
        speed = 0.9 if button._border_effect == 'Prism Shard' else 1.5
        if button._border_effect == 'Liquid Mercury':
            speed = 1.2
        angle = button._anim_progress * 360.0 * speed

        opacity = 1.0
        if button._anim_progress > 0.8:
            opacity = (1.0 - button._anim_progress) / 0.2

        opacity = max(0.0, opacity)

        painter.setOpacity(opacity)
        rect = button.rect().adjusted(1, 1, -1, -1)

        if button._border_effect == 'Rainbow':
            DashboardButtonPainter.draw_rainbow_border(painter, rect, angle)
        elif button._border_effect == 'Aurora Borealis':
            DashboardButtonPainter.draw_aurora_border(painter, rect, angle)
        elif button._border_effect == 'Prism Shard':
            DashboardButtonPainter.draw_prism_shard_border(painter, rect, angle)
        elif button._border_effect == 'Liquid Mercury':
            DashboardButtonPainter.draw_liquid_mercury_border(painter, rect, angle)
        painter.end()

    @staticmethod
    def _paint_empty_slot(button):
        # Dashed border for empty slots (drawn over stylesheet bg)
        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = button.rect().adjusted(1, 1, -1, -1)

        # Theme-aware border color
        is_light = button.theme_manager and button.theme_manager.get_effective_theme() == 'light'
        border_color = QColor("#c0c0c0") if is_light else QColor("#555555")

        if not hasattr(button, '_dashed_pen') or getattr(button, '_dashed_pen_light', None) != is_light:
            button._dashed_pen = QPen(border_color)
            button._dashed_pen.setStyle(Qt.PenStyle.DashLine)
            button._dashed_pen.setWidth(2)
            button._dashed_pen_light = is_light

        painter.setPen(button._dashed_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 10, 10)

        painter.end()

    @staticmethod
    def _paint_resize_handle(button):
        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.setOpacity(button._resize_handle_opacity)

        # Bottom-right corner
        r = button.rect()
        radius = 12  # Match button border radius
        handle_size = 28  # Bigger handle

        path = QPainterPath()

        # 1. Start on bottom edge, left of corner
        path.moveTo(r.right() - handle_size, r.bottom())

        # 2. Outer Edge: Follow the button's rounded corner exactly
        corner_rect = QRectF(r.right() - 2 * radius, r.bottom() - 2 * radius, 2 * radius, 2 * radius)

        path.lineTo(r.right() - radius, r.bottom())  # Line to start of arc
        path.arcTo(corner_rect, 270, 90)  # The corner itself
        path.lineTo(r.right(), r.bottom() - handle_size)  # Line to handle top

        # 3. Inner Edge: Curve inward back to start
        path.quadTo(r.right() - 4, r.bottom() - 4, r.right() - handle_size, r.bottom())

        path.closeSubpath()

        # Glass Style
        painter.setPen(Qt.PenStyle.NoPen)

        # Gradient for glass/shiny look
        grad = QLinearGradient(QPointF(r.right() - handle_size, r.bottom() - handle_size), QPointF(r.bottomRight()))
        grad.setColorAt(0.0, QColor(255, 255, 255, 120))  # Start brighter
        grad.setColorAt(1.0, QColor(255, 255, 255, 10))  # Fade out

        painter.setBrush(QBrush(grad))
        painter.drawPath(path)

        # Accent line (Inner Edge only) for sharpness
        pen = QPen(QColor(255, 255, 255, 70))
        pen.setWidthF(2.0)
        painter.strokePath(path, pen)
        painter.end()

    @staticmethod
    def _draw_pill_label(
        painter,
        rect,
        label,
        background_pixmap=None,
        x_off=0,
        y_off=0,
        position='top-center',
        forced_bg_color=None,
        forced_text_color=None,
        force_dark=False,
    ):
        """Draws a pill label (frosted glass by default, or solid if colors provided)."""
        if not label:
            return

        # Calculate Pill Geometry
        if position == 'top-right':
            font = QFont(SYSTEM_FONT, 10, QFont.Weight.DemiBold)
            old_font = painter.font()
            painter.setFont(font)
            fm = painter.fontMetrics()

            text_w = fm.horizontalAdvance(label.upper())
            painter.setFont(old_font)  # restore

            pill_w = max(48, text_w + 24)
            pill_h = 28
            pill_x = rect.width() - pill_w - 12
            pill_y = 12
        elif position == 'top-left':
            font = QFont(SYSTEM_FONT, 10, QFont.Weight.DemiBold)
            old_font = painter.font()
            painter.setFont(font)
            fm = painter.fontMetrics()

            text_w = fm.horizontalAdvance(label.upper())
            painter.setFont(old_font)  # restore

            pill_w = max(48, text_w + 24)
            pill_h = 28
            pill_x = 12
            pill_y = 12
        else:  # top-center (Upper Third)
            font = QFont(SYSTEM_FONT, 10, QFont.Weight.DemiBold)
            old_font = painter.font()
            painter.setFont(font)
            fm = painter.fontMetrics()

            text_w = fm.horizontalAdvance(label.upper())
            painter.setFont(old_font)  # restore

            pill_w = min(rect.width() * 0.9, max(48, text_w + 24))  # Max width constraint
            pill_h = 28
            pill_x = (rect.width() - pill_w) / 2
            pill_y = rect.height() * 0.15  # 15% from top

        pill_rect = QRectF(pill_x, pill_y, pill_w, pill_h)

        if forced_bg_color:
            # Draw solid pill
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(forced_bg_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(pill_rect, 14, 14)
            painter.restore()
            text_color = forced_text_color or QColor("white")
        else:
            # Draw the frosted background and auto-magically get the correct text color contrast
            from ui.utils.glass_effect import draw_frosted_pill

            text_color = draw_frosted_pill(
                painter,
                pill_rect,
                background_pixmap=background_pixmap,
                bg_x_offset=x_off,
                bg_y_offset=y_off,
                force_dark=force_dark,
            )

        font = QFont(SYSTEM_FONT, 10, QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, label.upper())

    @staticmethod
    def draw_rainbow_border(painter, rect, angle):
        colors = ["#4285F4", "#EA4335", "#FBBC05", "#34A853", "#4285F4"]
        DashboardButtonPainter.draw_gradient_border(painter, rect, angle, colors)

    @staticmethod
    def draw_aurora_border(painter, rect, angle):
        colors = ["#00C896", "#0078FF", "#8C00FF", "#0078FF", "#00C896"]
        DashboardButtonPainter.draw_gradient_border(painter, rect, angle, colors)

    @staticmethod
    def draw_prism_shard_border(painter, rect, angle):
        # Muted jewel tones for a less "neon" look
        colors = ["#26C6DA", "#EC407A", "#FFCA28", "#CFD8DC", "#26C6DA"]
        DashboardButtonPainter.draw_gradient_border(painter, rect, angle, colors)

    @staticmethod
    def draw_liquid_mercury_border(painter, rect, angle):
        # Gunmetal Chrome: Darker, more sophisticated palette
        colors = ["#37474F", "#78909C", "#CFD8DC", "#ECEFF1", "#CFD8DC", "#78909C", "#37474F"]
        DashboardButtonPainter.draw_gradient_border(painter, rect, angle, colors)

    @staticmethod
    def draw_gradient_border(painter, rect, angle, colors):
        """Draw a conical gradient border."""
        gradient = QConicalGradient(QPointF(rect.center()), angle)

        for i, color in enumerate(colors):
            gradient.setColorAt(i / (len(colors) - 1), QColor(color))

        pen = QPen()
        pen.setWidth(2)
        pen.setBrush(QBrush(gradient))

        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 9, 9)

    @staticmethod
    def draw_image_edge_effects(painter, rect, is_top_clamped=False, is_light=False, corner_radius=12):
        """Draws a soft inner vignette shadow and a bright top-left specular highlight to make camera feeds look like physical glass rather than hard stickers."""
        painter.save()

        # 1. Inner Vignette — clipped to rounded rect so Multiply mode doesn't
        #    bleed into transparent widget corners and create a visible dark box
        clip = QPainterPath()
        clip.addRoundedRect(rect, corner_radius, corner_radius)
        painter.setClipPath(clip)

        center = rect.center()
        radius = max(rect.width(), rect.height()) / 1.5

        grad = QRadialGradient(center, radius)
        grad.setColorAt(0.7, QColor(0, 0, 0, 0))  # Transparent core
        grad.setColorAt(1.0, QColor(0, 0, 0, 100))  # Darkened exterior edges

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        painter.fillRect(rect, grad)

        # Restore for the highlight pass
        painter.setClipping(False)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # 2. Specular Glass Highlight & Perimeter
        DashboardButtonPainter.draw_button_bevel_edge(
            painter,
            rect,
            intensity_modifier=1.3,
            is_top_clamped=is_top_clamped,
            is_light=is_light,
            corner_radius=corner_radius,
        )

        painter.restore()

    @staticmethod
    def draw_button_bevel_edge(
        painter, rect, intensity_modifier=1.0, is_top_clamped=False, corner_radius=11, is_light=False
    ):
        """Draws a top-left specular highlight and perimeter outline to give buttons physical depth."""
        painter.save()

        line_rect = QRectF(rect)

        def make_path(adj_rect, r):
            p = QPainterPath()
            if is_top_clamped:
                p.moveTo(adj_rect.bottomLeft())
                p.lineTo(adj_rect.left(), adj_rect.top() + r)
                p.arcTo(adj_rect.left(), adj_rect.top(), r * 2, r * 2, 180, -90)
                p.lineTo(adj_rect.right() - r, adj_rect.top())
                p.arcTo(adj_rect.right() - r * 2, adj_rect.top(), r * 2, r * 2, 90, -90)
                p.lineTo(adj_rect.bottomRight())
                p.closeSubpath()
            else:
                p.addRoundedRect(adj_rect, r, r)
            return p

        if is_light:
            # Light mode: skip white highlight (invisible), just draw a visible dark perimeter
            perimeter_pen = QPen(QColor(0, 0, 0, 28))
            perimeter_pen.setWidthF(1.0)
            painter.setPen(perimeter_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(make_path(line_rect.adjusted(0.5, 0.5, -0.5, -0.5), corner_radius + 0.5))
        else:
            # Dark mode: white top-left highlight gradient + subtle white perimeter
            highlight_grad = QLinearGradient(line_rect.topLeft(), line_rect.topRight())
            alpha_start = min(255, int(45 * intensity_modifier))
            alpha_mid = min(255, int(15 * intensity_modifier))
            highlight_grad.setColorAt(0.0, QColor(255, 255, 255, alpha_start))
            highlight_grad.setColorAt(0.4, QColor(255, 255, 255, alpha_mid))
            highlight_grad.setColorAt(1.0, QColor(255, 255, 255, 0))

            pen = QPen(QBrush(highlight_grad), 2.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(make_path(line_rect.adjusted(1, 1, -1, -1), corner_radius))

            perimeter_alpha = min(255, int(15 * intensity_modifier))
            perimeter_pen = QPen(QColor(255, 255, 255, perimeter_alpha))
            perimeter_pen.setWidthF(1.0)
            painter.setPen(perimeter_pen)
            painter.drawPath(make_path(line_rect.adjusted(0.5, 0.5, -0.5, -0.5), corner_radius + 0.5))

        painter.restore()

    @staticmethod
    def _draw_sun_button(button):
        """Draw the sun entity as a gauge arc (sensor-style) with the sun/moon travelling along it."""

        def parse_iso(s):
            return datetime.fromisoformat(s.replace('Z', '+00:00'))

        def format_remaining(total_secs):
            days = total_secs // 86400
            hours = (total_secs % 86400) // 3600
            mins = (total_secs % 3600) // 60
            if days >= 1:
                return f"{int(days)}d"
            if hours >= 1:
                return f"{int(hours)}h"
            return f"{int(mins)}m"

        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(button.rect())
        w = rect.width()
        h = rect.height()

        clip_path = QPainterPath()
        clip_path.addRoundedRect(rect.adjusted(1, 1, -1, -1), 12, 12)
        painter.setClipPath(clip_path)

        # Colors
        is_light = getattr(button, 'theme_manager', None) and button.theme_manager.get_effective_theme() == 'light'
        track_color = QColor(0, 0, 0, 80) if is_light else QColor(255, 255, 255, 65)
        text_hex = '#1e1e1e' if is_light else '#ffffff'
        if getattr(button, 'theme_manager', None):
            colors = button.theme_manager.get_colors()
            text_hex = colors.get('text', text_hex)
        text_color = QColor(text_hex)

        # State
        above = getattr(button, 'sun_state', 'unknown') == 'above_horizon'
        next_rising_str = getattr(button, 'sun_next_rising', None)
        next_setting_str = getattr(button, 'sun_next_setting', None)

        # Sun ratio: 0 = sunrise, 1 = sunset
        ratio = None
        if above and next_rising_str and next_setting_str:
            try:
                now = datetime.now(timezone.utc)
                next_rising = parse_iso(next_rising_str)
                next_setting = parse_iso(next_setting_str)
                sunrise = next_rising - timedelta(days=1)
                total_secs = (next_setting - sunrise).total_seconds()
                elapsed = (now - sunrise).total_seconds()
                ratio = max(0.0, min(1.0, elapsed / total_secs)) if total_secs > 0 else 0.5
            except Exception:
                ratio = 0.5

        # Moon ratio: 0 = prev sunset, 1 = next sunrise
        moon_ratio = None
        if not above and next_rising_str and next_setting_str:
            try:
                now = datetime.now(timezone.utc)
                next_rising = parse_iso(next_rising_str)
                next_setting = parse_iso(next_setting_str)
                prev_sunset = next_setting - timedelta(days=1)
                night_dur = (next_rising - prev_sunset).total_seconds()
                elapsed = (now - prev_sunset).total_seconds()
                moon_ratio = max(0.0, min(1.0, elapsed / night_dur)) if night_dur > 0 else 0.5
            except Exception:
                moon_ratio = 0.5

        # Layout — mirrors sensor gauge positioning
        is_wide = button.span_x >= 2
        is_1x1 = button.span_x == 1 and button.span_y == 1

        if is_wide:
            arc_size = int(min(h - 12, w // 2 - 10))
            arc_size = max(arc_size, 32)
            arc_x = rect.x() + 13
            arc_y = rect.y() + h / 2 - arc_size / 4
        elif is_1x1:
            arc_size = int((w - 8) * 0.72)
            arc_size = max(arc_size, 24)
            content_h = arc_size // 2 + 24
            top_pad = max(6, int((h - content_h) // 2))
            arc_x = rect.x() + (w - arc_size) / 2
            arc_y = rect.y() + top_pad
        else:
            # tall single column
            arc_size = int(min(w - 16, h * 0.45))
            arc_size = max(arc_size, 32)
            arc_x = rect.x() + (w - arc_size) / 2
            arc_y = rect.y() + 12

        # Draw track arc (top semicircle, unfilled — sun/moon travel along it)
        thickness = max(6.0, arc_size / 10.0)
        inset = thickness / 2.0
        draw_rect = QRectF(arc_x, arc_y, arc_size, arc_size).adjusted(inset, inset, -inset, -inset)

        pen_track = QPen(track_color, thickness)
        pen_track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_track)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(draw_rect, 0, 180 * 16)

        # Point on the arc: r=0 → left/sunrise, r=0.5 → top/noon, r=1 → right/sunset
        def arc_point(r):
            cx = draw_rect.center().x()
            cy = draw_rect.center().y()
            rx = draw_rect.width() / 2
            ry = draw_rect.height() / 2
            theta = math.radians(180.0 - r * 180.0)
            return cx + rx * math.cos(theta), cy - ry * math.sin(theta)

        phase = getattr(button, 'sun_pulse_phase', 0.0)
        pulse = (1.0 + math.sin(phase * 2.0 * math.pi)) / 2.0
        entry = getattr(button, '_sun_entry_fraction', 1.0)

        # Sun dot travelling along the arc
        if above and ratio is not None:
            animated_ratio = ratio * entry
            dot_x, dot_y = arc_point(animated_ratio)
            sin_t = math.sin(animated_ratio * math.pi)
            dot_color = QColor(255, int(100 + 115 * sin_t), int(20 * (1.0 - sin_t)))
            base_r = 5.0 + 2.0 * sin_t
            glow_c = QColor(dot_color)
            glow_c.setAlpha(int(22 + 14 * pulse))
            painter.setBrush(QBrush(glow_c))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(dot_x, dot_y), base_r + 2.5 + 1.0 * pulse, base_r + 2.5 + 1.0 * pulse)
            painter.setBrush(QBrush(dot_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(dot_x, dot_y), base_r, base_r)

        # Moon sphere travelling along the arc
        if not above and moon_ratio is not None:
            animated_moon = moon_ratio * entry
            moon_x, moon_y = arc_point(animated_moon)
            moon_r = 5.0
            glow_c = QColor(200, 210, 230, int(15 + 15 * pulse))
            painter.setBrush(QBrush(glow_c))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(moon_x, moon_y), moon_r + 2.5, moon_r + 2.5)
            sphere_path = QPainterPath()
            sphere_path.addEllipse(QPointF(moon_x, moon_y), moon_r, moon_r)
            painter.setBrush(QBrush(QColor(215, 215, 220)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(sphere_path)

        # Time remaining text
        try:
            now = datetime.now(timezone.utc)
            if above and next_setting_str:
                secs = int((parse_iso(next_setting_str) - now).total_seconds())
                time_str = format_remaining(secs) if secs > 0 else None
            elif not above and next_rising_str:
                secs = int((parse_iso(next_rising_str) - now).total_seconds())
                time_str = format_remaining(secs) if secs > 0 else None
            else:
                time_str = None
        except Exception:
            time_str = None

        if time_str:
            if is_wide:
                context_str = t("sun.until_sunset") if above else t("sun.until_sunrise")
                ctx_color = QColor(text_color)
                ctx_color.setAlphaF(text_color.alphaF() * 0.55)

                text_x = arc_x + arc_size + 4
                right_w = w - (text_x - rect.x())

                time_font = QFont(SYSTEM_FONT, 15, QFont.Weight.Medium)
                ctx_font = QFont(SYSTEM_FONT, 8)
                time_h = QFontMetrics(time_font).height()
                ctx_h = QFontMetrics(ctx_font).height()
                gap = 3
                block_y = rect.y() + (h - time_h - gap - ctx_h) / 2

                painter.setFont(time_font)
                painter.setPen(text_color)
                painter.drawText(QRectF(text_x, block_y, right_w, time_h), Qt.AlignmentFlag.AlignCenter, time_str)

                painter.setFont(ctx_font)
                painter.setPen(ctx_color)
                painter.drawText(
                    QRectF(text_x, block_y + time_h + gap, right_w, ctx_h), Qt.AlignmentFlag.AlignCenter, context_str
                )
            else:
                # Below the arc (1x1 only — no context label, space is too tight)
                baseline_y = arc_y + arc_size / 2
                bottom_rect = QRectF(rect.x(), baseline_y + 2, w, rect.y() + h - baseline_y - 2)
                painter.setFont(QFont(SYSTEM_FONT, 11))
                painter.setPen(text_color)
                painter.drawText(bottom_rect, Qt.AlignmentFlag.AlignCenter, time_str)

        painter.end()


def _cover_crop(pixmap, width: int, height: int):
    """(scaled, x, y): pixmap scaled to cover width x height, and the offsets that centre the crop."""
    scaled = pixmap.scaled(
        width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
    )
    return scaled, (scaled.width() - width) // 2, (scaled.height() - height) // 2


# ── 3D printer tile ──


@dataclass
class _PrinterView:
    """What a 3D printer tile shows, gathered from its several entities."""

    state: str  # lower case: printing, paused, operational, ...
    text_color: QColor
    dim_text_color: QColor
    accent: QColor
    camera: object = None  # QPixmap or None
    nozzle: str = "--"
    bed: str = "--"
    progress: float = 0.0

    @property
    def title(self) -> str:
        return self.state.capitalize()

    @property
    def offline(self) -> bool:
        return self.state in ("off", "unavailable")


def _printer_accent(state: str) -> QColor:
    if state in ('printing', 'heating'):
        return QColor('#34A853')  # green
    if state == 'paused':
        return QColor('#FBBC05')  # yellow
    if 'error' in state or state == 'offline':
        return QColor('#EA4335')  # red
    return QColor('#4285F4')  # blue: idle/operational


def _printer_view(button) -> _PrinterView:
    state = button._state.lower()
    if button.theme_manager:
        text_color = QColor(button.theme_manager.get_colors().get('text', '#e0e0e0'))
        dim_text_color = text_color.darker(150)
    else:
        text_color, dim_text_color = QColor('#e0e0e0'), QColor('#aaaaaa')
    view = _PrinterView(state, text_color, dim_text_color, _printer_accent(state))

    # A tile binds one entity, so the printer's other entities come from the dashboard.
    states = getattr(button.window(), '_entity_states', None)
    if states is None:
        return view
    cached = getattr(button, '_cached_display_pixmap', None)
    if cached and not cached.isNull():
        view.camera = cached
    elif getattr(button, '_last_camera_pixmap', None):
        view.camera = button._last_camera_pixmap

    cfg = button.config
    view.progress = _printer_progress(cfg, states)
    show_targets = button.span_x > 3
    pref = getattr(button, 'temperature_unit_preference', None)
    nozzle = states.get(cfg.get('printer_nozzle_entity'), {})
    nozzle_unit = nozzle.get('attributes', {}).get('unit_of_measurement')
    view.nozzle = _temperature_pair(
        nozzle.get('state', '--'),
        states.get(cfg.get('printer_nozzle_target_entity'), {}).get('state'),
        nozzle_unit,
        pref,
        show_targets,
    )
    bed = states.get(cfg.get('printer_bed_entity'), {})
    view.bed = _temperature_pair(
        bed.get('state', '--'),
        states.get(cfg.get('printer_bed_target_entity'), {}).get('state'),
        bed.get('attributes', {}).get('unit_of_measurement') or nozzle_unit,
        pref,
        show_targets,
    )
    return view


def _printer_progress(cfg: dict, states: dict) -> float:
    """Percent done: the dedicated progress entity, else common attributes on the state entity."""
    prog_entity = cfg.get('printer_progress_entity')
    if prog_entity:
        progress = states.get(prog_entity, {}).get('state', 0)
    else:
        attrs = states.get(cfg.get('printer_state_entity'), {}).get('attributes', {})
        progress = attrs.get('progress', attrs.get('job_percentage', 0))
    try:
        return float(progress)
    except (ValueError, TypeError):
        return 0.0


def _temperature_pair(actual, target, unit, pref, show_target: bool) -> str:
    """'215°' or, when there is room and a target, '215°/220°'."""

    def fmt(value):
        return format_temperature(value, unit, pref, precision=0, fallback="--")

    if actual == '--':
        return "--"
    try:
        value = float(actual)
        if show_target and target and target != 'unknown':
            return f"{fmt(value)}/{fmt(float(target))}"
        return fmt(value)
    except Exception:
        return fmt(actual)


def _paint_printer_huge(painter, button, view: _PrinterView):
    """2x2+: the camera feed fills the card; pills float over it."""
    rect = button.rect()
    w, h = rect.width(), rect.height()
    cam_rect = QRectF(0, 0, w, h)
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect), 12, 12)
    painter.setClipPath(path)

    bg = _draw_printer_camera(painter, button, view.camera, cam_rect)
    if not view.offline:
        DashboardButtonPainter._draw_pill_label(
            painter, cam_rect, f"{view.progress:.0f}%", *bg, position='top-right', force_dark=True
        )
    if button.span_x > 2 or button.span_y > 2:
        name = button.config.get("label", button.config.get("name", "3D Printer"))
        DashboardButtonPainter._draw_pill_label(painter, cam_rect, name, *bg, position='top-left', force_dark=True)
    _draw_printer_footer(painter, view, w, h, bg)


def _draw_printer_camera(painter, button, camera, cam_rect: QRectF):
    """Draw the feed (or a placeholder); return (pixmap, x, y) for the frosted pills to blur."""
    w, h = cam_rect.width(), cam_rect.height()
    if not camera or camera.isNull():
        painter.fillRect(cam_rect.toRect(), QColor(0, 0, 0, 100))
        painter.setFont(get_mdi_font(40))
        painter.setPen(QColor(255, 255, 255, 100))
        painter.drawText(cam_rect, Qt.AlignmentFlag.AlignCenter, Icons.VIDEO_OFF)
        return None, 0, 0
    if camera == getattr(button, '_cached_display_pixmap', None):
        painter.drawPixmap(0, 0, camera)  # already rounded and sized
        bg = (camera, 0, 0)
    else:
        scaled = camera.scaled(
            int(w), int(h), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
        )
        x_off = int((scaled.width() - w) / 2)
        y_off = int((scaled.height() - h) / 2)
        painter.drawPixmap(0, 0, scaled, x_off, y_off, int(w), int(h))
        bg = (scaled, x_off, y_off)
    DashboardButtonPainter.draw_image_edge_effects(painter, cam_rect, is_top_clamped=False)
    return bg


class _TemperatureStrip:
    """Nozzle and bed icons with their values, laid out in one row."""

    def __init__(self, nozzle: str, bed: str):
        self.icon_font = get_mdi_font(13)
        self.value_font = QFont(SYSTEM_FONT, 10, QFont.Weight.DemiBold)
        self.icon_metrics = QFontMetrics(self.icon_font)
        self.value_metrics = QFontMetrics(self.value_font)
        # (text, is_icon, gap after it)
        self.parts = [
            (get_icon('printer-3d-nozzle'), True, 6),
            (nozzle, False, 12),
            (get_icon('square-medium'), True, 6),
            (bed, False, 0),
        ]
        self.width = sum(self._advance(text, is_icon) + gap for text, is_icon, gap in self.parts)

    def _advance(self, text: str, is_icon: bool) -> int:
        return (self.icon_metrics if is_icon else self.value_metrics).horizontalAdvance(text)

    def draw(self, painter, rect: QRectF, color: QColor):
        x = rect.x() + (rect.width() - self.width) / 2
        painter.setPen(color)
        for text, is_icon, gap in self.parts:
            painter.setFont(self.icon_font if is_icon else self.value_font)
            advance = self._advance(text, is_icon)
            painter.drawText(
                QRectF(x, rect.y(), advance, rect.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                text,
            )
            x += advance + gap


def _draw_printer_footer(painter, view: _PrinterView, w: int, h: int, bg):
    """Frosted pills at the bottom: state on the left, temperatures on the right.

    The temperatures are sized first and take priority; the state pill only
    appears if at least 56px remain.
    """
    from ui.utils.glass_effect import draw_frosted_pill

    pill_h = 28
    pill_y = h - pill_h - 12
    pad_x = 12
    temps = _TemperatureStrip(view.nozzle, view.bed)
    temps_pill_w = 0 if view.offline else min(temps.width + 24, w - pad_x * 2)

    state_label = view.title.upper()
    state_font = QFont(SYSTEM_FONT, 10, QFont.Weight.DemiBold)
    fm_state = QFontMetrics(state_font)
    ideal_state_w = max(56, fm_state.horizontalAdvance(state_label) + 24) if state_label else 0
    remaining_w = w - pad_x * 2 - (temps_pill_w + 8 if temps_pill_w else 0)
    state_pill_w = min(ideal_state_w, remaining_w) if remaining_w >= 56 else 0

    if state_pill_w:
        state_rect = QRectF(pad_x, pill_y, state_pill_w, pill_h)
        text_color = draw_frosted_pill(painter, state_rect, *bg, force_dark=True)
        painter.setFont(state_font)
        painter.setPen(text_color)
        painter.drawText(
            state_rect,
            Qt.AlignmentFlag.AlignCenter,
            fm_state.elidedText(state_label, Qt.TextElideMode.ElideRight, int(state_pill_w - 16)),
        )
    if temps_pill_w > 0:
        temps_rect = QRectF(w - temps_pill_w - pad_x, pill_y, temps_pill_w, pill_h)
        temps.draw(painter, temps_rect, draw_frosted_pill(painter, temps_rect, *bg, force_dark=True))


def _paint_printer_wide(painter, button, view: _PrinterView):
    """2x1: icon on the left, state and temperatures on the right, progress bar along the bottom."""
    w, h = button.rect().width(), button.rect().height()
    icon_size, icon_x = 40, 24
    painter.setFont(get_mdi_font(28))
    painter.setPen(view.accent)
    painter.drawText(
        QRectF(icon_x, (h - icon_size) // 2, icon_size, icon_size), Qt.AlignmentFlag.AlignCenter, Icons.PRINTER_3D
    )

    text_x = icon_x + icon_size + 16
    avail_width = w - text_x - 16
    state_font = QFont(SYSTEM_FONT, 12, QFont.Weight.Bold)
    painter.setFont(state_font)
    painter.setPen(view.text_color)
    painter.drawText(
        QRectF(text_x, 16, avail_width, 24),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
        QFontMetrics(state_font).elidedText(view.title, Qt.TextElideMode.ElideRight, int(avail_width)),
    )
    painter.setFont(QFont(SYSTEM_FONT, 10, QFont.Weight.Medium))
    painter.setPen(view.dim_text_color)
    painter.drawText(
        QRectF(text_x, 44, w - text_x - 16, 24),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        f"N: {view.nozzle}  B: {view.bed}",
    )
    DashboardButtonPainter.draw_bottom_bar(painter, QRectF(0, 0, w, h), view.progress, 100.0, view.accent)


def _paint_printer_tall(painter, button, view: _PrinterView):
    """1x2: the icon where a 1x1 has it, then state, percent and a progress bar."""
    w, h = button.rect().width(), button.rect().height()
    painter.setFont(get_mdi_font(40))
    painter.setPen(view.accent)
    painter.drawText(QRectF(0, 0, w, 80), Qt.AlignmentFlag.AlignCenter, Icons.PRINTER_3D)

    state_font = QFont(SYSTEM_FONT, 12, QFont.Weight.Bold)
    painter.setFont(state_font)
    painter.setPen(view.text_color)
    painter.drawText(
        QRectF(0, 90, w, 24),
        Qt.AlignmentFlag.AlignCenter,
        QFontMetrics(state_font).elidedText(view.title, Qt.TextElideMode.ElideRight, int(w - 16)),
    )
    if not view.offline:
        painter.setFont(QFont(SYSTEM_FONT, 11, QFont.Weight.Medium))
        painter.setPen(view.dim_text_color)
        painter.drawText(QRectF(0, 116, w, 20), Qt.AlignmentFlag.AlignCenter, f"{view.progress:.0f}%")
    DashboardButtonPainter.draw_bottom_bar(painter, QRectF(0, 0, w, h), view.progress, 100.0, view.accent)


def _paint_printer_compact(painter, button, view: _PrinterView):
    """1x1: the icon in the state's colour, with the percent below while printing."""
    rect = button.rect()
    w, h = rect.width(), rect.height()
    painter.setFont(get_mdi_font(40))
    painter.setPen(view.accent)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, Icons.PRINTER_3D)
    if 0 < view.progress < 100 and not view.offline:
        painter.setFont(QFont(SYSTEM_FONT, 10, QFont.Weight.Bold))
        painter.setPen(view.text_color)
        painter.drawText(QRectF(0, h / 2 + 20, w, 20), Qt.AlignmentFlag.AlignCenter, f"{view.progress:.0f}%")
