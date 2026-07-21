from PyQt6.QtWidgets import QPushButton
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QPainter
from ui.widgets.dashboard_button_painter import DashboardButtonPainter

class FooterButton(QPushButton):
    """
    A specialized button for the dashboard footer that inherits the
    universal physical bevel/glass effect.
    """

    button_style = 'Gradient'

    def paintEvent(self, event):
        # Draw standard QPushButton (including stylesheet backgrounds)
        super().paintEvent(event)

        if self.button_style != 'Gradient':
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Apply the shared glass edge effect using the footer's corner radius (4px)
        DashboardButtonPainter.draw_button_bevel_edge(
            painter,
            QRectF(self.rect()),
            intensity_modifier=0.25,
            corner_radius=4,
        )

        painter.end()
