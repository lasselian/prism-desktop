"""
Assist chat overlay: full-window text/voice chat that talks to Home
Assistant's Assist (conversation + pipeline) APIs. Unlike the other overlays
it isn't tied to a specific grid button/entity — it's opened from the footer
button or a global hotkey, so callers just hand it a start/target rect to
morph between.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
    QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt, QRect, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QPainter

from ui.icons import get_icon, get_mdi_font
from core.localization_manager import t
from ui.widgets.dashboard_button_painter import DashboardButtonPainter
from ui.widgets.overlays import BaseOverlay

_BUBBLE_MAX_WIDTH = 460

_BUBBLE_STYLES = {
    'user':      ("rgba(124,77,255,0.85)", "white"),
    'assistant': ("rgba(255,255,255,0.08)", "white"),
    'error':     ("rgba(234,67,53,0.22)", "#ffb4ab"),
}

class AssistOverlay(BaseOverlay):
    """Full-window chat popup with a text field and push-to-talk mic button."""

    text_submitted = pyqtSignal(str)
    voice_start_requested = pyqtSignal()
    voice_stop_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = "Assist"
        self._color = QColor("#4285F4")
        self._base_color = QColor("#2d2d2d")
        self._listening = False
        self._instant_close = False
        self._build_content()

    def _build_content(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._content = QWidget(self)
        outer.addWidget(self._content)

        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 14, 14, 8)

        title = QLabel(t("assist.chat.title"))
        title.setStyleSheet(
            "color: rgba(255,255,255,0.85); font-size: 12px; font-weight: 600; "
            "letter-spacing: 1px; background: transparent;"
        )
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        self.close_button = QPushButton()
        self.close_button.setFixedSize(28, 28)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setFont(get_mdi_font(16))
        self.close_button.setText(get_icon("close"))
        self.close_button.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.08); border: none; "
            "border-radius: 14px; color: white; }"
            "QPushButton:hover { background: rgba(255,255,255,0.18); }"
        )
        self.close_button.clicked.connect(self.request_close)
        header_layout.addWidget(self.close_button)
        content_layout.addWidget(header)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_scroll.setStyleSheet("background: transparent; border: none;")

        self.chat_container = QWidget()
        self.chat_container.setStyleSheet("background: transparent;")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(18, 4, 18, 4)
        self.chat_layout.setSpacing(10)
        self.chat_layout.addStretch(1)  # keeps early messages pinned near the top

        self.chat_scroll.setWidget(self.chat_container)
        # Fires exactly when new content actually changes the scrollable range —
        # more reliable than a deferred timer, which can fire before layout catches up.
        self.chat_scroll.verticalScrollBar().rangeChanged.connect(self._on_chat_range_changed)
        content_layout.addWidget(self.chat_scroll, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(
            "color: rgba(255,255,255,0.55); font-size: 11px; background: transparent; padding: 0 18px;"
        )
        content_layout.addWidget(self.status_label)

        input_row = QWidget()
        row = QHBoxLayout(input_row)
        row.setContentsMargins(18, 8, 18, 16)
        row.setSpacing(8)

        self.input = QLineEdit()
        self.input.setPlaceholderText(t("assist.input.placeholder"))
        self.input.returnPressed.connect(self._on_submit)
        self.input.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.08); border: none; border-radius: 8px; "
            "padding: 8px 12px; color: white; font-size: 14px; }"
        )
        row.addWidget(self.input, 1)

        self.mic_button = QPushButton()
        self.mic_button.setFixedSize(36, 36)
        self.mic_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic_button.setFont(get_mdi_font(18))
        self.mic_button.setText(get_icon("microphone"))
        self.mic_button.clicked.connect(self._on_mic_clicked)
        row.addWidget(self.mic_button)

        content_layout.addWidget(input_row)
        self._update_mic_style()

    def start_morph(self, start_geo: QRect, target_geo: QRect, color: QColor = None, base_color: QColor = None,
                     instant_close: bool = False):
        self._color = color or QColor("#4285F4")
        self._base_color = base_color or QColor("#2d2d2d")
        self._listening = False
        self._instant_close = instant_close
        self._content.setVisible(False)
        self.status_label.setText("")
        self.input.clear()
        self.clear_chat()
        self._update_mic_style()
        self._start_morph_animations(start_geo, target_geo)

    def on_anim_finished(self):
        super().on_anim_finished()
        if not self._is_closing:
            self._content.setVisible(True)
            self.input.setFocus()

    def request_close(self):
        """Close via the normal fade+shrink, or instantly if this session was
        opened quietly (hotkey while the dashboard was hidden) — a fade here
        would otherwise reveal the grid through the overlay as it fades."""
        if self._instant_close:
            self.close_instant()
        else:
            self.close_morph()

    def close_instant(self):
        self.anim.stop()
        self.content_anim.stop()
        self.input.clearFocus()
        self._content.setVisible(False)
        self._is_closing = True
        self.hide()
        self.finished.emit()

    def close_morph(self):
        self.input.clearFocus()
        self._content.setVisible(False)
        super().close_morph()

    def clear_chat(self):
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def add_user_message(self, text: str):
        self._append_bubble(text, 'user')

    def add_assistant_message(self, text: str):
        self.set_status("")
        self._append_bubble(text, 'assistant')

    def add_error_message(self, text: str):
        self.set_status("")
        self._append_bubble(text, 'error')

    def _append_bubble(self, text: str, role: str):
        row_widget = QWidget()
        row_widget.setStyleSheet("background: transparent;")
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)

        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bubble.setMaximumWidth(_BUBBLE_MAX_WIDTH)
        bg, fg = _BUBBLE_STYLES.get(role, _BUBBLE_STYLES['assistant'])
        bubble.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {fg}; border-radius: 12px; padding: 9px 13px; font-size: 13px; }}"
        )

        if role == 'user':
            row.addStretch(1)
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch(1)

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, row_widget)

    def _on_chat_range_changed(self, _minimum, maximum):
        self.chat_scroll.verticalScrollBar().setValue(maximum)

    def set_status(self, text: str):
        self.status_label.setText(text)

    def set_listening(self, listening: bool):
        self._listening = listening
        self._update_mic_style()

    def _update_mic_style(self):
        bg = "rgba(234,67,53,0.85)" if self._listening else "rgba(255,255,255,0.1)"
        self.mic_button.setStyleSheet(
            f"QPushButton {{ background: {bg}; border: none; border-radius: 18px; color: white; }}"
        )

    def _on_submit(self):
        text = self.input.text().strip()
        if not text:
            return
        self.add_user_message(text)
        self.text_submitted.emit(text)
        self.input.clear()

    def _on_mic_clicked(self):
        if self._listening:
            self.voice_stop_requested.emit()
        else:
            self.voice_start_requested.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.request_close()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw_close_fade(painter)

        rect = self.rect()
        self._draw_background(painter, rect)
        DashboardButtonPainter.draw_image_edge_effects(
            painter, QRectF(rect), is_top_clamped=False, is_light=self._is_light_bg()
        )
        self._draw_border_animation(painter, rect)
