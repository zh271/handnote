"""Pen pressure for generated strokes (numpy only, no torch).

SDT writes centre lines without pressure. When the writer's capture has real
pressure, a small ridge regression learns how it varies along a stroke;
otherwise a generic rule shades the stroke.

The learned model uses geometry only, because generated strokes carry no
timing: where the point sits along the stroke (start, middle, end), how
sharply the stroke turns there, the stroke's overall direction and length.
Both the writer's strokes and generated ones are first resampled to the
same arc-length step, so the features mean the same thing on both sides.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

#: Resampling step in cell units (the cell is 1 x 1).
STEP = 0.012
#: Knots of the hat functions over the stroke position t in [0, 1].
KNOTS = (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)
RIDGE = 1.0
#: A capture counts as having pressure when it varies at least this much.
MIN_STD = 0.02


def resample(points: np.ndarray, step: float = STEP) -> np.ndarray:
    """Evenly spaced points along the polyline; extra columns are interpolated."""
    points = np.asarray(points, dtype=np.float64)
    if len(points) < 2:
        return points
    seg = np.hypot(*np.diff(points[:, :2], axis=0).T)
    dist = np.concatenate([[0.0], np.cumsum(seg)])
    total = dist[-1]
    if total <= 0:
        return points[[0, -1]]
    n = max(2, int(math.ceil(total / step)) + 1)
    at = np.linspace(0, total, n)
    return np.stack([np.interp(at, dist, points[:, c]) for c in range(points.shape[1])], 1)


def features(points: np.ndarray) -> np.ndarray:
    """Per-point feature rows for one resampled stroke (x, y in the first columns)."""
    xy = points[:, :2]
    n = len(xy)
    t = np.linspace(0.0, 1.0, n)
    hats = []
    for i, k in enumerate(KNOTS):
        left = KNOTS[i - 1] if i else None
        right = KNOTS[i + 1] if i + 1 < len(KNOTS) else None
        h = np.zeros(n)
        if left is not None:
            m = (t >= left) & (t <= k)
            h[m] = (t[m] - left) / (k - left)
        if right is not None:
            m = (t >= k) & (t <= right)
            h[m] = (right - t[m]) / (right - k)
        hats.append(h)
    d = np.diff(xy, axis=0)
    ang = np.arctan2(d[:, 1], d[:, 0])
    turn = np.zeros(n)
    if n > 2:
        turn[1:-1] = np.abs((np.diff(ang) + np.pi) % (2 * np.pi) - np.pi) / np.pi
    overall = xy[-1] - xy[0]
    octant = int(round(math.atan2(overall[1], overall[0]) / (math.pi / 4))) % 8
    direction = np.zeros((n, 8))
    direction[:, octant] = 1.0
    length = float(np.hypot(*d.T).sum()) if n > 1 else 0.0
    cols = [*hats, turn, np.full(n, length), np.full(n, length ** 2)]
    return np.column_stack([*cols, direction])


@dataclass
class PressureModel:
    """Ridge-regression coefficients over :func:`features`."""

    coef: list[float]
    mean: float

    def predict(self, points: np.ndarray) -> np.ndarray:
        """Pressure for each point of an already resampled stroke."""
        p = features(points) @ np.asarray(self.coef)
        return _smooth(np.clip(p, 0.0, 1.0))

    def to_json(self) -> dict[str, Any]:
        return {"kind": "ridge", "coef": [round(c, 6) for c in self.coef],
                "mean": round(self.mean, 6)}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PressureModel:
        return cls([float(c) for c in data["coef"]], float(data["mean"]))


def fit(strokes: list[np.ndarray]) -> PressureModel | None:
    """Learn pressure from written strokes (``[n, 3]`` u, v, p); ``None`` if
    the capture carries no usable pressure."""
    rows, target = [], []
    for s in strokes:
        if s.shape[1] < 3 or len(s) < 2:
            continue
        r = resample(s)
        rows.append(features(r))
        target.append(r[:, 2])
    if not rows:
        return None
    x = np.vstack(rows)
    y = np.concatenate(target)
    if float(np.std(y)) < MIN_STD:
        return None
    coef = np.linalg.solve(x.T @ x + RIDGE * np.eye(x.shape[1]), x.T @ y)
    return PressureModel(coef.tolist(), float(np.mean(y)))


def fallback(points: np.ndarray, raw_spacing: np.ndarray | None = None) -> np.ndarray:
    """Generic shading: lighter where the pen moved fast, tapered at both ends.

    ``raw_spacing`` is the distance between the generator's own points around
    each resampled point (long straight runs are written fast); the mapping
    follows pen.js: ``clamp(0.8 - speed * k, 0.2, 0.8)``.
    """
    n = len(points)
    t = np.linspace(0.0, 1.0, n)
    if raw_spacing is not None and len(raw_spacing) == n:
        rel = raw_spacing / max(float(np.median(raw_spacing)), 1e-9)
        base = np.clip(0.8 - 0.3 * rel, 0.2, 0.8)
    else:
        base = np.full(n, 0.6)
    taper = np.minimum(1.0, 0.75 + 2.5 * t) * np.minimum(1.0, 0.45 + 3.0 * (1 - t))
    return _smooth(np.clip(base * taper, 0.0, 1.0))


def _smooth(p: np.ndarray) -> np.ndarray:
    if len(p) < 3:
        return p
    out = p.copy()
    out[1:-1] = (p[:-2] + p[1:-1] + p[2:]) / 3
    return out
