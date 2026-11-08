"""Glyph sets: deterministic outline warps so repeated characters differ.

Set 0 is the font's own outline. Set ``k > 0`` applies a small, smooth warp
to every glyph: a random affine (scale, shear, rotation about the glyph
centre) followed by two low-frequency sine displacements. Parameters are
drawn from ``(seed, codepoint, k)``, so the same character in the same set
always has the same shape, in body text and in formulas alike.

Handwritten sets win: when the font carries OpenType ``rand`` alternates
for a character (see :mod:`handnote.opentype`), set 0 is its cmap glyph and
set ``k`` is its ``k``-th alternate, unwarped; the character then has
``1 + len(alternates)`` sets regardless of ``glyph_sets``.

Two consumers share :func:`warp_points`:

* body text and matrix cells go through :class:`GlyphSets`, which builds one
  tiny TrueType font per set (only the characters the page needs) so PIL and
  FreeType rasterise warped glyphs exactly like the original ones;
* formulas warp the matplotlib glyph paths directly (see ``render.py``).
"""

from __future__ import annotations

import io
import math
import random
from collections import OrderedDict
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.basePen import BasePen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from matplotlib.path import Path as MplPath
from PIL import ImageFont

from .opentype import read_alternates

MAX_SETS = 8


@dataclass(frozen=True)
class Warp:
    """One glyph's deformation, in em-normalised units."""

    matrix: tuple[float, float, float, float]
    waves: tuple[tuple[float, float, float, float, float], ...]


def make_warp(code: int, k: int, seed: int, strength: float) -> Warp | None:
    """Warp for character ``code`` in set ``k``; ``None`` for the original."""
    if k <= 0 or strength <= 0:
        return None
    rng = np.random.default_rng([seed & 0xFFFFFFFF, code & 0xFFFFFFFF, k, 0x5E7])
    s = float(strength)
    angle = math.radians(rng.normal(0.0, 1.8) * s)
    shear = rng.normal(0.0, 0.05) * s
    sx = 1.0 + rng.normal(0.0, 0.045) * s
    sy = 1.0 + rng.normal(0.0, 0.035) * s
    cos, sin = math.cos(angle), math.sin(angle)
    # rotation @ [[sx, shear], [0, sy]]
    matrix = (cos * sx, cos * shear - sin * sy, sin * sx, sin * shear + cos * sy)
    waves = []
    for _ in range(2):
        direction = rng.uniform(0.0, 2 * math.pi)
        push = rng.uniform(0.0, 2 * math.pi)
        amp = rng.normal(0.0, 0.022) * s
        freq = rng.uniform(0.55, 1.5)
        waves.append((
            amp * math.cos(push),
            amp * math.sin(push),
            freq * math.cos(direction),
            freq * math.sin(direction),
            rng.uniform(0.0, 2 * math.pi),
        ))
    return Warp(matrix, tuple(waves))


def warp_points(points: np.ndarray, warp: Warp | None, em: float,
                center: tuple[float, float] | None = None) -> np.ndarray:
    """Apply ``warp`` to an ``(N, 2)`` array of y-up points in units of ``em``."""
    pts = np.asarray(points, dtype=np.float64)
    if warp is None or len(pts) == 0:
        return pts.copy()
    if center is None:
        lo = pts.min(axis=0)
        hi = pts.max(axis=0)
        center = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)
    c = np.array(center, dtype=np.float64)
    rel = (pts - c) / em
    a, b, cc, d = warp.matrix
    out = np.empty_like(rel)
    out[:, 0] = a * rel[:, 0] + b * rel[:, 1]
    out[:, 1] = cc * rel[:, 0] + d * rel[:, 1]
    for ax, ay, fx, fy, phase in warp.waves:
        wave = np.sin(2 * math.pi * (fx * rel[:, 0] + fy * rel[:, 1]) + phase)
        out[:, 0] += ax * wave
        out[:, 1] += ay * wave
    return out * em + c


class SetPicker:
    """Picks a set per occurrence, never the same set twice in a row per char."""

    def __init__(self, sets: int, rng: random.Random):
        self.sets = max(1, int(sets))
        self.rng = rng
        self._last: dict[int, int] = {}

    def pick(self, code: int, sets: int | None = None) -> int:
        """Choose a set for one occurrence; ``sets`` overrides the default count."""
        count = self.sets if sets is None else max(1, int(sets))
        if count <= 1:
            return 0
        last = self._last.get(code)
        choices = [k for k in range(count) if k != last]
        k = choices[self.rng.randrange(len(choices))]
        self._last[code] = k
        return k


