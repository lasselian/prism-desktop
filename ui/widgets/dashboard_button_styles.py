from PyQt6.QtGui import QColor
from ui.styles import Typography, Dimensions


class DashboardButtonStyleManager:
    """Handles QSS styling for DashboardButton."""

    @staticmethod
    def _get_gradient(color_str, lighten_factor=110):
        c_base = QColor(color_str)
        # For near-white colors, lightening does nothing — darken the bottom instead
        if c_base.lightnessF() > 0.90:
            c_top = c_base
            c_bottom = c_base.darker(115)
        else:
            c_top = c_base.lighter(lighten_factor)
            c_bottom = c_base
        color_top = f"rgba({c_top.red()}, {c_top.green()}, {c_top.blue()}, {c_base.alphaF():.2f})"
        color_bottom = f"rgba({c_bottom.red()}, {c_bottom.green()}, {c_bottom.blue()}, {c_base.alphaF():.2f})"
        return f"background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 {color_top}, stop: 1 {color_bottom});"

    @staticmethod
    def apply_style(button):
        """Update visual style based on state and theme."""
        colors = button.theme_manager.get_colors() if button.theme_manager else _DEFAULT_COLORS
        is_gradient = getattr(button, 'button_style', 'Gradient') == 'Gradient'
        button.setStyleSheet(DashboardButtonStyleManager._stylesheet_for(button, colors, is_gradient))

    @staticmethod
    def _stylesheet_for(button, colors: dict, is_gradient: bool) -> str:
        cls = DashboardButtonStyleManager
        if not button.config:
            return cls._muted_style(colors, is_gradient)  # empty slot
        btn_type = button.config.get('type')
        if btn_type == 'sun':
            return cls._plain_style(colors, is_gradient)
        if btn_type == 'forbidden':
            # Forbidden slot: visually muted, no hover, no interaction
            return cls._muted_style(colors, is_gradient, label_extra=" opacity: 0.5;")
        if btn_type == 'lock' and button._state == 'jammed':
            return cls._jammed_style(is_gradient)
        if cls._is_active(button):
            return cls._active_style(button, colors, is_gradient)
        return cls._off_style(colors, is_gradient)

    @staticmethod
    def _background(color: str, is_gradient: bool, lighten_factor: int) -> str:
        if is_gradient:
            return DashboardButtonStyleManager._get_gradient(color, lighten_factor)
        return f"background-color: {color};"

    @staticmethod
    def _is_active(button) -> bool:
        """On, open, running and the like: drawn in the button's colour."""
        cfg = button.config or {}
        return (
            button._state in _ACTIVE_STATES
            or cfg.get('type') == 'script'
            or (cfg.get('type') == 'input_number' and bool(cfg.get('color')))
        )

    @staticmethod
    def _muted_style(colors: dict, is_gradient: bool, label_extra: str = "") -> str:
        bg_style = DashboardButtonStyleManager._background(colors['alternate_base'], is_gradient, 105)
        return f"""
                DashboardButton {{
                    {bg_style}
                    border-radius: {Dimensions.RADIUS_XLARGE};
                }}
                QLabel {{ color: {colors['border']}; background: transparent;{label_extra} }}
            """

    @staticmethod
    def _plain_style(colors: dict, is_gradient: bool) -> str:
        background = DashboardButtonStyleManager._background
        return f"""
                DashboardButton {{
                    {background(colors['base'], is_gradient, 115)}
                    border-radius: 12px;
                }}
                DashboardButton:hover {{
                    {background(colors['alternate_base'], is_gradient, 115)}
                }}
            """

    @staticmethod
    def _jammed_style(is_gradient: bool) -> str:
        # Jammed lock: unmissable alert red, independent of the button's configured color
        bg_style = DashboardButtonStyleManager._background('#EA4335', is_gradient, 115)
        return f"""
                DashboardButton {{
                    {bg_style}
                    border-radius: {Dimensions.RADIUS_XLARGE};
                }}
                QLabel#valueLabel {{
                    color: rgba(255, 255, 255, 0.9);
                    background: transparent;
                    font-weight: {Typography.WEIGHT_REGULAR};
                    font-size: {Typography.SIZE_BUTTON_ICON_LARGE};
                }}
                QLabel#nameLabel {{
                    color: rgba(255, 255, 255, 0.95);
                    background: transparent;
                    {_NAME_LABEL_FONT}
                }}
            """

    @staticmethod
    def _active_color(button, colors: dict) -> str:
        """The button's colour (or the accent), adapted to the theme and dimmed by brightness."""
        button_color = button.config.get('color', colors['accent'])
        # #3c3c3c is the "dynamic" sensor colour: white in light mode, gray in dark mode.
        if str(button_color).lower() == "#3c3c3c":
            light = button.theme_manager and button.theme_manager.get_effective_theme() == 'light'
            button_color = "#ffffff" if light else "#3c3c3c"

        # Dimming: brightness 0-255 maps to alpha 60-255, so the tile never disappears.
        if getattr(button, '_show_dimming', False) and getattr(button, '_brightness', None) is not None:
            bri = int(getattr(button, '_brightness', 255))
            if bri < 255:
                c = QColor(button_color)
                c.setAlpha(int(60 + (bri / 255.0) * 195))
                button_color = f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha() / 255.0:.2f})"
        return button_color

    @staticmethod
    def _active_style(button, colors: dict, is_gradient: bool) -> str:
        button_color = DashboardButtonStyleManager._active_color(button, colors)
        if str(button_color).lower() == "#ffffff":  # light tile: dark text
            icon_color, text_color = "rgba(0, 0, 0, 0.65)", "rgba(30, 30, 30, 1.0)"
        else:
            icon_color, text_color = "rgba(255, 255, 255, 0.65)", "rgba(255, 255, 255, 1.0)"
        # QColor can't parse CSS rgba() strings, so no gradient while dimmed.
        use_gradient = is_gradient and not str(button_color).startswith('rgba')
        bg_style = DashboardButtonStyleManager._background(button_color, use_gradient, 115)
        return f"""
                DashboardButton {{
                    {bg_style}
                    border-radius: {Dimensions.RADIUS_XLARGE};
                }}
                QLabel#valueLabel {{ 
                    color: {icon_color}; 
                    background: transparent; 
                    font-size: {Typography.SIZE_BUTTON_VALUE}; font-weight: {Typography.WEIGHT_MEDIUM};
                }}
                /* Beefier font for Icons (Switch/Light/Script) */
                DashboardButton[type="switch"] QLabel#valueLabel,
                DashboardButton[type="script"] QLabel#valueLabel,
                DashboardButton[type="scene"] QLabel#valueLabel,
                DashboardButton[type="fan"] QLabel#valueLabel,
                DashboardButton[type="automation"] QLabel#valueLabel {{
                     color: {icon_color};
                     font-weight: {Typography.WEIGHT_REGULAR}; 
                     font-size: {Typography.SIZE_BUTTON_ICON_LARGE}; /* Significantly larger icon */
                }}
                /* Climate/Widget shows text/value - keep it readable */
                DashboardButton[type="climate"] QLabel#valueLabel,
                DashboardButton[type="widget"] QLabel#valueLabel {{
                     color: {text_color};
                     font-weight: {Typography.WEIGHT_REGULAR}; 
                     font-size: {Typography.SIZE_BUTTON_ICON_SMALL};
                }}
                /* Input Number text readability */
                DashboardButton[type="input_number"] QLabel#valueLabel {{
                     color: {text_color};
                }}
                /* Curtain uses icon */
                DashboardButton[type="curtain"] QLabel#valueLabel {{
                     color: {icon_color};
                     font-weight: {Typography.WEIGHT_REGULAR}; 
                     font-size: {Typography.SIZE_BUTTON_ICON_LARGE}; 
                }}
                /* Weather style handled dynamically in code but defaults here */
                DashboardButton[type="weather"] QLabel#valueLabel {{
                     color: {text_color};
                     font-weight: {Typography.WEIGHT_REGULAR};
                     /* flow: multiline */
                }}
                QLabel#nameLabel {{ 
                    color: {text_color}; 
                    background: transparent;
                    opacity: 0.9;
                    {_NAME_LABEL_FONT}
                }}
            """

    @staticmethod
    def _off_style(colors: dict, is_gradient: bool) -> str:
        background = DashboardButtonStyleManager._background
        return f"""
                DashboardButton {{
                    {background(colors['base'], is_gradient, 115)}
                    border-radius: 12px;
                }}
                DashboardButton:hover {{
                    {background(colors['alternate_base'], is_gradient, 115)}
                }}
                QLabel#valueLabel {{ 
                    color: {colors['text']}; 
                    background: transparent;
                    font-size: {Typography.SIZE_BUTTON_VALUE}; font-weight: {Typography.WEIGHT_MEDIUM};
                }}
                DashboardButton[type="switch"] QLabel#valueLabel,
                DashboardButton[type="script"] QLabel#valueLabel,
                DashboardButton[type="scene"] QLabel#valueLabel,
                DashboardButton[type="fan"] QLabel#valueLabel,
                DashboardButton[type="automation"] QLabel#valueLabel {{
                     font-weight: 400; 
                     font-size: 26px; /* Significantly larger icon */
                }}
                DashboardButton[type="climate"] QLabel#valueLabel {{
                     font-weight: 400; 
                     font-size: 20px;
                }}
                DashboardButton[type="weather"] QLabel#valueLabel {{
                     font-weight: 400;
                }}
                QLabel#nameLabel {{ 
                    color: {colors.get('subtext', '#888888')}; 
                    background: transparent;
                    {_NAME_LABEL_FONT}
                }}
            """


_DEFAULT_COLORS = {
    'base': '#2d2d2d',
    'accent': '#0078d4',
    'text': '#ffffff',
    'border': '#555555',
    'alternate_base': '#353535',
    'subtext': '#888888',
}

_ACTIVE_STATES = frozenset({"on", "open", "locked", "mowing", "returning", "cleaning"})

# Tile labels: small, semibold, uppercase.
_NAME_LABEL_FONT = (
    f'font-family: "{Typography.FONT_FAMILY_MAIN}"; font-size: {Typography.SIZE_BUTTON_LABEL}; '
    f'font-weight: {Typography.WEIGHT_SEMIBOLD}; text-transform: uppercase;'
)
