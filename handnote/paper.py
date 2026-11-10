"""Paper backgrounds: ruled presets, scanned sheets and the row grid text snaps to.

Every preset is a set of :class:`~handnote.config.RenderConfig` fields. The
row grid (:func:`grid`) is shared by the rules drawn on the page and by the
layout, so text sits on the lines: rule ``k`` is at ``first + k * pitch`` and
the baseline of row ``k`` is ``drop`` above it.

A scanned sheet goes through :func:`detect_page` (outer corners),
:func:`flatten_scan` (perspective warp to the page size and shadow removal)
and :func:`detect_rules` (pitch and position of printed lines, so the layout
can follow them).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

import cv2
import matplotlib
import numpy as np
from PIL import Image

from .config import _PAPER_STYLES as STYLES  # noqa: F401 - re-exported
from .config import RenderConfig

#: Styles whose rows are square cells (text can also snap to them horizontally).
CELL_STYLES = ("essay", "tianzi")
MARGIN_RULE_RGB = (222, 120, 120)

#: Paper fields a preset sets. Applying a preset resets all of them.
PAPER_FIELDS: dict[str, Any] = {
    "paper_style": "plain",
    "paper_color": "",
    "rule_color": "#7fa3cf",
    "rule_alpha": 150,
    "rule_width": 1.3,
    "rule_pitch": 0.0,
    "rule_top": 0.0,
    "cell_size": 0.0,
    "margin_rule": False,
    "paper_snap": True,
    "cell_snap": True,
    "grid_cols": 0,
    "grid_rows": 0,
    "count_marks": True,
    "paper_image": "",
}

#: ``key -> (label, overrides)``; overrides apply on top of ``PAPER_FIELDS``.
PRESETS: dict[str, tuple[str, dict[str, Any]]] = {
    "plain": ("白纸", {}),
    "lined": ("横线本", {"paper_style": "lined"}),
    "lined_margin": ("红边线横线本", {"paper_style": "lined", "margin_rule": True,
                                  "rule_color": "#6f95c8"}),
    "eyecare": ("护眼黄横线", {"paper_style": "lined", "paper_color": "#f5eed6",
                          "rule_color": "#b39b6b", "rule_alpha": 140}),
    "grid": ("方格本", {"paper_style": "grid", "rule_color": "#8db4d8",
                     "rule_alpha": 120, "rule_width": 1.0}),
    "dots": ("点阵本", {"paper_style": "dots", "rule_color": "#7d7d86",
                     "rule_alpha": 170, "rule_width": 1.4}),
    "essay": ("400字稿纸", {"paper_style": "essay", "rule_color": "#4f9a64",
                         "rule_alpha": 190, "rule_width": 1.2, "grid_cols": 20,
                         "grid_rows": 20}),
    "essay800": ("800字稿纸", {"paper_style": "essay", "rule_color": "#c0504d",
                            "rule_alpha": 180, "rule_width": 1.1, "grid_cols": 25,
                            "grid_rows": 32}),
    "tianzi": ("田字格", {"paper_style": "tianzi", "rule_color": "#cf6464",
                       "rule_alpha": 180, "rule_width": 1.2}),
    "kraft": ("牛皮纸", {"paper_style": "lined", "paper_color": "#e6d3ad",
                      "rule_color": "#8a6a43", "rule_alpha": 130}),
}

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def preset(name: str) -> dict[str, Any]:
    """All paper fields for preset ``name``."""
    if name not in PRESETS:
        raise ValueError(f"unknown paper preset {name!r}; choose from {', '.join(PRESETS)}")
    return {**PAPER_FIELDS, **PRESETS[name][1]}


def presets() -> list[dict[str, Any]]:
    """Presets for the web UI: ``[{"key", "label", "config"}]``."""
    return [{"key": key, "label": label, "config": preset(key)}
            for key, (label, _) in PRESETS.items()]


def hex_rgb(value: str) -> tuple[int, int, int]:
    m = _HEX_RE.match(value)
    if not m:
        raise ValueError(f"expected a #rrggbb colour, got {value!r}")
    h = m.group(1)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def paper_rgb(cfg: RenderConfig) -> tuple[int, int, int]:
    """Base paper colour: ``paper_color``, or the ``paper_base`` grey."""
    if cfg.paper_color:
        return hex_rgb(cfg.paper_color)
    return (cfg.paper_base,) * 3


# -- row grid ---------------------------------------------------------------
@dataclass(frozen=True)
class Grid:
    """Where text rows, cells and printed lines are on a page.

    ``pitch == 0`` means free layout (no rows). ``top`` is the top of the
    first text row and ``bottom`` the lowest a row may reach; a row is
    ``pitch`` tall and its baseline sits ``baseline`` below its top, ``drop``
    above its rule. ``cell`` is the square of the essay styles (and of the
    grid style when text aligns to it), ``cols`` how many fit a row starting
    at ``x0``. ``unit`` is the spacing of the grid and dot styles.
    ``count`` is ``(cols, rows)`` per page for counted essay paper.
    """

    style: str
    pitch: float = 0.0
    first: float = 0.0
    top: float = 0.0
    bottom: float = 0.0
    baseline: float = 0.0
    drop: float = 0.0
    cell: float = 0.0
    cols: int = 0
    x0: float = 0.0
    unit: float = 0.0
    snap: bool = False
    cell_snap: bool = False
    count: tuple[int, int] | None = None

    @property
    def align(self) -> float:
        """Cell width characters snap to horizontally (0 for free flow)."""
        return self.cell if self.cell_snap else 0.0

    def rule(self, k: int) -> float:
        """y of rule ``k`` (row ``k`` counted from ``first``)."""
        return self.first + k * self.pitch

    def rows(self) -> list[int]:
        """Indices of the rows between ``top`` and ``bottom``."""
        if self.pitch <= 0:
            return []
        k0 = round((self.top + self.baseline + self.drop - self.first) / self.pitch)
        out = []
        k = k0
        while self.rule(k) - self.drop - self.baseline + self.pitch <= self.bottom + 0.5:
            out.append(k)
            k += 1
        return out


#: Classic manuscript paper: label -> (columns, rows) per page.
ESSAY_COUNTS: dict[str, tuple[int, int]] = {
    "300": (20, 15),
    "400": (20, 20),
    "500": (25, 20),
    "600": (20, 30),
    "800": (25, 32),
}


def _counted(cfg: RenderConfig) -> tuple[float, float, float] | None:
    """``(pitch, cell, x0)`` when essay paper has a fixed number of cells."""
    if cfg.paper_style not in CELL_STYLES or cfg.grid_cols <= 0 or cfg.grid_rows <= 0:
        return None
    width = cfg.page_width - 2 * cfg.margin_x
    pitch = (cfg.page_height - cfg.margin_top - cfg.margin_bottom) / cfg.grid_rows
    cell = min(width / cfg.grid_cols, pitch * 0.82)
    return pitch, cell, cfg.margin_x + (width - cfg.grid_cols * cell) / 2


def fit_body(cfg: RenderConfig) -> int | None:
    """Body size that fills a counted cell, or ``None`` when cells are free-sized."""
    counted = _counted(cfg)
    return None if counted is None else max(8, round(counted[1] * 0.72))


def _cell_grid_unit(cfg: RenderConfig) -> bool:
    """Grid paper whose squares hold one character each."""
    return cfg.paper_style == "grid" and cfg.cell_snap


def grid(cfg: RenderConfig) -> Grid:
    """Row grid for ``cfg`` (free layout for plain paper)."""
    style = cfg.paper_style
    body = cfg.body_size
    bottom = float(cfg.page_height - cfg.margin_bottom)
    if style == "plain" or (style == "image" and (cfg.rule_pitch <= 0 or cfg.rule_top <= 0)):
        return Grid(style, top=float(cfg.margin_top), bottom=bottom, x0=float(cfg.margin_x))
    baseline = 1.28 * body
    counted = _counted(cfg)
    cell = 0.0
    if counted is not None:
        pitch, cell, x0 = counted
        drop = cell / 2 - 0.38 * body
        # Cells centred in their rows: the first rule is the first cell's bottom.
        first = cfg.margin_top + (pitch + cell) / 2
    else:
        pitch = float(cfg.rule_pitch or round(body * cfg.line_height))
        x0 = float(cfg.margin_x)
        if style in CELL_STYLES or _cell_grid_unit(cfg):
            cell = float(cfg.cell_size or round(body * 1.3))
            # Grid squares hold a character each: rows sit on whole squares.
            pitch = (cell * max(1, round(pitch / cell)) if style == "grid"
                     else max(pitch, cell))
            drop = cell / 2 - 0.38 * body
        else:
            drop = 0.2 * body
        first = float(cfg.rule_top or cfg.margin_top + baseline + drop)
    # First row whose top is at or below the top margin (half a row of slack).
    origin = first - drop - baseline
    k0 = math.ceil((cfg.margin_top - 0.5 * pitch - origin) / pitch)
    top = origin + k0 * pitch
    if top < 0:
        top += math.ceil(-top / pitch) * pitch
    count = None
    if counted is not None:
        count = (int(cfg.grid_cols), int(cfg.grid_rows))
        bottom = top + cfg.grid_rows * pitch
        cols = int(cfg.grid_cols)
    else:
        cols = int((cfg.page_width - 2 * cfg.margin_x) // cell) if cell else 0
    if style == "grid":
        unit = cell if cell else float(cfg.cell_size or pitch / 2)
    else:
        unit = float(cfg.cell_size or pitch / 2) if style == "dots" else 0.0
    return Grid(style, pitch, first, top, bottom, baseline, drop, cell, cols, x0, unit,
                snap=bool(cfg.paper_snap), cell_snap=bool(cfg.cell_snap and cell), count=count)


# -- rules ------------------------------------------------------------------
class Rule(NamedTuple):
    """One printed line, dot or label, in page pixels.

    ``kind`` is ``"h"`` (``pos`` = y, ``start``/``end`` = x), ``"v"``
    (``pos`` = x, ``start``/``end`` = y), ``"dot"`` (centre ``(pos, start)``,
    ``width`` = diameter) or ``"text"`` (``label`` at x ``pos``, baseline
    ``start``, font size ``width``; ``end`` is 0 for left and 1 for right
    alignment). ``dash`` is ``(on, off)`` or ``None``.
    """

    kind: str
    pos: float
    start: float
    end: float
    width: float
    rgb: tuple[int, int, int]
    alpha: int
    dash: tuple[float, float] | None = None
    label: str = ""


def rules(cfg: RenderConfig, g: Grid, page: int = 1) -> list[Rule]:
    """Lines, dots and labels printed on page ``page`` (1-based).

    Nothing is printed on plain or scanned paper. Counted essay paper marks
    every hundredth cell (counting on across pages) and prints its size,
    e.g. ``20×20=400``, under the grid.
    """
    style = g.style
    if style in ("plain", "image") or g.pitch <= 0:
        return []
    w, h = cfg.page_width, cfg.page_height
    rgb = hex_rgb(cfg.rule_color)
    a = int(cfg.rule_alpha)
    lw = float(cfg.rule_width)
    rows = g.rows()
    out: list[Rule] = []
    border = max(8.0, round(min(cfg.margin_x, cfg.margin_top) * 0.4))
    if style == "lined":
        for k in rows:
            out.append(Rule("h", g.rule(k), border, w - border, lw, rgb, a))
    elif style in ("grid", "dots"):
        unit = g.unit
        x0 = g.x0 - math.floor((g.x0 - border) / unit) * unit
        anchor = g.rule(rows[0]) if rows else g.first
        y0 = anchor - math.floor((anchor - border) / unit) * unit
        xs = list(np.arange(x0, w - border + 1e-6, unit))
        ys = list(np.arange(y0, h - border + 1e-6, unit))
        if style == "grid":
            out += [Rule("h", y, xs[0], xs[-1], lw, rgb, a) for y in ys]
            out += [Rule("v", x, ys[0], ys[-1], lw, rgb, a) for x in xs]
        else:
            out += [Rule("dot", x, y, y, lw * 1.6, rgb, a) for y in ys for x in xs]
    else:  # essay / tianzi
        c = g.cell
        x0, x1 = g.x0, g.x0 + g.cols * c
        seen: set[float] = set()
        for k in rows:
            bottom = g.rule(k)
            top = bottom - c
            for y in (top, bottom):
                key = round(y, 1)
                if key not in seen:
                    seen.add(key)
                    out.append(Rule("h", y, x0, x1, lw, rgb, a))
            out += [Rule("v", x0 + i * c, top, bottom, lw, rgb, a) for i in range(g.cols + 1)]
            if style == "tianzi":
                dash = (max(2.0, c / 14), max(2.0, c / 14))
                thin = max(0.5, lw * 0.7)
                out.append(Rule("h", bottom - c / 2, x0, x1, thin, rgb, a, dash))
                out += [Rule("v", x0 + (i + 0.5) * c, top, bottom, thin, rgb, a, dash)
                        for i in range(g.cols)]
        if g.count is not None and cfg.count_marks and rows:
            out += _count_marks(g, rows, page, x1, rgb, a)
    if cfg.margin_rule and style != "dots":
        x = g.x0 - 0.45 * cfg.body_size
        out.append(Rule("v", x, 0.0, float(h), lw * 1.1, MARGIN_RULE_RGB, max(a, 170)))
    return out


def _count_marks(g: Grid, rows: list[int], page: int, right: float,
                 rgb: tuple[int, int, int], a: int) -> list[Rule]:
    """Running cell counts beside the rows where a hundred is completed."""
    cols, per_page = g.count
    size = max(8.0, min(14.0, g.cell * 0.3))
    alpha = max(a, 200)
    out = []
    for r, k in enumerate(rows):
        done = ((page - 1) * per_page + r + 1) * cols
        if done // 100 > (done - cols) // 100:
            label = str(done // 100 * 100)
            out.append(Rule("text", right + size * 0.35, g.rule(k) - 1, 0.0, size, rgb,
                            alpha, label=label))
    total = cols * per_page
    out.append(Rule("text", g.x0, g.rule(rows[-1]) + size * 1.6, 0.0, size, rgb, alpha,
                    label=f"{cols}×{per_page}={total}"))
    return out


_LABEL_FONT = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"


@lru_cache(maxsize=256)
def label_path(label: str, size: float) -> tuple[np.ndarray, np.ndarray, float]:
    """Outline of a printed label: ``(verts, codes, advance)``, y down, origin on the baseline."""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath

    path = TextPath((0, 0), label, size=size, prop=FontProperties(fname=str(_LABEL_FONT)))
    verts = np.array(path.vertices, dtype=float)
    verts[:, 1] *= -1
    advance = float(path.get_extents().x1) if len(verts) else 0.0
    return verts, np.array(path.codes, dtype=np.uint8), advance


@lru_cache(maxsize=8)
def _label_font(size: int):
    from PIL import ImageFont

    return ImageFont.truetype(str(_LABEL_FONT), size)


def _paint_label(field: np.ndarray, r: Rule) -> None:
    from PIL import ImageDraw

    _, _, advance = label_path(r.label, r.width)
    x = r.pos - advance * r.end
    font = _label_font(max(1, round(r.width)))
    pad = int(r.width) + 4
    w = int(advance) + 2 * pad
    mask = Image.new("L", (w, 2 * pad + int(r.width)), 0)
    ImageDraw.Draw(mask).text((pad, pad + r.width), r.label, font=font, fill=255, anchor="ls")
    x0, y0 = int(round(x)) - pad, int(round(r.start - r.width)) - pad
    m = np.asarray(mask, dtype=np.float32) / 255.0 * (r.alpha / 255.0)
    h, wf = field.shape[:2]
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(wf, x0 + m.shape[1]), min(h, y0 + m.shape[0])
    if sx1 <= sx0 or sy1 <= sy0:
        return
    m = m[sy0 - y0: sy1 - y0, sx0 - x0: sx1 - x0, None]
    region = field[sy0:sy1, sx0:sx1]
    region *= 1 - m
    region += _paint_color(r, field) * m


def _paint_color(r: Rule, field: np.ndarray) -> np.ndarray:
    """``r``'s colour for ``field``: RGB, or premultiplied RGBA with full alpha."""
    rgb = tuple(r.rgb) + ((255,) if field.shape[2] == 4 else ())
    return np.asarray(rgb, dtype=np.float32)