# -- body text: per-set mini fonts -------------------------------------------

_SOURCES: OrderedDict[str, _Source] = OrderedDict()
_GLYPHS: OrderedDict[tuple, tuple] = OrderedDict()
_GLYPH_LIMIT = 20000


class _Source:
    """Lazy outline access for one font file."""

    def __init__(self, path: Path):
        self.font = TTFont(str(path), lazy=True, fontNumber=0)
        self.glyphs = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap() or {}
        self.upem = int(self.font["head"].unitsPerEm)
        self.is_ttf = "glyf" in self.font
        hhea = self.font["hhea"]
        self.ascent = int(hhea.ascent)
        self.descent = int(hhea.descent)
        by_name = read_alternates(self.font)
        self.alternates: dict[int, list[str]] = {
            code: by_name[name] for code, name in self.cmap.items() if name in by_name
        }

    def glyph_name(self, code: int, k: int) -> str | None:
        """Glyph for set ``k`` of a handwritten character, else ``None``."""
        alts = self.alternates.get(code)
        if not alts or k > len(alts):
            return None
        return self.cmap[code] if k == 0 else alts[k - 1]


def handwritten_sets(font_path: str | Path, code: int) -> int:
    """Number of handwritten sets the font has for ``code`` (0 when none)."""
    alts = _source(Path(font_path)).alternates.get(code)
    return 1 + len(alts) if alts else 0


class _MplPen(BasePen):
    """Collect a glyph outline as matplotlib path vertices and codes."""

    def __init__(self, glyph_set, scale: float):
        super().__init__(glyph_set)
        self.scale = scale
        self.verts: list[tuple[float, float]] = []
        self.codes: list[int] = []

    def _add(self, point, code: int) -> None:
        self.verts.append((point[0] * self.scale, point[1] * self.scale))
        self.codes.append(code)

    def _moveTo(self, pt):  # noqa: N802 - fontTools pen API
        self._add(pt, MplPath.MOVETO)

    def _lineTo(self, pt):  # noqa: N802
        self._add(pt, MplPath.LINETO)

    def _qCurveToOne(self, pt1, pt2):  # noqa: N802
        self._add(pt1, MplPath.CURVE3)
        self._add(pt2, MplPath.CURVE3)

    def _curveToOne(self, pt1, pt2, pt3):  # noqa: N802
        self._add(pt1, MplPath.CURVE4)
        self._add(pt2, MplPath.CURVE4)
        self._add(pt3, MplPath.CURVE4)

    def _closePath(self):  # noqa: N802
        self._add((0.0, 0.0), MplPath.CLOSEPOLY)

    _endPath = _closePath


def handwritten_outline(font_path: str | Path, code: int, k: int, em: float
                        ) -> tuple[np.ndarray, np.ndarray] | None:
    """Matplotlib ``(verts, codes)`` of handwritten set ``k``, scaled to ``em``."""
    src = _source(Path(font_path))
    name = src.glyph_name(code, k)
    if name is None:
        return None
    pen = _MplPen(src.glyphs, em / src.upem)
    src.glyphs[name].draw(pen)
    return (np.array(pen.verts, dtype=float).reshape(-1, 2),
            np.array(pen.codes, dtype=MplPath.code_type))


def _source(path: Path) -> _Source:
    key = f"{path}:{path.stat().st_mtime_ns}"
    src = _SOURCES.get(key)
    if src is None:
        src = _Source(path)
        _SOURCES[key] = src
        while len(_SOURCES) > 4:
            _SOURCES.popitem(last=False)
    return src


def _replay(value: list, points: np.ndarray, pen) -> None:
    """Replay a recording onto ``pen`` with its points replaced by ``points``."""
    i = 0
    for op, args in value:
        new_args = []
        for arg in args:
            if arg is None:
                new_args.append(None)
            else:
                new_args.append((float(points[i, 0]), float(points[i, 1])))
                i += 1
        getattr(pen, op)(*new_args)


