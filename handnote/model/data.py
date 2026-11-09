"""Captures -> what SDT reads: style images and pen sequences (numpy + OpenCV).

Geometry: SDT works in its own normalised units (ink centred on its mean,
scaled by its spread, y down). Captures are cell units ``(u, v)`` in
makemeahanzi's box. :func:`to_sequence` goes cell -> SDT for training;
:mod:`.generate` places generated ink back into a cell.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from ..capture import rasterize_strokes
from ..hanzi_font import normalize_hanzi
from .sdt import normalize_xys

#: Style images: uniform thin lines, ink fitted into 54 px at the top-left of
#: a 64 px square (how SDT's content glyphs are laid out). Pressure-shaded
#: lines read to the pretrained style encoder as fast, cursive writing.
STYLE_WIDTH = 0.022
STYLE_SIZE, STYLE_FIT, STYLE_MARGIN = 64, 54, 5
#: Ramer-Douglas-Peucker tolerance in SDT units; gives ~28 points per
#: character, the density SDT itself writes at.
RDP_EPS = 0.06


@dataclass(frozen=True)
class Written:
    """One handwritten character: strokes as ``[n, 3]`` arrays of (u, v, pressure)."""

    char: str
    set: int
    strokes: tuple[np.ndarray, ...]


def written_chars(capture: dict[str, Any]) -> list[Written]:
    """Every written character, ordered by (char, set) so results never depend
    on the order of the JSON."""
    out = []
    for set_no, group in normalize_hanzi(capture).items():
        for ch, strokes in group.items():
            arrays = tuple(np.array(pts, dtype=np.float64) for pts, _ in strokes
                           if len(pts) >= 2)
            if arrays:
                out.append(Written(ch, set_no, arrays))
    return sorted(out, key=lambda w: (w.char, w.set))


# -- style images ---------------------------------------------------------------
def style_image(strokes: tuple[np.ndarray, ...]) -> np.ndarray:
    """A written character as SDT's style input: 64x64 float, 1 = paper."""
    img = rasterize_strokes([([tuple(p) for p in s], STYLE_WIDTH) for s in strokes], 256,
                            pressure=0.0)
    ys, xs = np.nonzero(img < 200)
    out = np.full((STYLE_SIZE, STYLE_SIZE), 255, np.uint8)
    if len(xs):
        crop = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        h, w = crop.shape
        scale = STYLE_FIT / max(h, w)
        crop = cv2.resize(crop, (max(1, round(w * scale)), max(1, round(h * scale))),
                          interpolation=cv2.INTER_AREA)
        out[STYLE_MARGIN:STYLE_MARGIN + crop.shape[0],
            STYLE_MARGIN:STYLE_MARGIN + crop.shape[1]] = crop
    return out.astype(np.float32) / 255.0


def style_bank(written: list[Written]) -> np.ndarray:
    """All style images, ``[N, 64, 64]``."""
    return np.stack([style_image(w.strokes) for w in written])


def style_subset(count: int, size: int, seed: tuple[int, ...]) -> list[int]:
    """A reproducible choice of ``size`` style images out of ``count``."""
    if count <= size:
        return list(range(count))
    return sorted(random.Random(hash_seed(seed)).sample(range(count), size))


def hash_seed(parts: tuple[int, ...]) -> int:
    """Stable integer seed from a few ints, the same on every platform."""
    value = 0x9E3779B9
    for p in parts:
        value = (value * 1000003) ^ (p & 0xFFFFFFFF)
    return value & 0xFFFFFFFF


# -- sequences ----------------------------------------------------------------------
def rdp(points: np.ndarray, eps: float) -> np.ndarray:
    """Ramer-Douglas-Peucker on an ``[n, 2]`` polyline (endpoints kept)."""
    if len(points) < 3:
        return points
    a, b = points[0], points[-1]
    ab = b - a
    norm = float(np.hypot(*ab))
    rel = points - a
    dist = (np.hypot(rel[:, 0], rel[:, 1]) if norm < 1e-12
            else np.abs(ab[0] * rel[:, 1] - ab[1] * rel[:, 0]) / norm)
    i = int(np.argmax(dist))
    if dist[i] <= eps:
        return np.vstack([a, b])
    return np.vstack([rdp(points[:i + 1], eps)[:-1], rdp(points[i:], eps)])


def _affine(rng: random.Random) -> np.ndarray:
    sx, sy = rng.uniform(0.9, 1.1), rng.uniform(0.9, 1.1)
    shear, theta = rng.uniform(-0.12, 0.12), rng.uniform(-0.05, 0.05)
    rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    return rot @ np.array([[sx, shear], [0, sy]])


def to_sequence(strokes: tuple[np.ndarray, ...], eps: float = RDP_EPS,
                rng: random.Random | None = None) -> np.ndarray:
    """Cell strokes -> SDT training rows ``(dx, dy, down, up, end)``.

    With ``rng``, a small random scale/shear/rotation is applied first (data
    augmentation). Row 0 is the pen-down origin token SDT starts from.
    """
    xy = [s[:, :2] for s in strokes]
    if rng is not None:
        m = _affine(rng)
        xy = [s @ m.T for s in xy]
    rows = []
    for s in xy:
        rows.extend([x, y, 1, 0, 0] for x, y in s)
        rows[-1][2:] = [0, 1, 0]
    norm = normalize_xys(np.array(rows, dtype=np.float64))
    simple, start = [], 0
    for s in xy:
        for x, y in rdp(norm[start:start + len(s), :2], eps):
            simple.append([x, y, 1, 0, 0])
        simple[-1][2:] = [0, 1, 0]
        start += len(s)
    seq = np.array(simple, dtype=np.float32)
    seq[1:, :2] = seq[1:, :2] - seq[:-1, :2]
    seq[0, :2] = 0
    return seq


def from_sequence(seq: np.ndarray) -> list[np.ndarray]:
    """SDT output rows -> strokes of absolute ``[n, 2]`` points (SDT units).

    A one-point stroke is a dot and gets a short tail so it still draws.
    """
    seq = np.vstack([[0, 0, 1, 0, 0], np.asarray(seq, dtype=np.float64)])
    xy = np.cumsum(seq[:, :2], axis=0)
    strokes, cur = [], []
    for point, state in zip(xy, seq[:, 2:], strict=True):
        if state[2] == 1:
            break
        cur.append(point)
        if state[1] == 1:
            strokes.append(cur)
            cur = []
    if cur:
        strokes.append(cur)
    out = []
    for s in strokes:
        arr = np.array(s)
        if len(arr) == 1:
            arr = np.vstack([arr, arr + [0.08, 0.12]])
        out.append(arr)
    return out


def chaikin(points: np.ndarray, rounds: int = 1) -> np.ndarray:
    """Corner-cutting smoothing that keeps both endpoints."""
    for _ in range(rounds):
        if len(points) < 3:
            return points
        a, b = points[:-1], points[1:]
        cut = np.empty((2 * len(a), points.shape[1]))
        cut[0::2] = 0.75 * a + 0.25 * b
        cut[1::2] = 0.25 * a + 0.75 * b
        points = np.vstack([points[:1], cut[1:-1], points[-1:]])
    return points
