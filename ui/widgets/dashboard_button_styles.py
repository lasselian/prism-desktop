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
        if button.theme_manager:
            colors = button.theme_manager.get_colors()
        else:
            colors = {
                'base': '#2d2d2d',
                'accent': '#0078d4',
                'text': '#ffffff',
                'border': '#555555',
                'alternate_base': '#353535',
                'subtext': '#888888',
            }
        
        # Use cleaner, Apple-style typography
        # Main Value: Large, Thin/Light
        # Label: Small, Uppercase, Tracking
        
        font_weight_val = Typography.WEIGHT_MEDIUM
        font_size_val = Typography.SIZE_BUTTON_VALUE
        
        font_label = Typography.FONT_FAMILY_MAIN
        font_size_label = Typography.SIZE_BUTTON_LABEL
        font_weight_label = Typography.WEIGHT_SEMIBOLD

        is_gradient = getattr(button, 'button_style', 'Gradient') == 'Gradient'

        if not button.config:
            # Empty
            if is_gradient:
                bg_style = DashboardButtonStyleManager._get_gradient(colors['alternate_base'], 105)
            else:
                bg_style = f"background-color: {colors['alternate_base']};"
                
            button.setStyleSheet(f"""
                DashboardButton {{
                    {bg_style}
                    border-radius: {Dimensions.RADIUS_XLARGE};
                }}
                QLabel {{ color: {colors['border']}; background: transparent; }}
            """)
            return
        if button.config and button.config.get('type') == 'sun':
            if is_gradient:
                bg_style       = DashboardButtonStyleManager._get_gradient(colors['base'], 115)
                bg_hover_style = DashboardButtonStyleManager._get_gradient(colors['alternate_base'], 115)
            else:
                bg_style       = f"background-color: {colors['base']};"
                bg_hover_style = f"background-color: {colors['alternate_base']};"
            button.setStyleSheet(f"""
                DashboardButton {{
                    {bg_style}
                    border-radius: 12px;
                }}
                DashboardButton:hover {{
                    {bg_hover_style}
                }}
            """)
            return
        if button.config and button.config.get('type') == 'forbidden':
            # Forbidden slot: visually muted, no hover, no interaction
            if is_gradient:
                bg_style = DashboardButtonStyleManager._get_gradient(colors['alternate_base'], 105)
            else:
                bg_style = f"background-color: {colors['alternate_base']};"
                
            button.setStyleSheet(f"""
                DashboardButton {{
                    {bg_style}
                    border-radius: {Dimensions.RADIUS_XLARGE};
                }}
                QLabel {{ color: {colors['border']}; background: transparent; opacity: 0.5; }}
            """)
            return
        if (button._state == "on" or button._state == "open" or button._state == "locked" or
            button._state == "mowing" or button._state == "returning" or button._state == "cleaning" or
            (button.config and button.config.get('type') == 'script') or
            (button.config and button.config.get('type') == 'input_number' and button.config.get('color'))):
             # On - Use button's custom color if set, otherwise theme accent
             button_color = button.config.get('color', colors['accent'])
             

             # GLOBAL Dynamic Color Logic (Applies to ALL buttons using #3c3c3c)
             is_dynamic_gray = (str(button_color).lower() == "#3c3c3c")
             
             if is_dynamic_gray:
                 # Dynamic Sensor Color: White in Light Mode, Gray in Dark Mode
                 if button.theme_manager and button.theme_manager.get_effective_theme() == 'light':
                     button_color = "#ffffff"
                 else:
                     button_color = "#3c3c3c"
                     
             # Special case for sensors: Lighten custom colors (but NOT if it's the dynamic gray)
             elif (button.config and button.config.get('type') == 'widget'):
                 # Custom color -> lighten it slightly for visibility
                 # c = QColor(button_color)
                 # button_color = c.lighter(117).name() # 115% brightness
                 pass
             
             # Apply Dimming Logic (if enabled)
             if getattr(button, '_show_dimming', False) and getattr(button, '_brightness', None) is not None:
                 # Only applies if we have a brightness value
                 # Map brightness 0-255 to Alpha 60-255 (so it's never invisible)
                 bri = int(getattr(button, '_brightness', 255))
                 if bri < 255:
                     alpha = int(60 + (bri / 255.0) * 195)
                     c = QColor(button_color)
                     c.setAlpha(alpha)
                     # Convert back to rgba string for stylesheet
                     button_color = f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha() / 255.0:.2f})"
             
             # Determine text contrast based on background brightness
             # Simple heuristic: if background is White (#ffffff), use dark text
             is_light_bg = (str(button_color).lower() == "#ffffff")
             
             if is_light_bg:
                 icon_color = "rgba(0, 0, 0, 0.65)"
                 text_color = "rgba(30, 30, 30, 1.0)"
             else:
                 icon_color = "rgba(255, 255, 255, 0.65)"
                 text_color = "rgba(255, 255, 255, 1.0)"
             
             # QColor can't parse CSS rgba() strings, so skip gradient when dimming is active
             if is_gradient and not str(button_color).startswith('rgba'):
                 bg_style = DashboardButtonStyleManager._get_gradient(button_color, 115)
             else:
                 bg_style = f"background-color: {button_color};"
             
             button.setStyleSheet(f"""
                DashboardButton {{
                    {bg_style}
                    border-radius: {Dimensions.RADIUS_XLARGE};
                }}
                QLabel#valueLabel {{ 
                    color: {icon_color}; 
                    background: transparent; 
                    font-size: {font_size_val}; font-weight: {font_weight_val};
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
                    font-family: "{font_label}"; font-size: {font_size_label}; font-weight: {font_weight_label}; text-transform: uppercase;
                }}
            """)
        else:
            # Off / Widget (Default dark state)
            if is_gradient:
                 bg_style = DashboardButtonStyleManager._get_gradient(colors['base'], 115)
                 bg_hover_style = DashboardButtonStyleManager._get_gradient(colors['alternate_base'], 115)
            else:
                 bg_style = f"background-color: {colors['base']};"
                 bg_hover_style = f"background-color: {colors['alternate_base']};"
            
            button.setStyleSheet(f"""
                DashboardButton {{
                    {bg_style}
                    border-radius: 12px;
                }}
                DashboardButton:hover {{
                    {bg_hover_style}
                }}
                QLabel#valueLabel {{ 
                    color: {colors['text']}; 
                    background: transparent;
                    font-size: {font_size_val}; font-weight: {font_weight_val};
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
                    font-family: "{font_label}"; font-size: {font_size_label}; font-weight: {font_weight_label}; text-transform: uppercase;
                }}
            """)