def _band(center: float, width: float, n: int) -> tuple[int, np.ndarray]:
    """First pixel index and per-pixel coverage of ``[center ± width/2]``."""
    lo, hi = center - width / 2, center + width / 2
    i0, i1 = max(0, math.floor(lo)), min(n, math.ceil(hi))
    if i1 <= i0:
        return i0, np.zeros(0, dtype=np.float32)
    idx = np.arange(i0, i1, dtype=np.float32)
    cov = np.clip(np.minimum(idx + 1, hi) - np.maximum(idx, lo), 0.0, 1.0)
    return i0, cov.astype(np.float32)


def _along(start: float, end: float, n: int, dash: tuple[float, float] | None
           ) -> tuple[int, np.ndarray]:
    i0, cov = _band((start + end) / 2, end - start, n)
    if dash is not None and len(cov):
        on, off = dash
        pos = np.arange(i0, i0 + len(cov), dtype=np.float32) + 0.5 - start
        cov = cov * ((pos % (on + off)) < on)
    return i0, cov


def paint_rules(field: np.ndarray, items: list[Rule]) -> None:
    """Blend ``items`` into a float array, anti-aliased.

    ``field`` is ``(h, w, 3)`` RGB over opaque paper, or ``(h, w, 4)``
    premultiplied RGBA over a transparent page (see :func:`rgba_image`).
    """
    h, w = field.shape[:2]
    for r in items:
        color = _paint_color(r, field)
        a = r.alpha / 255.0
        if r.kind == "text":
            _paint_label(field, r)
            continue
        if r.kind == "dot":
            rad = r.width / 2
            x0, x1 = max(0, math.floor(r.pos - rad)), min(w, math.ceil(r.pos + rad))
            y0, y1 = max(0, math.floor(r.start - rad)), min(h, math.ceil(r.start + rad))
            if x1 <= x0 or y1 <= y0:
                continue
            yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
            dist = np.hypot(xx + 0.5 - r.pos, yy + 0.5 - r.start)
            m = np.clip(rad + 0.5 - dist, 0.0, 1.0)[..., None] * a
            region = field[y0:y1, x0:x1]
            region *= 1 - m
            region += color * m
            continue
        if r.kind == "h":
            r0, across = _band(r.pos, r.width, h)
            c0, along = _along(r.start, r.end, w, r.dash)
            m = across[:, None] * along[None, :]
            sl = (slice(r0, r0 + len(across)), slice(c0, c0 + len(along)))
        else:
            c0, across = _band(r.pos, r.width, w)
            r0, along = _along(r.start, r.end, h, r.dash)
            m = along[:, None] * across[None, :]
            sl = (slice(r0, r0 + len(along)), slice(c0, c0 + len(across)))
        if not m.size:
            continue
        m = m[..., None] * a
        region = field[sl]
        region *= 1 - m
        region += color * m


