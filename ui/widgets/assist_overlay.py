"""
Assist chat overlay: full-window text/voice chat that talks to Home
Assistant's Assist pipeline API. Unlike the other overlays it isn't tied to
a specific grid button/entity — it's opened from the footer button or a
global hotkey, so callers just hand it the rect to appear at (see
open_fade_in).
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QLabel,
    QScrollArea,
    QFrame,
    QGraphicsOpacityEffect,
)
from PyQt6.QtCore import (
    Qt,
    QRect,
    QRectF,
    QPointF,
    QEvent,
    QTimer,
    QPropertyAnimation,
    QEasingCurve,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter, QPalette

from ui.icons import get_icon, get_mdi_font
from core.localization_manager import t
from ui.widgets.dashboard_button_painter import DashboardButtonPainter
from ui.widgets.overlays import BaseOverlay

_BUBBLE_MAX_WIDTH = 460
_HISTORY_LIMIT = 50
_RED = QColor(234, 67, 53)


def _luminance(c: QColor) -> float:
    return 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()


def _rgba(c: QColor, alpha: float) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha:.2f})"


class _TypingIndicator(QWidget):
    """Three pulsing dots in an assistant-style bubble. Animated by a timer
    and plain painting — QGraphicsOpacityEffect nests badly with the
    overlay's own fade effect."""

    _W, _H = 58, 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self._W, self._H)
        self._bubble = QColor(255, 255, 255, 20)
        self._dot = QColor(255, 255, 255)
        self._phase = 0
        self._timer = QTimer(self)
        self._timer.setInterval(140)
        self._timer.timeout.connect(self._tick)

    def set_colors(self, bubble: QColor, dot: QColor):
        self._bubble, self._dot = bubble, dot
        self.update()

    def _tick(self):
        self._phase = (self._phase + 1) % 6
        self.update()

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._bubble)
        p.drawRoundedRect(QRectF(self.rect()), 12, 12)
        for i in range(3):
            active = (self._phase // 2) == i
            c = QColor(self._dot)
            c.setAlphaF(0.95 if active else 0.35)
            p.setBrush(c)
            r = 3.6 if active else 3.0
            p.drawEllipse(QPointF(17 + i * 12, self._H / 2), r, r)


class _ActionButton(QPushButton):
    """Round input-row button that switches between mic / send / stop, with
    a halo that follows the microphone level while listening."""

    SIZE = 40
    RADIUS = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(get_mdi_font(18))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mode = 'mic'
        self._accent = QColor("#7C4DFF")
        self._fg = QColor(255, 255, 255)
        self._target_level = 0.0
        self._level = 0.0
        self._hover = False
        self._level_timer = QTimer(self)
        self._level_timer.setInterval(33)
        self._level_timer.timeout.connect(self._step_level)

    # "send" maps to a codepoint that renders as a stray glyph in the bundled
    # MDI font; "send-outline" draws the paper plane correctly.
    _ICONS = {'mic': "microphone", 'send': "send-outline", 'stop_listen': "stop", 'stop': "stop"}

    def set_colors(self, accent: QColor, fg: QColor):
        self._accent, self._fg = QColor(accent), QColor(fg)
        self.update()

    def set_mode(self, mode: str):
        if mode == self.mode:
            return
        self.mode = mode
        self.setText(get_icon(self._ICONS[mode]))
        tips = {
            'mic': t("assist.button.mic"),
            'send': t("assist.button.send"),
            'stop_listen': t("assist.button.stop_listening"),
            'stop': t("assist.button.stop"),
        }
        self.setToolTip(tips[mode])
        if mode == 'stop_listen':
            self._level_timer.start()
        else:
            self._level_timer.stop()
            self._level = self._target_level = 0.0
        self.update()

    def set_level(self, level: float):
        self._target_level = max(0.0, min(1.0, level))

    def _step_level(self):
        # Rise fast, fall slowly — reads as a VU meter rather than flicker.
        if self._target_level > self._level:
            self._level += (self._target_level - self._level) * 0.6
        else:
            self._level *= 0.85
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        center = QPointF(self.width() / 2, self.height() / 2)

        if self.mode == 'stop_listen':
            halo = QColor(_RED)
            halo.setAlphaF(0.28)
            p.setBrush(halo)
            r = self.RADIUS + 1 + self._level * (self.SIZE / 2 - self.RADIUS - 1)
            p.drawEllipse(center, r, r)
            bg, icon = QColor(_RED), QColor(255, 255, 255)
        elif self.mode == 'send':
            bg = QColor(self._accent)
            icon = QColor(255, 255, 255) if _luminance(bg) < 170 else QColor(0, 0, 0)
        else:
            bg = QColor(self._fg)
            bg.setAlphaF(0.16 if self._hover else 0.10)
            icon = QColor(self._fg)

        if self._hover and self.mode in ('send', 'stop_listen'):
            bg = bg.lighter(112)
        p.setBrush(bg)
        p.drawEllipse(center, self.RADIUS, self.RADIUS)

        p.setPen(icon)
        p.setFont(self.font())
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


class AssistOverlay(BaseOverlay):
    """Full-window chat popup with a text field and a mic/send/stop button."""

    text_submitted = pyqtSignal(str)
    voice_start_requested = pyqtSignal()
    voice_stop_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    stop_speaking_requested = pyqtSignal()
    new_chat_requested = pyqtSignal()
    content_changed = pyqtSignal()

    _FADE_DURATION = 160

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = "Assist"
        self._color = QColor("#7C4DFF")
        self._base_color = QColor("#2d2d2d")
        self._listening = False
        self._busy = False
        self._speaking = False
        self._instant_close = False
        self._fade_anim = None
        self._rows = []  # [(row_widget, bubble_label, role)]
        self._partial_bubble = None
        self._history = []
        self._history_idx = None  # None = editing a fresh line
        self._history_draft = ""
        self._build_content()
        self._apply_theme()

    # ── Construction ──

    def _build_content(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._content = QWidget(self)
        outer.addWidget(self._content)

        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        header = QWidget()
        self._header = header
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 14, 14, 8)
        header_layout.setSpacing(6)

        self._title = QLabel(t("assist.chat.title"))
        header_layout.addWidget(self._title)
        header_layout.addStretch(1)

        self.new_chat_button = self._make_header_button("chat-plus-outline", t("assist.button.new_chat"))
        self.new_chat_button.clicked.connect(self._on_new_chat)
        header_layout.addWidget(self.new_chat_button)

        self.close_button = self._make_header_button("close", t("assist.button.close"))
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

        # Empty state, shown until the first message of a conversation.
        self._empty_hint = QWidget()
        hint_layout = QVBoxLayout(self._empty_hint)
        hint_layout.setContentsMargins(4, 10, 4, 6)
        hint_layout.setSpacing(6)
        self._empty_icon = QLabel(get_icon("chat-processing-outline"))
        self._empty_icon.setFont(get_mdi_font(26))
        self._empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_text = QLabel(t("assist.empty.hint"))
        self._empty_text.setWordWrap(True)
        self._empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_layout.addWidget(self._empty_icon)
        hint_layout.addWidget(self._empty_text)
        self.chat_layout.addWidget(self._empty_hint)

        # Bubbles are inserted before this row, which holds the typing dots.
        self._typing_row = QWidget()
        typing_layout = QHBoxLayout(self._typing_row)
        typing_layout.setContentsMargins(0, 0, 0, 0)
        self._typing = _TypingIndicator()
        typing_layout.addWidget(self._typing)
        typing_layout.addStretch(1)
        self._typing_row.hide()
        self.chat_layout.addWidget(self._typing_row)

        self.chat_layout.addStretch(1)  # keeps early messages pinned near the top

        self.chat_scroll.setWidget(self.chat_container)
        # Fires once layout has actually settled — more reliable than a deferred timer.
        self.chat_scroll.verticalScrollBar().rangeChanged.connect(self._on_chat_range_changed)
        content_layout.addWidget(self.chat_scroll, 1)

        self.status_label = QLabel("")
        content_layout.addWidget(self.status_label)

        input_row = QWidget()
        self._input_row = input_row
        row = QHBoxLayout(input_row)
        row.setContentsMargins(18, 6, 14, 14)
        row.setSpacing(6)

        self.input = QLineEdit()
        self.input.setPlaceholderText(t("assist.input.placeholder"))
        self.input.returnPressed.connect(self._on_submit)
        self.input.textChanged.connect(self._update_action_button)
        self.input.installEventFilter(self)
        row.addWidget(self.input, 1)

        self.mic_button = _ActionButton()
        self.mic_button.clicked.connect(self._on_action_clicked)
        row.addWidget(self.mic_button)

        content_layout.addWidget(input_row)
        self.mic_button.mode = None  # force the first set_mode to apply
        self._update_action_button()
        self._update_empty_state()

    def _make_header_button(self, icon: str, tooltip: str) -> QPushButton:
        button = QPushButton()
        button.setFixedSize(28, 28)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setFont(get_mdi_font(16))
        button.setText(get_icon(icon))
        button.setToolTip(tooltip)
        return button

    # ── Theming ──

    def _palette(self) -> dict:
        light = self._is_light_bg()
        fg = QColor(0, 0, 0) if light else QColor(255, 255, 255)
        accent = QColor(self._color)
        user_fg = "white" if _luminance(accent) < 170 else "black"
        return {
            'light': light,
            'fg': fg,
            'accent': accent,
            'user': (_rgba(accent, 0.92), user_fg),
            'assistant': (_rgba(fg, 0.07 if light else 0.08), _rgba(fg, 0.9)),
            'warning': (
                "rgba(251,188,4,0.20)" if light else "rgba(251,188,4,0.14)",
                "#5f4300" if light else "#ffe08a",
            ),
            'error': (
                "rgba(234,67,53,0.13)" if light else "rgba(234,67,53,0.22)",
                "#b3261e" if light else "#ffb4ab",
            ),
        }

    def _apply_theme(self):
        pal = self._palette()
        fg = pal['fg']

        self._title.setStyleSheet(
            f"color: {_rgba(fg, 0.8)}; font-size: 12px; font-weight: 600; "
            "letter-spacing: 1px; background: transparent;"
        )
        header_btn_style = (
            f"QPushButton {{ background: {_rgba(fg, 0.07)}; border: none; "
            f"border-radius: 14px; color: {_rgba(fg, 0.85)}; }}"
            f"QPushButton:hover {{ background: {_rgba(fg, 0.16)}; }}"
            f"QPushButton:disabled {{ background: transparent; color: {_rgba(fg, 0.25)}; }}"
        )
        self.close_button.setStyleSheet(header_btn_style)
        self.new_chat_button.setStyleSheet(header_btn_style)

        self.status_label.setStyleSheet(
            f"color: {_rgba(fg, 0.55)}; font-size: 11px; background: transparent; padding: 0 18px;"
        )
        self._empty_icon.setStyleSheet(f"color: {_rgba(pal['accent'], 0.9)}; background: transparent;")
        self._empty_text.setStyleSheet(f"color: {_rgba(fg, 0.55)}; font-size: 12px; background: transparent;")

        self.input.setStyleSheet(
            f"QLineEdit {{ background: {_rgba(fg, 0.07)}; border: 1px solid transparent; "
            f"border-radius: 18px; padding: 8px 14px; color: {_rgba(fg, 0.92)}; font-size: 14px; }}"
            f"QLineEdit:focus {{ border: 1px solid {_rgba(pal['accent'], 0.55)}; }}"
        )
        qpal = self.input.palette()
        qpal.setColor(QPalette.ColorRole.PlaceholderText, QColor(fg.red(), fg.green(), fg.blue(), 110))
        self.input.setPalette(qpal)

        self.mic_button.set_colors(pal['accent'], fg)
        bubble = QColor(fg)
        bubble.setAlphaF(0.07 if pal['light'] else 0.08)
        self._typing.set_colors(bubble, fg)

        for _row, label, role in self._rows:
            self._style_bubble(label, role, pal)

    def _style_bubble(self, label: QLabel, role: str, pal: dict | None = None):
        pal = pal or self._palette()
        bg, fg = pal.get(role, pal['assistant'])
        label.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {fg}; border-radius: 12px; " "padding: 9px 13px; font-size: 13px; }"
        )

    # ── Open / close ──

    def open_fade_in(
        self,
        target_rect: QRect,
        color: QColor = None,
        base_color: QColor = None,
        instant_close: bool = False,
        instant: bool = False,
    ):
        """Show the panel directly at its final size/position and fade it
        in. With instant=True, appear immediately with no fade at all —
        used when the dashboard itself was just shown quietly for this same
        open, so nothing should flash."""
        self._color = color or QColor("#7C4DFF")
        self._base_color = base_color or QColor("#2d2d2d")
        # Listening/busy/speaking and the status line are NOT reset here: they
        # follow the controller's signals, and auto-listen may already have
        # started the mic during the grid fade that precedes this call.
        # Closing resets them (controller.cancel() + request_close).
        self._instant_close = instant_close
        self._content.setVisible(False)
        self.input.clear()
        self._history_idx = None
        self.clear_chat()
        self._apply_theme()
        self._update_action_button()
        self.setGeometry(target_rect)

        self.anim_border.stop()
        self.anim_border.setStartValue(0.0)
        self.anim_border.setEndValue(1.0)
        self.anim_border.start()

        self.show()
        self.raise_()
        self.activateWindow()

        if instant:
            self._content.setVisible(True)
            self.input.setFocus()
            return

        self._fade(0.0, 1.0, QEasingCurve.Type.OutQuad, self._on_fade_in_finished)

    def _on_fade_in_finished(self):
        self._content.setVisible(True)
        self.input.setFocus()

    def request_close(self):
        """Instant if this session was opened quietly (hotkey while the
        dashboard was hidden — a fade would reveal the grid), otherwise a
        quick fade-out."""
        self.input.clearFocus()
        self._content.setVisible(False)
        self.status_label.setText("")
        self._listening = self._busy = self._speaking = False
        self._update_action_button()
        if self._instant_close:
            if self._fade_anim is not None:
                self._fade_anim.stop()
                self._fade_anim = None
                self.setGraphicsEffect(None)
            self.hide()
            self.finished.emit()
        else:
            self._fade(1.0, 0.0, QEasingCurve.Type.Linear, self._on_fade_out_finished)

    def _on_fade_out_finished(self):
        self.hide()
        self.finished.emit()

    def _fade(self, start: float, end: float, easing: QEasingCurve.Type, on_finished):
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(start)
        self.setGraphicsEffect(effect)

        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(self._FADE_DURATION)
        anim.setEasingCurve(easing)
        anim.setStartValue(start)
        anim.setEndValue(end)

        def _finished():
            self.setGraphicsEffect(None)
            self._fade_anim = None
            on_finished()

        anim.finished.connect(_finished)
        self._fade_anim = anim  # keep alive until finished
        anim.start()

    # ── Chat content ──

    def clear_chat(self):
        for row_widget, _label, _role in self._rows:
            self.chat_layout.removeWidget(row_widget)
            row_widget.deleteLater()
        self._rows.clear()
        self._partial_bubble = None
        self._typing_row.hide()
        self._update_empty_state()

    def add_user_message(self, text: str):
        self._append_bubble(text, 'user')

    def add_assistant_message(self, text: str, is_error: bool = False):
        """Final reply. Replaces the streamed bubble if one is in progress."""
        self.set_status("")
        self.show_typing(False)
        role = 'warning' if is_error else 'assistant'
        if self._partial_bubble is not None:
            label = self._partial_bubble
            self._partial_bubble = None
            label.setText(text)
            for i, (row, lbl, _r) in enumerate(self._rows):
                if lbl is label:
                    self._rows[i] = (row, lbl, role)
            self._style_bubble(label, role)
        else:
            self._append_bubble(text, role)

    def update_partial_reply(self, text: str):
        """Streamed reply text so far (agents that stream their answer)."""
        self.show_typing(False)
        if self._partial_bubble is None:
            self._partial_bubble = self._append_bubble(text, 'assistant')
        else:
            self._partial_bubble.setText(text)

    def add_error_message(self, text: str):
        self.set_status("")
        self.show_typing(False)
        self._partial_bubble = None
        self._append_bubble(text, 'error')

    def show_typing(self, visible: bool):
        if visible and self._partial_bubble is not None:
            return
        self._typing_row.setVisible(visible)

    def _append_bubble(self, text: str, role: str) -> QLabel:
        row_widget = QWidget()
        row_widget.setStyleSheet("background: transparent;")
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)

        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextFormat(Qt.TextFormat.PlainText)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bubble.setMaximumWidth(_BUBBLE_MAX_WIDTH)
        self._style_bubble(bubble, role)

        if role == 'user':
            row.addStretch(1)
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch(1)

        self.chat_layout.insertWidget(self.chat_layout.indexOf(self._typing_row), row_widget)
        self._rows.append((row_widget, bubble, role))
        self._update_empty_state()
        return bubble

    def _update_empty_state(self):
        has_rows = bool(self._rows)
        self._empty_hint.setVisible(not has_rows)
        self.new_chat_button.setEnabled(has_rows)

    def _on_chat_range_changed(self, _minimum, maximum):
        self.chat_scroll.verticalScrollBar().setValue(maximum)
        self.content_changed.emit()

    def _on_new_chat(self):
        self.clear_chat()
        self.set_status("")
        self.new_chat_requested.emit()
        self.input.setFocus()
        self.content_changed.emit()

    def measure_target_content_height(self, width: int | None = None) -> int:
        """Natural height of the header/status/input chrome plus the chat
        content (bubbles, empty hint, typing dots), used by the parent window
        to size itself around this overlay's content.

        `width` is the overlay width the caller is about to apply. A
        word-wrapped label's sizeHint guesses its own width, so without it
        the empty hint can be measured a line taller than it renders."""
        chat_height = self.chat_container.sizeHint().height()
        if width and not self._rows:
            hint_m = self._empty_hint.layout().contentsMargins()
            chat_m = self.chat_layout.contentsMargins()
            text_width = width - chat_m.left() - chat_m.right() - hint_m.left() - hint_m.right()
            if text_width > 0:
                chat_height += self._empty_text.heightForWidth(text_width) - self._empty_text.sizeHint().height()
        return (
            self._header.sizeHint().height()
            + self.status_label.sizeHint().height()
            + self._input_row.sizeHint().height()
            + chat_height
        )

    def retarget(self, rect: QRect):
        """Reposition/resize instantly to a new rect while already open."""
        self.setGeometry(rect)

    # ── State from the controller ──

    def set_status(self, text: str):
        self.status_label.setText(text)

    def set_listening(self, listening: bool):
        self._listening = listening
        self._update_action_button()

    def set_busy(self, busy: bool):
        self._busy = busy
        if not busy:
            # A run that ended without a final reply (stopped, failed) leaves
            # its streamed text as-is; the next reply gets its own bubble.
            self._partial_bubble = None
            self.show_typing(False)
        self._update_action_button()

    def set_speaking(self, speaking: bool):
        self._speaking = speaking
        self._update_action_button()

    def set_input_level(self, level: float):
        self.mic_button.set_level(level)

    def _update_action_button(self, *_args):
        if self._listening:
            mode = 'stop_listen'
        elif self._busy:
            mode = 'stop'
        elif self.input.text().strip():
            mode = 'send'
        else:
            mode = 'mic'
        self.mic_button.set_mode(mode)

    # ── User input ──

    def _on_submit(self):
        text = self.input.text().strip()
        if not text or self._busy or self._listening:
            return
        if not self._history or self._history[-1] != text:
            self._history.append(text)
            del self._history[:-_HISTORY_LIMIT]
        self._history_idx = None
        self.add_user_message(text)
        self.input.clear()
        self.show_typing(True)
        self.text_submitted.emit(text)

    def _on_action_clicked(self):
        mode = self.mic_button.mode
        if mode == 'stop_listen':
            self.voice_stop_requested.emit()
        elif mode == 'stop':
            self.cancel_requested.emit()
        elif mode == 'send':
            self._on_submit()
        else:
            self.voice_start_requested.emit()

    def _recall_history(self, step: int) -> bool:
        if not self._history:
            return False
        if self._history_idx is None:
            if step > 0:
                return False
            self._history_draft = self.input.text()
            idx = len(self._history) - 1
        else:
            idx = self._history_idx + step
        if idx >= len(self._history):
            self._history_idx = None
            self.input.setText(self._history_draft)
            return True
        idx = max(0, idx)
        self._history_idx = idx
        self.input.setText(self._history[idx])
        return True

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Up:
                return self._recall_history(-1)
            if event.key() == Qt.Key.Key_Down:
                return self._recall_history(+1)
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            # Esc peels back one layer at a time before closing the popup.
            if self._listening:
                self.voice_stop_requested.emit()
            elif self._busy:
                self.cancel_requested.emit()
            elif self._speaking:
                self.stop_speaking_requested.emit()
            else:
                self.request_close()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        self._draw_background(painter, rect)
        DashboardButtonPainter.draw_image_edge_effects(
            painter, QRectF(rect), is_top_clamped=False, is_light=self._is_light_bg()
        )
        self._draw_border_animation(painter, rect)
