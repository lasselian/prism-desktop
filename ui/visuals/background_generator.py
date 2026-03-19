import math
import random
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import (
    QColor, QPainter, QPixmap, QRadialGradient, QBrush,
)


class BackgroundGenerator:
    """
    Generates prismatic light-field backgrounds for media player buttons.

    The effect looks like spectral light projected through frosted glass:
    colored radial gradients are rendered onto a tiny pixmap with additive
    blending, then upscaled — the bilinear interpolation *is* the blur.
    """

    # Prismatic / spectral palettes
    PRISM_PALETTES = [
        ["#7400B8", "#5390D9", "#48BFE3", "#64DFDF"],  # Deep violet → cyan spectrum
        ["#FF006E", "#8338EC", "#3A86FF"],              # Magenta → purple → blue
        ["#3A0CA3", "#4361EE", "#4CC9F0"],              # Royal blue → electric cyan
        ["#F72585", "#B5179E", "#7209B7", "#3A0CA3"],   # Hot pink → deep purple
        ["#560BAD", "#480CA8", "#3F37C9", "#4895EF"],   # Indigo cascade
        ["#7209B7", "#4CC9F0", "#F72585"],              # Purple → cyan → pink (triadic)
        ["#4361EE", "#F72585", "#4CC9F0"],              # Blue → pink → cyan
        ["#3A86FF", "#8338EC", "#FF006E", "#48BFE3"],   # Full prism
    ]

    # Golden ratio — used to create irrational frequency ratios
    _PHI = 1.6180339887

    @staticmethod
    def generate(width: int, height: int, seed: int = None, palette: list[str] = None) -> QPixmap:
        """
        Generate a static background pixmap (single frozen frame of the light field).

        Args:
            width: Width of the background.
            height: Height of the background.
            seed: Random seed for deterministic generation.
            palette: Optional list of hex color strings or QColors.

        Returns:
            QPixmap: The generated background.
        """
        layers = BackgroundGenerator.generate_layers(width, height, seed=seed, palette=palette)
        return BackgroundGenerator.render_frame(
            width, height, layers, frame=(layers["seed"] % 1000)
        )

    @staticmethod
    def generate_layers(width: int, height: int, seed: int = None, palette: list[str] = None) -> dict:
        """
        Generate anchor definitions for a prismatic light-field animation.

        Each anchor is a colored radial gradient that drifts on a Lissajous
        curve.  The caller renders frames by passing these anchors plus a
        frame counter to ``render_frame()``.

        Returns:
            dict with keys:
              anchors   – list of anchor dicts (color, frequencies, phases, radius)
              base_color – dark tinted QColor for the canvas fill
              seed      – echo back for cache comparison
        """
        width = max(1, width)
        height = max(1, height)
        if seed is None:
            seed = random.randint(0, 1_000_000)

        rng = random.Random(seed)

        # --- Resolve palette ---
        if not palette:
            palette = rng.choice(BackgroundGenerator.PRISM_PALETTES)
        q_palette = [QColor(c) if isinstance(c, str) else c for c in palette]

        # --- Base color: dark glass tinted by palette average ---
        avg_r = sum(c.red()   for c in q_palette) // len(q_palette)
        avg_g = sum(c.green() for c in q_palette) // len(q_palette)
        avg_b = sum(c.blue()  for c in q_palette) // len(q_palette)
        base_color = QColor(avg_r, avg_g, avg_b)
        # Darken dramatically: keep hue, reduce saturation + value
        h, s, v, _ = base_color.getHsv()
        base_color = QColor.fromHsv(h, max(0, min(255, int(s * 0.30))), int(255 * 0.12))

        # --- Anchor definitions ---
        num_anchors = rng.randint(4, 5)
        base_freq = rng.uniform(0.002, 0.006)
        anchors = []

        for i in range(num_anchors):
            color = QColor(q_palette[i % len(q_palette)])
            color.setAlpha(rng.randint(90, 130))

            # Frequencies: multiply by golden-ratio powers for irrational ratios
            phi_pow = BackgroundGenerator._PHI ** i
            freq_x = base_freq * phi_pow * rng.uniform(0.8, 1.2)
            freq_y = base_freq * phi_pow * rng.uniform(0.6, 1.0)

            anchors.append({
                "color":      color,
                "freq_x":     freq_x,
                "freq_y":     freq_y,
                "phase_x":    rng.uniform(0, 2 * math.pi),
                "phase_y":    rng.uniform(0, 2 * math.pi),
                "radius":     rng.uniform(0.4, 0.7),       # fraction of max(tiny_w, tiny_h)
                "radius_freq": rng.uniform(0.0008, 0.002),  # very slow breathing
            })

        return {
            "anchors":    anchors,
            "base_color": base_color,
            "seed":       seed,
        }

    @staticmethod
    def render_frame(width: int, height: int, layers: dict, frame: int,
                     tiny_pixmap: QPixmap = None) -> QPixmap:
        """
        Render one frame of the prismatic light field.

        Draws colour gradients onto a tiny canvas with additive blending,
        then upscales — the bilinear interpolation produces the frosted-glass
        blur for free.

        Args:
            width:  Target output width.
            height: Target output height.
            layers: Dict returned by ``generate_layers()``.
            frame:  Animation frame counter.
            tiny_pixmap: Optional pre-allocated tiny QPixmap for reuse
                         (avoids per-frame allocation).
        """
        width = max(1, width)
        height = max(1, height)
        scale = 0.15
        tiny_w = max(20, int(width * scale))
        tiny_h = max(16, int(height * scale))

        if tiny_pixmap is not None:
            tiny = tiny_pixmap
        else:
            tiny = QPixmap(tiny_w, tiny_h)

        tiny.fill(layers["base_color"])

        p = QPainter(tiny)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

        t = frame
        tw, th = tiny.width(), tiny.height()
        max_dim = max(tw, th)

        for anchor in layers["anchors"]:
            # Lissajous position — smooth, organic, bounded to canvas
            cx = 0.5 + 0.4 * math.sin(anchor["freq_x"] * t + anchor["phase_x"])
            cy = 0.5 + 0.4 * math.sin(anchor["freq_y"] * t + anchor["phase_y"])
            # Gentle radius breathing
            r_scale = 1.0 + 0.15 * math.sin(anchor["radius_freq"] * t + anchor["phase_x"])
            px = cx * tw
            py = cy * th
            r = anchor["radius"] * max_dim * r_scale

            grad = QRadialGradient(QPointF(px, py), max(1.0, r))
            grad.setColorAt(0.0, anchor["color"])
            edge = QColor(anchor["color"])
            edge.setAlpha(0)
            grad.setColorAt(1.0, edge)
            p.setBrush(QBrush(grad))
            p.drawEllipse(QPointF(px, py), r, r)

        p.end()

        # Upscale — bilinear interpolation IS the frosted-glass blur
        result = tiny.scaled(
            width, height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        # Subtle frost overlay
        frost = QPainter(result)
        frost.fillRect(0, 0, width, height, QColor(255, 255, 255, 18))
        frost.end()

        return result