def rgba_image(field: np.ndarray) -> Image.Image:
    """Premultiplied ``(h, w, 4)`` float RGBA (0-255) -> a straight-alpha RGBA image."""
    alpha = np.clip(field[..., 3:], 0.0, 255.0)
    rgb = np.where(alpha > 0, field[..., :3] * 255.0 / np.maximum(alpha, 1e-6), 0.0)
    out = np.concatenate([np.clip(rgb, 0, 255), alpha], axis=2)
    return Image.fromarray(np.rint(out).astype(np.uint8), "RGBA")


def has_fill(cfg: RenderConfig) -> bool:
    """Whether the page is filled with paper (``paper_color`` or grey, with grain).

    Otherwise the page is transparent and only the rules, cells and dots are
    drawn, so printing on real paper adds no tint.
    """
    return cfg.paper


# -- scanned paper ----------------------------------------------------------
@lru_cache(maxsize=4)
def _background(path: str, mtime: float, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as img:
        img = img.convert("RGB")
        if img.size != size:
            img = img.resize(size, Image.Resampling.LANCZOS)
        return img


def background(cfg: RenderConfig) -> Image.Image | None:
    """The scanned sheet at page size, or ``None`` (not set or unreadable)."""
    if cfg.paper_style != "image" or not cfg.paper_image:
        return None
    path = Path(cfg.paper_image)
    if not path.is_file():
        return None
    return _background(str(path), path.stat().st_mtime, (cfg.page_width, cfg.page_height))


def _order(quad: np.ndarray) -> np.ndarray:
    """Corners as top-left, top-right, bottom-right, bottom-left."""
    quad = quad.reshape(4, 2).astype(np.float32)
    s, d = quad.sum(axis=1), np.diff(quad, axis=1).ravel()
    return np.array([quad[s.argmin()], quad[d.argmin()], quad[s.argmax()], quad[d.argmax()]],
                    dtype=np.float32)


def detect_page(img: Image.Image) -> list[list[float]]:
    """Corners of the sheet in a photo or scan (the whole image if none found).

    Returns ``[[x, y], ...]`` clockwise from the top-left, in image pixels.
    """
    rgb = np.asarray(img.convert("RGB"))
    h, w = rgb.shape[:2]
    full = [[0.0, 0.0], [w - 1.0, 0.0], [w - 1.0, h - 1.0], [0.0, h - 1.0]]
    scale = min(1.0, 900.0 / max(h, w))
    small = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) \
        if scale < 1.0 else rgb
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area = small.shape[0] * small.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:3]:
        if cv2.contourArea(contour) < 0.2 * area:
            break
        hull = cv2.convexHull(contour)
        quad = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
        if len(quad) == 4:
            if cv2.contourArea(quad) > 0.97 * area:
                break  # the "sheet" is the whole frame
            return (_order(quad) / scale).astype(float).round(1).tolist()
    return full


