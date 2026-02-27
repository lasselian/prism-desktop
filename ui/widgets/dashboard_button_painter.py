from PyQt6.QtCore import Qt, QRect, QRectF, QPointF, QPropertyAnimation
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPainterPath, QColor, QPen, QBrush, 
    QLinearGradient, QConicalGradient, QRadialGradient
)
from PyQt6.QtWidgets import QApplication
from ui.icons import get_icon, get_mdi_font, Icons
from core.utils import SYSTEM_FONT
from ui.visuals.background_generator import BackgroundGenerator

class DashboardButtonPainter:
    """Handles custom painting for DashboardButton."""
    
    @staticmethod
    def paint(button, event):
        """Main paint method."""
        # Media Player (Apple-like)
        if button.config.get('type') == 'media_player':
            DashboardButtonPainter._paint_media_player(button)
            
        # 3D Printer
        if button.config.get('type') == '3d_printer':
            DashboardButtonPainter._paint_3d_printer(button)
        
        # Draw Camera Image (if applicable)
        # Draw Camera Image (if applicable)
        if button.config and button.config.get('type') == 'camera':
             # Use cached rounded pixmap if available (Performance + Style)
             if hasattr(button, '_cached_display_pixmap') and button._cached_display_pixmap and not button._cached_display_pixmap.isNull():
                 painter = QPainter(button)
                 painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                 painter.drawPixmap(0, 0, button._cached_display_pixmap)
                 DashboardButtonPainter.draw_image_edge_effects(painter, QRectF(button.rect()), is_top_clamped=False)
                 painter.end()
             
             # Fallback to direct scaling if cache missing (e.g. first frame or resize race condition)
             elif hasattr(button, '_last_camera_pixmap') and button._last_camera_pixmap:
                 painter = QPainter(button)
                 painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                 
                 # Clip to rounded rect
                 path = QPainterPath()
                 path.addRoundedRect(QRectF(button.rect()), 12, 12)
                 painter.setClipPath(path)
                 
                 # Scale to fill button while maintaining aspect ratio (crop if needed)
                 scaled = button._last_camera_pixmap.scaled(
                     button.width(), button.height(),
                     Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                     Qt.TransformationMode.SmoothTransformation
                 )
                 
                 # Center crop
                 x = (scaled.width() - button.width()) // 2
                 y = (scaled.height() - button.height()) // 2
                 
                 # Draw cropped
                 painter.drawPixmap(0, 0, scaled, x, y, button.width(), button.height())
                 DashboardButtonPainter.draw_image_edge_effects(painter, QRectF(button.rect()), is_top_clamped=False)
                 painter.end()
                 
        # === Camera Pill Label ===
        if button.config and button.config.get('type') == 'camera':
             label = button.config.get('label')
             if label:
                 # Re-open painter for overlay
                 painter = QPainter(button)
                 painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                 
                 # Prepare background for blur
                 bg_pix = None
                 x_off = 0
                 y_off = 0
                 
                 if hasattr(button, '_cached_display_pixmap') and button._cached_display_pixmap and not button._cached_display_pixmap.isNull():
                     bg_pix = button._cached_display_pixmap
                 elif hasattr(button, '_last_camera_pixmap') and button._last_camera_pixmap:
                     # For fallback, we need to replicate the scale/crop to get correct blur source
                     # This is expensive but fallback is rare
                     scaled = button._last_camera_pixmap.scaled(
                         button.width(), button.height(),
                         Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                         Qt.TransformationMode.SmoothTransformation
                     )
                     bg_pix = scaled
                     x_off = (scaled.width() - button.width()) // 2
                     y_off = (scaled.height() - button.height()) // 2
                 
                 DashboardButtonPainter._draw_pill_label(painter, button.rect(), label, bg_pix, x_off, y_off)
                 painter.end()
        
        # Pulse Animation (Script)
        if button._pulse_opacity > 0.01:
            DashboardButtonPainter._paint_pulse(button)
        
        # Only draw special border if animating or if progress > 0
        if button.anim.state() == QPropertyAnimation.State.Running or button._anim_progress > 0.0:
            DashboardButtonPainter._paint_border_animation(button)

        if not button.config:
            DashboardButtonPainter._paint_empty_slot(button)

        # Draw Resize Handle (Glass-like)
        if button._resize_handle_opacity > 0.01:
            DashboardButtonPainter._paint_resize_handle(button)

        # UNIVERSAL EDGE BEVEL HIGHLIGHT
        # Gives ALL configured buttons a subtle physical lip, making them feel like 3D glass/plastic tiles
        button_style = getattr(button, 'button_style', 'Gradient')
        if button.config and button.config.get('type') != 'forbidden' and button_style == 'Gradient':
            painter = QPainter(button)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            DashboardButtonPainter.draw_button_bevel_edge(painter, QRectF(button.rect()), intensity_modifier=0.25)
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
                rect.width(), rect.height(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            # Center crop
            x_off = (scaled.width() - rect.width()) // 2
            y_off = (scaled.height() - rect.height()) // 2
            painter.drawPixmap(0, 0, scaled, x_off, y_off, rect.width(), rect.height())
            
            # Gradient overlay: user color at bottom -> transparent at top
            # FIX: Use explicit TopLeft/BottomLeft for full coverage
            grad = QLinearGradient(QPointF(rect.topLeft()), QPointF(rect.bottomLeft()))
            grad.setColorAt(0.0, QColor(0, 0, 0, 0))           # Top: transparent
            overlay_color = QColor(btn_color)
            overlay_color.setAlpha(255)
            grad.setColorAt(1.0, overlay_color)                  # Bottom: user color
            painter.fillRect(rect, grad)
        else:
             # No Art -> Procedural Background
             
             # Generate deterministic seed based on media info or button slot
             media_state = button._media_state or {}
             attrs = media_state.get('attributes', {})
             
             title = attrs.get('media_title', '')
             artist = attrs.get('media_artist', '')
             
             if title or artist:
                 # Consistent background for the same track
                 seed = hash(f"{title}{artist}")
             else:
                 # Consistent background for the slot (idle state)
                 seed = button.slot
                 
             # Generate background
             # We use the button rect size
             bg_pixmap = BackgroundGenerator.generate(rect.width(), rect.height(), seed=seed)
             
             # Draw Background
             painter.drawPixmap(0, 0, bg_pixmap)
             
             # Gradient Overlay (for text readability)
             grad = QLinearGradient(QPointF(rect.topLeft()), QPointF(rect.bottomLeft()))
             grad.setColorAt(0.0, QColor(0, 0, 0, 0))              # Top: transparent
             
             overlay_color = QColor(btn_color)
             # Slightly stronger overlay for generated backgrounds to unify theme
             overlay_color.setAlpha(200 if is_playing else 120)    
             grad.setColorAt(1.0, overlay_color)                   # Bottom: user/accent color
             
             painter.fillRect(rect, grad)
            
        # === PILL LABEL (Apple-like) ===
        # Always show Pill if Huge 2x2+
        label = button.config.get('label')
        if is_huge and label:
            bg_pix = None
            x_off = 0
            y_off = 0
            if has_art and show_art:
                 bg_pix = scaled
                 # x_off and y_off are already calculated above
            
            DashboardButtonPainter._draw_pill_label(painter, rect, label, bg_pix, x_off, y_off)

        
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
                    # Blurred art background (same technique as label pill)
                    pill_src_x = x_off + info_pill_x
                    pill_src_y = y_off + info_pill_y
                    valid_src = QRect(int(pill_src_x), int(pill_src_y), int(info_pill_w), int(info_pill_h))
                    info_pix = scaled.copy(valid_src)
                    
                    if not info_pix.isNull():
                        small = info_pix.scaled(
                            max(1, int(info_pill_w * 0.1)), max(1, int(info_pill_h * 0.1)),
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation
                        )
                        blurred = small.scaled(
                            int(info_pill_w), int(info_pill_h),
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation
                        )
                        
                        # Luminance check
                        small_img = small.toImage()
                        r_sum = g_sum = b_sum = count = 0
                        for sy in range(small_img.height()):
                            for sx in range(small_img.width()):
                                pc = small_img.pixelColor(sx, sy)
                                r_sum += pc.red()
                                g_sum += pc.green()
                                b_sum += pc.blue()
                                count += 1
                        
                        avg_lum = 0
                        if count > 0:
                            avg_lum = 0.2126 * (r_sum/count) + 0.7152 * (g_sum/count) + 0.0722 * (b_sum/count)
                        
                        is_light_bg = avg_lum > 128
                        if is_light_bg:
                            info_text_color = QColor(0, 0, 0, 220)
                            info_border_color = QColor(0, 0, 0, 30)
                            info_tint = QColor(255, 255, 255, 60)
                        else:
                            info_text_color = QColor(255, 255, 255, 240)
                            info_border_color = QColor(255, 255, 255, 50)
                            info_tint = QColor(255, 255, 255, 30)
                        
                        painter.save()
                        painter.setClipPath(info_pill_path)
                        painter.drawPixmap(int(info_pill_x), int(info_pill_y), blurred)
                        painter.fillPath(info_pill_path, info_tint)
                        
                        pen = QPen(info_border_color)
                        pen.setWidth(1)
                        painter.setPen(pen)
                        painter.drawRoundedRect(info_pill_rect, 14, 14)
                        painter.restore()
                    else:
                        info_text_color = QColor(255, 255, 255, 240)
                        painter.save()
                        painter.fillPath(info_pill_path, QColor(0, 0, 0, 80))
                        painter.restore()
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
    def _paint_3d_printer(button):
        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = button.rect()
        is_huge = button.span_x >= 2 and button.span_y >= 2  # 2x2+
        is_wide = button.span_x >= 2
        is_tall = button.span_y >= 2 and button.span_x < 2  # 1x2+
        
        # State Data
        state = button._state.lower()  # printing, paused, operational, etc.
        printer_state = state.capitalize()
        
        # Theme / Colors
        if button.theme_manager:
            colors = button.theme_manager.get_colors()
            base_color = QColor(colors.get('base', '#2d2d2d'))
            text_color = QColor(colors.get('text', '#e0e0e0'))
            dim_text_color = text_color.darker(150)
        else:
            base_color = QColor('#2d2d2d')
            text_color = QColor('#e0e0e0')
            dim_text_color = QColor('#aaaaaa')
            
        # Accent color based on state
        if state in ['printing', 'heating']:
            accent_color = QColor('#34A853') # Green
        elif state == 'paused':
            accent_color = QColor('#FBBC05') # Yellow
        elif 'error' in state or state == 'offline':
            accent_color = QColor('#EA4335') # Red
        else:
            accent_color = QColor('#4285F4') # Blue (Idle/Operational)
            
        # Fetch entities manually since standard button abstraction binds to 1 entity
        dashboard = button.window()  # Walk up to dashboard
        camera_pixmap = None
        nozzle_str = "--°"
        bed_str = "--°"
        progress = 0
        
        cfg = button.config
        
        if hasattr(dashboard, '_entity_states'):
            # Camera
            cam_ent = cfg.get('printer_camera_entity')
            # Camera image is handled by the standard `_last_camera_pixmap` on button if configured via `set_camera_image`
            # Wait, dashboard updates the button's `_last_camera_pixmap` if it matches.
            # We already routed `apply_camera_cache` in dashboard to hit `3d_printer`. 
            # So the button already holds `_last_camera_pixmap`.
            if hasattr(button, '_cached_display_pixmap') and button._cached_display_pixmap and not button._cached_display_pixmap.isNull():
                 camera_pixmap = button._cached_display_pixmap
            elif hasattr(button, '_last_camera_pixmap') and button._last_camera_pixmap:
                 camera_pixmap = button._last_camera_pixmap
            
            # Progress (read from state attributes or secondary entity if configured - sticking to state attributes for simplicity if it's there)
            # Many HA integrations put progress in attributes of the state entity or a dedicated entity.
            # We will try both: primary state attributes or if user configures separate ones later
            state_data = dashboard._entity_states.get(cfg.get('printer_state_entity'), {})
            attrs = state_data.get('attributes', {})
            # Look for common progress attributes
            progress = attrs.get('progress', attrs.get('job_percentage', 0))
            if progress is None: progress = 0
            
            # Nozzle
            nozzle_data = dashboard._entity_states.get(cfg.get('printer_nozzle_entity'), {})
            nozzle_val = nozzle_data.get('state', '--')
            nozzle_target_data = dashboard._entity_states.get(cfg.get('printer_nozzle_target_entity'), {})
            nozzle_target_val = nozzle_target_data.get('state', None)
            
            if nozzle_val != '--':
                try: 
                    n_act = float(nozzle_val)
                    if button.span_x > 3 and nozzle_target_val and nozzle_target_val != 'unknown':
                        n_tgt = float(nozzle_target_val)
                        nozzle_str = f"{n_act:.0f}°/{n_tgt:.0f}°"
                    else:
                        nozzle_str = f"{n_act:.0f}°"
                except: nozzle_str = f"{nozzle_val}°"
                
            # Bed
            bed_data = dashboard._entity_states.get(cfg.get('printer_bed_entity'), {})
            bed_val = bed_data.get('state', '--')
            bed_target_data = dashboard._entity_states.get(cfg.get('printer_bed_target_entity'), {})
            bed_target_val = bed_target_data.get('state', None)
            
            if bed_val != '--':
                try: 
                    b_act = float(bed_val)
                    if button.span_x > 3 and bed_target_val and bed_target_val != 'unknown':
                        b_tgt = float(bed_target_val)
                        bed_str = f"{b_act:.0f}°/{b_tgt:.0f}°"
                    else:
                        bed_str = f"{b_act:.0f}°"
                except: bed_str = f"{bed_val}°"
            
        icon = Icons.PRINTER_3D # standard mdi-printer-3d
        
        # DRAW LOGIC
        if is_huge:
            # === 2x2+: Camera Top, Stats Strip Bottom ===
            w = rect.width()
            h = rect.height()
            
            # 1. Camera Feed (Top Area)
            strip_h = 44 # Reduced height to show more camera feed
            cam_h = h - strip_h
            cam_rect = QRectF(0, 0, w, cam_h)
            
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect), 12, 12)
            painter.setClipPath(path)
            
            if camera_pixmap and not camera_pixmap.isNull():
                 # Fast direct draw if using cached rounded pixmap
                 if camera_pixmap == getattr(button, '_cached_display_pixmap', None):
                      painter.drawPixmap(0, 0, camera_pixmap)
                      bg_pix = camera_pixmap
                      x_bg = 0
                      y_bg = 0
                 else:
                      # Aspect fill the top section
                      scaled = camera_pixmap.scaled(
                          int(w), int(cam_h),
                          Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                          Qt.TransformationMode.SmoothTransformation
                      )
                      x_off = (scaled.width() - w) / 2
                      y_off = (scaled.height() - cam_h) / 2
                      painter.drawPixmap(0, 0, scaled, int(x_off), int(y_off), int(w), int(cam_h))
                      bg_pix = scaled
                      x_bg = int(x_off)
                      y_bg = int(y_off)
                      
                 # Add glass edge effects (shadow + highlight) to the camera feed
                 DashboardButtonPainter.draw_image_edge_effects(painter, cam_rect, is_top_clamped=True)
                 
            else:
                 bg_pix = None
                 x_bg = 0
                 y_bg = 0
                 # Fallback empty camera zone
                 painter.fillRect(cam_rect.toRect(), QColor(0, 0, 0, 100))
                 painter.setFont(get_mdi_font(40))
                 painter.setPen(QColor(255, 255, 255, 100))
                 painter.drawText(cam_rect, Qt.AlignmentFlag.AlignCenter, Icons.VIDEO_OFF)
            
            # Draw Pill Overlay for Progress %
            try:
                prog_val = float(progress)
            except (ValueError, TypeError):
                prog_val = 0.0
                
            if printer_state.lower() not in ["off", "unavailable"]:
                prog_str = f"{prog_val:.0f}%"
                DashboardButtonPainter._draw_pill_label(
                    painter, cam_rect, prog_str, bg_pix, x_bg, y_bg, position='top-right'
                )
            
            # Draw Pill Overlay for Name (if button is bigger than 2x2)
            # A 2x2 button has spans (2, 2). If either span is > 2, or both are > 2, etc.
            # The prompt says "bigger than 2x2", let's assume ifspan_x > 2 or span_y > 2
            if button.span_x > 2 or button.span_y > 2:
                btn_name = button.config.get("label", button.config.get("name", "3D Printer"))
                DashboardButtonPainter._draw_pill_label(
                    painter, cam_rect, btn_name, bg_pix, x_bg, y_bg, position='top-left'
                )
            
            # 2. Stats Strip (Bottom Area)
            # We want the strip to look like frosted glass overlaying the bottom part of the button
            # Or just a solid color block to keep it completely clear. Let's do a semi-transparent block matching the theme base
            strip_bg = QColor(base_color)
            strip_bg.setAlpha(240) # Mostly solid for readability
            
            strip_rect = QRectF(0, cam_h, w, strip_h)
            painter.fillRect(strip_rect, strip_bg)
            
            # Divider line
            painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
            painter.drawLine(0, int(cam_h), int(w), int(cam_h))
            
            # Strip Content Grid
            # [State]       [Nozzle] [Bed]
            painter.setPen(text_color)
            
            # Left: Status
            status_text_x = 16
            painter.setFont(QFont(SYSTEM_FONT, 11, QFont.Weight.DemiBold))
            painter.setPen(text_color)
            painter.drawText(QRectF(status_text_x, cam_h, w/2, strip_h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, printer_state.capitalize())
            
            # Right: Stats
            # Determine if we have extra space (width > 2 tiles)
            use_long_names = button.span_x > 2
            
            stats_w = int(w * 0.6) # Give a bit more room to stats on all sizes
            stats_x = int(w - stats_w - 16)
            stats_rect = QRect(stats_x, int(cam_h), stats_w, strip_h)
            
            part_w = stats_w / 2.0
            
            lbl_noz = "NOZZLE" if use_long_names else "N"
            lbl_bed = "BED" if use_long_names else "B"
            
            # calculate text width of labels to dynamic offset the values
            font_lbl = QFont(SYSTEM_FONT, 9 if use_long_names else 10, QFont.Weight.Bold)
            font_val = QFont(SYSTEM_FONT, 10, QFont.Weight.Medium)
            
            fm_lbl = QFontMetrics(font_lbl)
            off_noz = fm_lbl.horizontalAdvance(f"{lbl_noz}  ")
            off_bed = fm_lbl.horizontalAdvance(f"{lbl_bed}  ")
            
            # Nozzle
            r_noz = QRectF(stats_x, cam_h, part_w, strip_h)
            painter.setFont(font_lbl)
            painter.setPen(dim_text_color)
            painter.drawText(r_noz, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, f"{lbl_noz}")
            r_noz.adjust(off_noz, 0, 0, 0) # adjust text left exactly by word size
            painter.setPen(text_color)
            painter.setFont(font_val)
            painter.drawText(r_noz, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, nozzle_str)
            
            # Bed
            r_bed = QRectF(stats_x + part_w, cam_h, part_w, strip_h)
            painter.setFont(font_lbl)
            painter.setPen(dim_text_color)
            painter.drawText(r_bed, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, f"{lbl_bed}")
            r_bed.adjust(off_bed, 0, 0, 0)
            painter.setPen(text_color)
            painter.setFont(font_val)
            painter.drawText(r_bed, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, bed_str)
            
        elif is_wide:
            # === 2x1: Wide layout ===
            # Icon/Ring Left, Stats Right
            w = rect.width()
            h = rect.height()
            
            # Left Icon (with circular progress if printing)
            icon_size = 40
            icon_x = 24
            icon_y = (h - icon_size) // 2
            
            painter.setFont(get_mdi_font(28))
            painter.setPen(accent_color)
            painter.drawText(QRectF(icon_x, icon_y, icon_size, icon_size), Qt.AlignmentFlag.AlignCenter, icon)
            
            # Text area on the right
            text_x = icon_x + icon_size + 16
            
            # State Title
            painter.setFont(QFont(SYSTEM_FONT, 12, QFont.Weight.Bold))
            painter.setPen(text_color)
            painter.drawText(QRectF(text_x, 16, w - text_x - 16, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, printer_state)
            
            # Sub stats
            painter.setFont(QFont(SYSTEM_FONT, 10, QFont.Weight.Medium))
            painter.setPen(dim_text_color)
            stats_str = f"N: {nozzle_str}  B: {bed_str}"
            painter.drawText(QRectF(text_x, 44, w - text_x - 16, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, stats_str)
            
            # Progress Bar Bottom
            bar_h = 4
            bar_y = h - bar_h
            if progress > 0:
                fill_w = w * (float(progress) / 100.0)
                painter.fillRect(QRectF(0, bar_y, fill_w, bar_h), accent_color)
            
        elif button.span_y >= 2 and button.span_x == 1:
            # === 1x2: Tall layout ===
            w = rect.width()
            h = rect.height()
            
            # Icon in the identical spot as a 1x1 (top half)
            top_half = QRectF(0, 0, w, 80)
            painter.setFont(get_mdi_font(40))
            painter.setPen(accent_color)
            painter.drawText(top_half, Qt.AlignmentFlag.AlignCenter, icon)
            
            # State Title
            painter.setFont(QFont(SYSTEM_FONT, 12, QFont.Weight.Bold))
            painter.setPen(text_color)
            painter.drawText(QRectF(0, 90, w, 24), Qt.AlignmentFlag.AlignCenter, printer_state.capitalize())
            
            # Progress %
            try: prog_val = float(progress)
            except: prog_val = 0.0
                
            if printer_state.lower() not in ["off", "unavailable"]:
                 painter.setFont(QFont(SYSTEM_FONT, 11, QFont.Weight.Medium))
                 painter.setPen(dim_text_color)
                 painter.drawText(QRectF(0, 116, w, 20), Qt.AlignmentFlag.AlignCenter, f"{prog_val:.0f}%")
                 
            # Progress Bar Bottom
            bar_h = 4
            bar_y = h - bar_h
            if prog_val > 0:
                fill_w = w * (prog_val / 100.0)
                painter.fillRect(QRectF(0, bar_y, fill_w, bar_h), accent_color)
            
        else:
            # === 1x1: Compact ===
            w = rect.width()
            h = rect.height()
            # Just the icon centered, color coded
            painter.setFont(get_mdi_font(40))
            painter.setPen(accent_color)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, icon)
            
            try: prog_val = float(progress)
            except: prog_val = 0.0
            
            if prog_val > 0 and prog_val < 100 and printer_state.lower() not in ["off", "unavailable"]:
                 # Small progress indicator text below icon
                 painter.setFont(QFont(SYSTEM_FONT, 10, QFont.Weight.Bold))
                 painter.setPen(text_color)
                 painter.drawText(QRectF(0, h/2 + 20, w, 20), Qt.AlignmentFlag.AlignCenter, f"{prog_val:.0f}%")

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
    def _paint_border_animation(button):
        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Interactive 'Press' feedback
        speed = 0.9 if button._border_effect == 'Prism Shard' else 1.5
        if button._border_effect == 'Liquid Mercury': speed = 1.2
        angle = button._anim_progress * 360.0 * speed
        
        opacity = 1.0
        if button._anim_progress > 0.8:
            opacity = (1.0 - button._anim_progress) / 0.2
        
        # Make sure opacity doesn't go below 0
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
        is_light = (button.theme_manager and button.theme_manager.get_effective_theme() == 'light')
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
        
        # Opacity control
        painter.setOpacity(button._resize_handle_opacity)
        
        # Bottom-right corner
        r = button.rect()
        radius = 12 # Match button border radius
        handle_size = 28 # Bigger handle
        
        path = QPainterPath()
        
        # 1. Start on bottom edge, left of corner
        path.moveTo(r.right() - handle_size, r.bottom())
        
        # 2. Outer Edge: Follow the button's rounded corner exactly
        corner_rect = QRectF(r.right() - 2*radius, r.bottom() - 2*radius, 2*radius, 2*radius)
        
        path.lineTo(r.right() - radius, r.bottom()) # Line to start of arc
        path.arcTo(corner_rect, 270, 90) # The corner itself
        path.lineTo(r.right(), r.bottom() - handle_size) # Line to handle top
        
        # 3. Inner Edge: Curve inward back to start
        path.quadTo(r.right() - 4, r.bottom() - 4, r.right() - handle_size, r.bottom())
        
        path.closeSubpath()
        
        # Glass Style
        painter.setPen(Qt.PenStyle.NoPen)
        
        # Gradient for glass/shiny look
        grad = QLinearGradient(QPointF(r.right() - handle_size, r.bottom() - handle_size), QPointF(r.bottomRight()))
        grad.setColorAt(0.0, QColor(255, 255, 255, 120)) # Start brighter 
        grad.setColorAt(1.0, QColor(255, 255, 255, 10))  # Fade out
        
        painter.setBrush(QBrush(grad))
        painter.drawPath(path)
        
        # Accent line (Inner Edge only) for sharpness
        pen = QPen(QColor(255, 255, 255, 70))
        pen.setWidthF(2.0)
        painter.strokePath(path, pen)
        painter.end()

    @staticmethod
    def _draw_pill_label(painter, rect, label, background_pixmap=None, x_off=0, y_off=0, position='top-center'):
        """Draws a glass/blurred pill label."""
        if not label:
            return

        # Calculate Pill Geometry
        if position == 'top-right':
            font = QFont(SYSTEM_FONT, 10, QFont.Weight.DemiBold)
            old_font = painter.font()
            painter.setFont(font)
            fm = painter.fontMetrics()
            
            text_w = fm.horizontalAdvance(label.upper())
            painter.setFont(old_font) # restore
            
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
            painter.setFont(old_font) # restore
            
            pill_w = max(48, text_w + 24)
            pill_h = 28
            pill_x = 12
            pill_y = 12
        else: # top-center (Upper Third)
            font = QFont(SYSTEM_FONT, 10, QFont.Weight.DemiBold)
            old_font = painter.font()
            painter.setFont(font)
            fm = painter.fontMetrics()
            
            text_w = fm.horizontalAdvance(label.upper())
            painter.setFont(old_font) # restore
            
            pill_w = min(rect.width() * 0.9, max(48, text_w + 24)) # Max width constraint
            pill_h = 28
            pill_x = (rect.width() - pill_w) / 2
            pill_y = rect.height() * 0.15 # 15% from top
            
        pill_rect = QRectF(pill_x, pill_y, pill_w, pill_h)
        
        # --- Pill Rendering ---
        # Check 1: Do we have a background pixmap to blur?
        if background_pixmap and not background_pixmap.isNull():
            # 1. Access Background (Crop from Scaled Art)
            # Need to map pill rect to scaled image coordinates
            # offset in scaled image = x_off, y_off
            pill_src_x = x_off + pill_x
            pill_src_y = y_off + pill_y
            
            # 2. Create Blurred Pill Background
            # Crop
            # Ensure we don't go out of bounds
            valid_src = QRect(int(pill_src_x), int(pill_src_y), int(pill_w), int(pill_h))
            pill_pix = background_pixmap.copy(valid_src)
            
            if not pill_pix.isNull():
                # Blur Simulation: Downscale -> Upscale
                # Downscale factor 0.1 (very small) -> heavy blur
                small = pill_pix.scaled(
                    int(pill_w * 0.1), int(pill_h * 0.1),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                blurred = small.scaled(
                    int(pill_w), int(pill_h), 
                    Qt.AspectRatioMode.IgnoreAspectRatio, 
                    Qt.TransformationMode.SmoothTransformation
                )
                
                # Calculate Luminance from the small downscaled image (fast)
                # Convert to QImage to access pixels
                small_img = small.toImage()
                
                # Simple average luminance
                # We only check a few pixels to be fast (center, corners) or full average?
                # Full average on 16x3 image is negligible cost.
                r_sum = 0
                g_sum = 0
                b_sum = 0
                count = 0
                
                for y in range(small_img.height()):
                    for x in range(small_img.width()):
                        c = small_img.pixelColor(x, y)
                        r_sum += c.red()
                        g_sum += c.green()
                        b_sum += c.blue()
                        count += 1
                        
                avg_lum = 0
                if count > 0:
                    # Standard Luminance formula
                    avg_lum = (0.2126 * (r_sum/count) + 0.7152 * (g_sum/count) + 0.0722 * (b_sum/count))
                
                # Determine Theme (Dark vs Light Text)
                is_light_bg = avg_lum > 128
                
                if is_light_bg:
                    # Light Background -> Dark Text
                    text_color = QColor(0, 0, 0, 220)
                    border_color = QColor(0, 0, 0, 30)
                    tint_color = QColor(255, 255, 255, 60) # Boost brightness slightly
                else:
                    # Dark Background -> White Text
                    text_color = QColor(255, 255, 255, 240)
                    border_color = QColor(255, 255, 255, 50)
                    tint_color = QColor(255, 255, 255, 30)
                    
                # Draw Blurred Background with Tint
                painter.save()
                pill_path = QPainterPath()
                pill_path.addRoundedRect(pill_rect, 14, 14) # Fully rounded pill
                painter.setClipPath(pill_path)
                painter.drawPixmap(int(pill_x), int(pill_y), blurred)
                painter.fillPath(pill_path, tint_color)
                
                # Border
                pen = QPen(border_color)
                pen.setWidth(1)
                painter.setPen(pen)
                painter.drawRoundedRect(pill_rect, 14, 14)
                painter.restore()
                
                # Text Color
                painter.setPen(text_color)
        
        else:
            # Fallback Pill (No Artwork -> Idle/Off)
            # Use semi-transparent background
            painter.save()
            pill_path = QPainterPath()
            pill_path.addRoundedRect(pill_rect, 14, 14)
            
            # Dark semi-transparent pill on solid bg
            painter.fillPath(pill_path, QColor(0, 0, 0, 60))
            
            # Border
            pen = QPen(QColor(255, 255, 255, 30))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawRoundedRect(pill_rect, 14, 14)
            painter.restore()
            
            painter.setPen(QColor(255, 255, 255, 220))

        # Draw Label Text (Final Step)
        # System Font, Bold
        font = QFont(SYSTEM_FONT, 10, QFont.Weight.DemiBold)
        painter.setFont(font)
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
        
        # Ensure smooth loop if not already handled
        if colors[0] != colors[-1]:
             # If colors don't wrap, we might need logic, but for Rainbow/Aurora they usually loop naturally 
             # or are defined to loop. If we pass custom 2 colors, we expect the caller to make them loop (C1, C2, C1).
             pass

        for i, color in enumerate(colors):
            gradient.setColorAt(i / (len(colors) - 1), QColor(color))
        
        pen = QPen()
        pen.setWidth(2)
        pen.setBrush(QBrush(gradient))
        
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 9, 9)

    @staticmethod
    def draw_image_edge_effects(painter, rect, is_top_clamped=False):
        """Draws a soft inner vignette shadow and a bright top-left specular highlight to make camera feeds look like physical glass rather than hard stickers."""
        painter.save()
        
        # 1. Inner Vignette (Soft dark border)
        # Using a radial gradient acts as a great recessed shadow
        center = rect.center()
        radius = max(rect.width(), rect.height()) / 1.5
        
        grad = QRadialGradient(center, radius)
        grad.setColorAt(0.7, QColor(0, 0, 0, 0))    # Transparent core
        grad.setColorAt(1.0, QColor(0, 0, 0, 100))  # Darkened exterior edges
        
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        painter.fillRect(rect, grad)
        
        # Restore composition mode for the highlight
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        
        # 2. Specular Glass Highlight & Perimeter
        DashboardButtonPainter.draw_button_bevel_edge(painter, rect, intensity_modifier=2.0, is_top_clamped=is_top_clamped)
            
        painter.restore()

    @staticmethod
    def draw_button_bevel_edge(painter, rect, intensity_modifier=1.0, is_top_clamped=False):
        """Draws a bright top-left specular highlight to simulate physical glass/plastic thickness."""
        painter.save()
        
        line_rect = QRectF(rect)
            
        highlight_grad = QLinearGradient(line_rect.topLeft(), line_rect.topRight())
        
        # Base opacities
        alpha_start = min(255, int(45 * intensity_modifier))
        alpha_mid = min(255, int(15 * intensity_modifier))
        
        highlight_grad.setColorAt(0.0, QColor(255, 255, 255, alpha_start)) # Bright left edge
        highlight_grad.setColorAt(0.4, QColor(255, 255, 255, alpha_mid))   # Fading across top
        highlight_grad.setColorAt(1.0, QColor(255, 255, 255, 0))           # Transparent right
        
        def make_path(adj_rect, r):
            p = QPainterPath()
            if is_top_clamped:
                p.moveTo(adj_rect.bottomLeft())
                p.lineTo(adj_rect.left(), adj_rect.top() + r)
                p.arcTo(adj_rect.left(), adj_rect.top(), r*2, r*2, 180, -90)
                p.lineTo(adj_rect.right() - r, adj_rect.top())
                p.arcTo(adj_rect.right() - r*2, adj_rect.top(), r*2, r*2, 90, -90)
                p.lineTo(adj_rect.bottomRight())
                p.closeSubpath()
            else:
                p.addRoundedRect(adj_rect, r, r)
            return p
        
        # Stroke an inner border path
        pen = QPen(QBrush(highlight_grad), 2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(make_path(line_rect.adjusted(1, 1, -1, -1), 11))
            
        # Perimeter Outline (Separates dark surfaces from dark app backgrounds)
        perimeter_alpha = min(255, int(15 * intensity_modifier))
        perimeter_pen = QPen(QColor(255, 255, 255, perimeter_alpha)) # Very subtle white frame
        perimeter_pen.setWidthF(1.0)
        painter.setPen(perimeter_pen)
        painter.drawPath(make_path(line_rect.adjusted(0.5, 0.5, -0.5, -0.5), 11.5))
            
        painter.restore()


