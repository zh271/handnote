"""Browser stroke capture: schema validation, rasterisation, font building.

Capture payload::

    {"version": 1, "source": "browser",
     "cells": [{"index": 12, "set": 0, "strokes": [[[u, v, pressure], ...]]}]}

Coordinates are normalised to ``[0, 1]`` inside one template cell, origin at
the top-left corner, ``v`` pointing down. Each cell is rasterised at
``size`` pixels and handed to the same contour tracer used for scans.

``set`` (optional, default 0) numbers handwritten sets of the same
template; the same ``index`` may appear once per set.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from .symbols import DEFAULT_KEEP, MAX_SETS, Template, assemble_font

DEFAULT_WIDTH = 0.05
MIN_SIZE = 64
MAX_SIZE = 1024
MAX_CELLS = 8192
MAX_STROKES_PER_CELL = 64
MAX_POINTS_PER_STROKE = 4000


class CaptureError(ValueError):
    """Raised when a capture payload is malformed."""


Stroke = tuple[list[tuple[float, float, float]], float]


def normalize(data: dict[str, Any]) -> dict[int, list[Stroke]]:
    """Validate a payload and group the first set's strokes by cell index.

    Returns ``{cell_index: [(points, width), ...]}`` with points as
    ``(u, v, pressure)`` tuples clamped to the cell box. See
    :func:`normalize_sets` for every set.
    """
    sets = normalize_sets(data)
    return sets[min(sets)]


def normalize_sets(data: dict[str, Any]) -> dict[int, dict[int, list[Stroke]]]:
    """Validate a payload and group strokes as ``{set: {cell_index: strokes}}``."""
    if not isinstance(data, dict):
        raise CaptureError("capture must be an object")
    cells = data.get("cells")
    if cells is None and "strokes" in data:
        cells = [data]
    if not isinstance(cells, list) or not cells:
        raise CaptureError("capture needs a non-empty 'cells' list")
    if len(cells) > MAX_CELLS:
        raise CaptureError(f"too many cells (max {MAX_CELLS})")

    default_width = float(data.get("width") or DEFAULT_WIDTH)
    default_width = min(max(default_width, 0.01), 0.2)
    grouped: dict[int, dict[int, list[Stroke]]] = {}
    for entry in cells:
        if not isinstance(entry, dict):
            raise CaptureError("each cell must be an object")
        index = entry.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise CaptureError(f"bad cell index: {index!r}")
        set_no = entry.get("set", 0)
        if (not isinstance(set_no, int) or isinstance(set_no, bool)
                or not 0 <= set_no < MAX_SETS):
            raise CaptureError(f"cell {index}: bad set {set_no!r} (0..{MAX_SETS - 1})")
        strokes = entry.get("strokes") or []
        if not isinstance(strokes, list):
            raise CaptureError(f"cell {index}: 'strokes' must be a list")
        if len(strokes) > MAX_STROKES_PER_CELL:
            raise CaptureError(f"cell {index}: too many strokes")
        parsed = []
        for stroke in strokes:
            points, width = _parse_stroke(stroke, index, default_width)
            if points:
                parsed.append((points, width))
        if parsed:
            grouped.setdefault(set_no, {}).setdefault(index, []).extend(parsed)
    if not grouped:
        raise CaptureError("capture contains no strokes")
    return grouped


def _parse_stroke(
    stroke: Any, index: int, default_width: float = DEFAULT_WIDTH
) -> tuple[list[tuple[float, float, float]], float]:
    width = default_width
    if isinstance(stroke, dict):
        points = stroke.get("points") or stroke.get("pts")
        if stroke.get("width") is not None:
            width = float(stroke["width"])
    else:
        points = stroke
    if not isinstance(points, list) or not points:
        return [], width
    if len(points) > MAX_POINTS_PER_STROKE:
        raise CaptureError(f"cell {index}: stroke has too many points")
    width = min(max(width, 0.01), 0.2)
    out: list[tuple[float, float, float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) not in (2, 3):
            raise CaptureError(f"cell {index}: bad point {point!r}")
        try:
            u = float(point[0])
            v = float(point[1])
            pressure = float(point[2]) if len(point) == 3 else 1.0
        except (TypeError, ValueError) as exc:
            raise CaptureError(f"cell {index}: bad point {point!r}") from exc
        if not (math.isfinite(u) and math.isfinite(v) and math.isfinite(pressure)):
            raise CaptureError(f"cell {index}: non-finite point")
        out.append((min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0),
                    min(max(pressure, 0.0), 1.0)))
    return out, width


def rasterize_strokes(
    strokes: Sequence[tuple[list[tuple[float, float, float]], float]],
    size: int = 256,
    *,
    default_width: float = DEFAULT_WIDTH,
) -> np.ndarray:
    """Draw strokes into an ``size`` square uint8 array (0 ink, 255 paper)."""
    size = int(min(max(size, MIN_SIZE), MAX_SIZE))
    image = Image.new("L", (size, size), 255)
    draw = ImageDraw.Draw(image)
    for points, width in strokes:
        pressure = sum(p for _, _, p in points) / len(points)
        scale = 0.7 + 0.6 * pressure
        px_width = max(1, int(round(min(max(width, 0.01), 0.2) * size * scale)))
        coords = [(u * size, v * size) for u, v, _ in points]
        if len(coords) == 1:
            x, y = coords[0]
            radius = px_width / 2
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=0)
            continue
        draw.line(coords, fill=0, width=px_width, joint="curve")
        radius = px_width / 2
        for x, y in (coords[0], coords[-1]):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=0)
    return np.asarray(image)


def capture_crops(
    data: dict[str, Any], template: Template | dict, size: int = 256
) -> dict[int, np.ndarray]:
    """Rasterise the first captured set, keyed by cell index."""
    return capture_set_crops(data, template, size)[0]


def capture_set_crops(
    data: dict[str, Any], template: Template | dict, size: int = 256
) -> list[dict[int, np.ndarray]]:
    """Rasterise every captured set, in set order (gaps in numbering close up)."""
    spec = template if isinstance(template, Template) else Template.from_dict(template)
    limit = len(spec.cells)
    out: list[dict[int, np.ndarray]] = []
    for _set_no, grouped in sorted(normalize_sets(data).items()):
        crops: dict[int, np.ndarray] = {}
        for index, strokes in grouped.items():
            if index >= limit:
                raise CaptureError(f"cell index {index} outside template (0..{limit - 1})")
            crops[index] = rasterize_strokes(strokes, size)
        out.append(crops)
    return out


def build_capture_font(
    data: dict[str, Any],
    template: Template | dict,
    out_path: str | Path,
    *,
    size: int = 256,
    upem: int = 1000,
    family: str = "HandNote Symbols",
    keep: float = DEFAULT_KEEP,
) -> dict:
    """Turn a capture payload into a TTF; returns the build report.

    ``keep`` is the share of the writer's size/position error kept after
    aligning each glyph to the template guide (1 = raw strokes).
    """
    spec = template if isinstance(template, Template) else Template.from_dict(template)
    sets = capture_set_crops(data, spec, size)
    report = assemble_font(sets[0], spec, out_path, upem=upem, family=family,
                           extra_sets=sets[1:], keep=keep)
    report["captured"] = len(set().union(*sets))
    report["captured_sets"] = len(sets)
    report["size"] = size
    return report