def flatten_scan(img: Image.Image, corners: list[list[float]], size: tuple[int, int], *,
                 evenness: float = 0.8, whiten: float = 0.6,
                 gray: bool = False) -> Image.Image:
    """Warp the sheet inside ``corners`` to ``size`` and even out its lighting.

    ``evenness`` blends between the warped photo (0) and a version with
    shadows and uneven light divided out (1); the average paper colour is
    kept either way. ``whiten`` (0-1) lifts that paper colour towards white
    without changing its tint (photos come out grey). ``gray`` drops the colour.
    """
    if len(corners) != 4:
        raise ValueError("expected four corners")
    src = _order(np.asarray(corners, dtype=np.float32))
    w, h = size
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)
    rgb = np.asarray(img.convert("RGB"))
    warped = cv2.warpPerspective(rgb, matrix, (w, h), flags=cv2.INTER_AREA,
                                 borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
    evenness = min(max(float(evenness), 0.0), 1.0)
    if evenness > 0:
        k = max(15, (min(w, h) // 30) | 1)
        # Closing removes the printed lines before the light field is estimated.
        bg = cv2.morphologyEx(warped, cv2.MORPH_CLOSE, np.ones((k // 3 | 1, k // 3 | 1), np.uint8))
        bg = cv2.GaussianBlur(bg, (0, 0), k)
        bg = np.maximum(bg, 1.0)
        level = np.median(bg.reshape(-1, 3), axis=0)
        flat = warped / bg * level
        warped = warped * (1 - evenness) + flat * evenness
    whiten = min(max(float(whiten), 0.0), 1.0)
    if whiten > 0:
        level = np.maximum(np.median(warped.reshape(-1, 3), axis=0), 1.0)
        target = level + (level * (250.0 / level.max()) - level) * whiten
        warped = warped * (target / level)
    out = np.clip(warped, 0, 255).astype(np.uint8)
    if gray:
        out = np.repeat(cv2.cvtColor(out, cv2.COLOR_RGB2GRAY)[..., None], 3, axis=2)
    return Image.fromarray(out)


def _peak(curve: np.ndarray, i: int) -> float:
    """Sub-pixel position of the local maximum at ``i`` (parabola through 3 samples)."""
    if i <= 0 or i >= len(curve) - 1:
        return float(i)
    a, b, c = curve[i - 1], curve[i], curve[i + 1]
    den = a - 2 * b + c
    return float(i + (0.5 * (a - c) / den if den else 0.0))


def detect_rules(img: Image.Image, *, min_pitch: float = 12.0,
                 max_pitch: float = 260.0) -> dict[str, float] | None:
    """Pitch and first position of evenly spaced horizontal lines on a sheet.

    Looks for a periodic darkening in the row profile of the middle columns.
    Returns ``{"pitch", "top", "count", "score"}`` with ``top`` the y of the
    first line, or ``None`` when nothing regular is found.
    """
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    h, w = gray.shape
    profile = gray[:, int(w * 0.2): int(w * 0.8)].mean(axis=1)
    # Darkness against the local paper level (a 1-D closing removes the thin
    # dark lines), so shading and gradients do not count.
    win = max(3, int(min_pitch * 0.8)) | 1
    col = profile.reshape(-1, 1)
    kernel = np.ones((win, 1), np.uint8)
    base = cv2.erode(cv2.dilate(col, kernel), kernel).ravel()
    dark = np.clip(base - profile, 0, None)
    if dark.max() <= 0.5:
        return None
    centred = dark - dark.mean()
    lo, hi = int(min_pitch), int(min(max_pitch, h / 3))
    if hi <= lo:
        return None
    ac = np.correlate(centred, centred, mode="full")[h - 1:]
    if ac[0] <= 0:
        return None
    ac = ac / ac[0]
    lag = lo + int(np.argmax(ac[lo:hi]))
    # Prefer the fundamental: a strong peak at half the lag means lag is a multiple.
    while lag // 2 >= lo and ac[lag // 2 - 1: lag // 2 + 2].max() > 0.6 * ac[lag]:
        half = lag // 2
        lag = half - 1 + int(np.argmax(ac[half - 1: half + 2]))
    score = float(ac[lag])
    if score < 0.15:
        return None
    # Sharpen the pitch with later multiples of the lag (errors shrink by 1/m).
    pitch = _peak(ac, lag)
    m = 2
    while m * pitch + 3 < len(ac) and m * pitch < h * 0.6:
        c = int(round(m * pitch))
        j = c - 2 + int(np.argmax(ac[c - 2: c + 3]))
        if ac[j] < 0.1:
            break
        pitch = _peak(ac, j) / m
        m += 1
    # Phase: where a comb with this pitch collects the most darkness.
    ys = np.arange(h, dtype=np.float32)
    best, phase = -1.0, 0.0
    for off in np.arange(0.0, pitch, 0.25):
        pos = np.arange(off, h - 1, pitch)
        total = float(np.interp(pos, ys, dark).sum())
        if total > best:
            best, phase = total, float(off)
    # Lines: comb teeth that land on a dark peak clearly above the paper
    # between the lines (faint or slightly tilted lines still count).
    between = np.interp(np.arange(phase + pitch / 2, h - 1, pitch), ys, dark)
    noise = float(np.percentile(between, 75)) if len(between) else 0.0
    thresh = max(0.6, 3.0 * noise + 0.3)
    idx, pos_list = [], []
    for k, y in enumerate(np.arange(phase, h - 1, pitch)):
        i = int(round(y))
        lo_i, hi_i = max(1, i - 2), min(h - 2, i + 2)
        j = lo_i + int(np.argmax(dark[lo_i: hi_i + 1]))
        if dark[j] >= thresh:
            idx.append(k)
            pos_list.append(_peak(dark, j))
    if len(idx) < 3:
        return None
    pitch_fit, top = np.polyfit(np.asarray(idx, float), np.asarray(pos_list, float), 1)
    if not (min_pitch <= pitch_fit <= max_pitch):
        return None
    k_min = min(idx)
    return {"pitch": round(float(pitch_fit), 2),
            "top": round(float(top + k_min * pitch_fit), 2),
            "count": len(idx), "score": round(score, 3)}
