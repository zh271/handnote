"""Stroke weight: grow or thin glyph outlines by a fixed distance.

Handwritten symbols and the font's hanzi often differ in stroke weight.
:func:`offset_points` moves every contour along its normal (like FreeType's
emboldening, without changing the advance), and :func:`weight_offset` picks
the amount per character: ``hanzi_weight`` for wide characters (hanzi,
full-width punctuation), ``symbol_weight`` for everything else.

Two consumers share it, both before the glyph-set warp:

* body text, through the per-set mini fonts of :class:`handnote.variants.GlyphSets`;
* formulas, through ``Renderer._math_glyph`` (see ``render.py``).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

import numpy as np

# matplotlib path codes, kept local to avoid importing matplotlib here.
_MOVETO, _CLOSEPOLY = 1, 79

#: Corners sharper than this (cosine of the turn) are not moved, as in FreeType.
_SHARP = -0.9375


def is_wide(code: int) -> bool:
    """True for hanzi and other full-width characters (the ``hanzi_weight`` class)."""
    return unicodedata.east_asian_width(chr(code)) in ("W", "F")


def weight_offset(code: int, weight: tuple[float, float], em: float) -> float:
    """Per-side outline offset for ``code``, in units where one em is ``em``.

    ``weight`` is ``(hanzi, symbol)`` in percent of the em: how much thicker
    (positive) or thinner (negative) every stroke gets in total.
    """
    pct = weight[0] if is_wide(code) else weight[1]
    return pct / 200.0 * em


def _area(points: np.ndarray) -> float:
    x, y = points[:, 0], points[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _offset_contour(pts: np.ndarray, delta: float) -> np.ndarray:
    """Move one closed contour by ``delta`` along its right-hand normal.

    Each point moves along the bisector of its two edges, so straight edges
    shift by exactly ``delta``; repeated points move with their neighbour,
    near-reversals stay put and long spikes are capped at the shorter edge.
    """
    n = len(pts)
    # Representative index of every point among the distinct, cyclic points.
    rep = np.zeros(n, dtype=int)
    uniq = [0]
    for i in range(1, n):
        if np.any(pts[i] != pts[uniq[-1]]):
            uniq.append(i)
        rep[i] = len(uniq) - 1
    while len(uniq) > 1 and np.all(pts[uniq[-1]] == pts[0]):
        rep[rep == len(uniq) - 1] = 0
        uniq.pop()
    if len(uniq) < 3:
        return pts.copy()
    u = pts[uniq]
    d_in = u - np.roll(u, 1, axis=0)
    d_out = np.roll(u, -1, axis=0) - u
    l_in = np.hypot(d_in[:, 0], d_in[:, 1])
    l_out = np.hypot(d_out[:, 0], d_out[:, 1])
    e_in = d_in / l_in[:, None]
    e_out = d_out / l_out[:, None]
    cos = np.einsum("ij,ij->i", e_in, e_out)
    normal = np.stack([e_in[:, 1] + e_out[:, 1], -(e_in[:, 0] + e_out[:, 0])], axis=1)
    sharp = cos <= _SHARP
    scale = np.where(sharp, 0.0, delta / np.where(sharp, 1.0, 1.0 + cos))
    shift = normal * scale[:, None]
    length = np.hypot(shift[:, 0], shift[:, 1])
    cap = np.minimum(l_in, l_out)
    over = length > cap
    shift[over] *= (cap[over] / length[over])[:, None]
    return pts + shift[rep]


def offset_points(points: np.ndarray, lengths: Iterable[int], delta: float) -> np.ndarray:
    """Grow (``delta > 0``) or thin every stroke of a glyph by ``delta`` per side.

    ``points`` are y-up outline points (control points included) and
    ``lengths`` the number of points in each closed contour, in order. Outer
    contours and holes are told apart by the glyph's overall winding, so this
    works for TrueType and PostScript outlines alike.
    """
    pts = np.asarray(points, dtype=np.float64)
    if delta == 0 or len(pts) == 0:
        return pts.copy()
    bounds = np.cumsum([0, *lengths])
    contours = [pts[a:b] for a, b in zip(bounds[:-1], bounds[1:], strict=True) if b > a]
    area = sum(_area(c) for c in contours if len(c) > 2)
    # Filled side on the left of a counter-clockwise (PostScript) outer contour.
    sign = 1.0 if area >= 0 else -1.0
    out = pts.copy()
    for a, b in zip(bounds[:-1], bounds[1:], strict=True):
        if b - a > 2:
            out[a:b] = _offset_contour(pts[a:b], sign * delta)
    return out


def offset_path(verts: np.ndarray, codes: np.ndarray, delta: float) -> np.ndarray:
    """:func:`offset_points` for a matplotlib path; ``CLOSEPOLY`` vertices are kept."""
    verts = np.asarray(verts, dtype=np.float64)
    if delta == 0 or len(verts) == 0:
        return verts.copy()
    codes = np.asarray(codes)
    keep = np.flatnonzero(codes != _CLOSEPOLY)
    starts = np.flatnonzero(codes[keep] == _MOVETO)
    if len(starts) == 0 or starts[0] != 0:
        starts = np.concatenate([[0], starts])
    lengths = np.diff(np.concatenate([starts, [len(keep)]]))
    out = verts.copy()
    out[keep] = offset_points(verts[keep], lengths.tolist(), delta)
    return out


def contour_lengths(value: list) -> list[int]:
    """Point count per contour of a fontTools recording (``RecordingPen.value``)."""
    lengths: list[int] = []
    count = 0
    for op, args in value:
        if op == "moveTo" and count:
            lengths.append(count)
            count = 0
        count += sum(arg is not None for arg in args)
        if op in ("closePath", "endPath"):
            lengths.append(count)
            count = 0
    if count:
        lengths.append(count)
    return lengths