def _warped_glyph(src: _Source, src_key: str, code: int, k: int, seed: int,
                  strength: float) -> tuple[object, int, int] | None:
    """Return ``(glyph, advance, lsb)`` for ``code`` in set ``k``."""
    key = (src_key, code, k, seed, round(strength, 4))
    hit = _GLYPHS.get(key)
    if hit is not None:
        _GLYPHS.move_to_end(key)
        return hit
    if code in src.alternates:
        name = src.glyph_name(code, k)
        warp = None
    else:
        name = src.cmap.get(code)
        warp = make_warp(code, k, seed, strength)
    if name is None:
        return None
    recorder = DecomposingRecordingPen(src.glyphs)
    src.glyphs[name].draw(recorder)
    advance = int(src.glyphs[name].width)
    flat = [arg for _, args in recorder.value for arg in args if arg is not None]
    tt_pen = TTGlyphPen(None)
    if flat:
        pts = warp_points(np.array(flat, dtype=np.float64), warp, src.upem)
        pen = tt_pen if src.is_ttf else Cu2QuPen(tt_pen, src.upem / 1000,
                                                 reverse_direction=True)
        _replay(recorder.value, pts, pen)
        lsb = int(round(float(pts[:, 0].min())))
    else:
        lsb = 0
    result = (tt_pen.glyph(), advance, lsb)
    _GLYPHS[key] = result
    while len(_GLYPHS) > _GLYPH_LIMIT:
        _GLYPHS.popitem(last=False)
    return result


def _mini_font(src: _Source, entries: dict[int, tuple[object, int, int]]) -> bytes:
    names = {code: f"g{code:X}" for code in entries}
    fb = FontBuilder(src.upem, isTTF=True)
    fb.setupGlyphOrder([".notdef"] + list(names.values()))
    fb.setupCharacterMap({code: names[code] for code in entries})
    glyphs = {".notdef": TTGlyphPen(None).glyph()}
    metrics = {".notdef": (src.upem // 2, 0)}
    for code, (glyph, advance, lsb) in entries.items():
        glyphs[names[code]] = deepcopy(glyph)
        metrics[names[code]] = (advance, lsb)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=src.ascent, descent=src.descent)
    fb.setupNameTable({"familyName": "HandNote Set", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=src.ascent, sTypoDescender=src.descent,
                usWinAscent=max(0, src.ascent), usWinDescent=max(0, -src.descent))
    fb.setupPost()
    buffer = io.BytesIO()
    fb.save(buffer)
    return buffer.getvalue()


class GlyphSets:
    """Per-set mini fonts for a fixed character set of one source font."""

    def __init__(self, font_path: str | Path, chars: Iterable[str], sets: int,
                 strength: float, seed: int):
        path = Path(font_path)
        self.sets = int(min(max(sets, 1), MAX_SETS))
        src = _source(path)
        src_key = f"{path}:{path.stat().st_mtime_ns}"
        wanted = {ord(ch) for ch in chars if not ch.isspace()}
        codes = sorted(wanted & set(src.cmap))
        self.codes = frozenset(codes)
        self.absent = frozenset(wanted - self.codes)
        self._choices = {code: (1 + len(src.alternates[code]) if code in src.alternates
                                else self.sets) for code in codes}
        slots = max([self.sets, *self._choices.values()])
        self._data: list[bytes] = []
        for k in range(slots):
            entries = {}
            for code in codes:
                if k >= self._choices[code]:
                    continue
                built = _warped_glyph(src, src_key, code, k, seed, strength)
                if built is not None:
                    entries[code] = built
            self._data.append(_mini_font(src, entries))
        self._fonts: dict[tuple[int, int], ImageFont.FreeTypeFont] = {}

    def has(self, ch: str) -> bool:
        return len(ch) == 1 and ord(ch) in self.codes

    def choices(self, ch: str) -> int:
        """How many sets ``ch`` has: its handwritten count, else ``sets``."""
        return self._choices.get(ord(ch), 1)

    def font(self, k: int, px: int) -> ImageFont.FreeTypeFont:
        key = (k, px)
        font = self._fonts.get(key)
        if font is None:
            font = ImageFont.truetype(io.BytesIO(self._data[k]), px)
            self._fonts[key] = font
        return font
