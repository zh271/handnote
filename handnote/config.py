"""Renderer configuration shared by the CLI and the web UI."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Any

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")

#: Bounds used by :meth:`RenderConfig.validate`. ``key`` -> ``(low, high)``.
_LIMITS: dict[str, tuple[float, float]] = {
    "page_width": (240, 5000),
    "page_height": (240, 8000),
    "margin_x": (0, 800),
    "margin_top": (0, 800),
    "margin_bottom": (0, 800),
    "body_size": (8, 120),
    "line_height": (1.05, 3.0),
    "h1_size": (8, 160),
    "h2_size": (8, 160),
    "h3_size": (8, 160),
    "ink_alpha_min": (0, 255),
    "ink_alpha_max": (0, 255),
    "jitter_rot": (0.0, 12.0),
    "jitter_dx": (0.0, 0.3),
    "jitter_dy": (0.0, 0.3),
    "size_wobble": (0.0, 6.0),
    "math_scale": (0.6, 2.0),
    "display_size": (8, 140),
    "paper_base": (0, 255),
    "paper_noise": (0.0, 40.0),
    "paper_gradient": (0.0, 60.0),
    "seed": (0, 2**31 - 1),
    "dpi": (60, 600),
    "matrix_gap": (0.0, 3.0),
    "glyph_sets": (1, 8),
    "glyph_strength": (0.0, 3.0),
}


@dataclass(slots=True)
class RenderConfig:
    """Every knob the renderer reads.

    Values are plain JSON scalars so the same object round-trips through the
    web API without conversion.
    """

    font_path: str = ""
    page_width: int = 1240
    page_height: int = 1754
    margin_x: int = 92
    margin_top: int = 96
    margin_bottom: int = 96
    body_size: int = 25
    line_height: float = 1.82
    h1_size: int = 36
    h2_size: int = 29
    h3_size: int = 26
    ink: str = "#1e1f2c"
    ink_alpha_min: int = 238
    ink_alpha_max: int = 255
    jitter_rot: float = 0.9
    jitter_dx: float = 0.03
    jitter_dy: float = 0.035
    size_wobble: float = 1.0
    math_scale: float = 1.12
    display_size: int = 36
    paper: bool = True
    paper_base: int = 250
    paper_noise: float = 1.6
    paper_gradient: float = 3.0
    page_numbers: bool = True
    matrix_gap: float = 0.9
    glyph_sets: int = 4
    glyph_strength: float = 1.0
    seed: int = 11
    dpi: int = 150

    # -- construction -----------------------------------------------------
    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> RenderConfig:
        """Build a config from a partial dict, ignoring unknown keys."""
        cfg = cls()
        if not data:
            return cfg
        known = {f.name: f.type for f in fields(cls)}
        for key, value in data.items():
            if key not in known or value is None:
                continue
            setattr(cfg, key, _coerce(key, value))
        cfg.validate()
        return cfg

    def replace(self, **changes: Any) -> RenderConfig:
        """Return a copy with ``changes`` applied and validated."""
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data.update(changes)
        return RenderConfig(**data)

    def validate(self) -> RenderConfig:
        """Clamp numeric fields and check colour/alpha consistency."""
        for name, (low, high) in _LIMITS.items():
            value = getattr(self, name)
            if value < low or value > high:
                setattr(self, name, min(max(value, low), high))
        if self.ink_alpha_min > self.ink_alpha_max:
            self.ink_alpha_min, self.ink_alpha_max = self.ink_alpha_max, self.ink_alpha_min
        if not _HEX_RE.match(self.ink):
            raise ValueError(f"ink must be a #rrggbb colour, got {self.ink!r}")
        if not self.font_path:
            raise ValueError("font_path is required")
        if self.page_width - 2 * self.margin_x < 120:
            raise ValueError("page is too narrow for the given margins")
        if self.page_height - self.margin_top - self.margin_bottom < 120:
            raise ValueError("page is too short for the given margins")
        return self

    # -- derived values ---------------------------------------------------
    @property
    def ink_rgb(self) -> tuple[int, int, int]:
        """Ink colour as an ``(r, g, b)`` tuple."""
        m = _HEX_RE.match(self.ink)
        assert m is not None
        h = m.group(1)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def as_dict(self) -> dict[str, Any]:
        """Serialise back to JSON-friendly scalars."""
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _coerce(key: str, value: Any) -> Any:
    if key in ("font_path", "ink"):
        return str(value)
    if key == "paper" or key == "page_numbers":
        if isinstance(value, str):
            return value.strip().lower() not in ("", "0", "false", "no", "off")
        return bool(value)
    if isinstance(value, bool):
        return int(value)
    if key in ("page_width", "page_height", "margin_x", "margin_top", "margin_bottom",
               "body_size", "h1_size", "h2_size", "h3_size", "ink_alpha_min",
               "ink_alpha_max", "display_size", "paper_base", "seed", "dpi", "glyph_sets"):
        return int(round(float(value)))
    return float(value)
