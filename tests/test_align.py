"""Aligning handwritten glyphs to the template guide while keeping some error."""

import pytest
from fontTools.ttLib import TTFont

from handnote.capture import build_capture_font
from handnote.opentype import read_alternates
from handnote.symbols import build_template, guide_box, merge_fonts

# A small "0" written in the top-left corner of its cell.
SMALL_CORNER = [[[0.2, 0.1], [0.1, 0.2], [0.2, 0.3], [0.3, 0.2], [0.2, 0.1]]]
BIG = [[[0.5, 0.05], [0.1, 0.5], [0.5, 0.95], [0.9, 0.5], [0.5, 0.05]]]


@pytest.fixture(scope="module")
def spec():
    return build_template(cell_mm=12, dpi=150)[1]


def _zero(spec, tmp_path, keep, sets=(SMALL_CORNER,)):
    index = next(cell.index for cell in spec.cells if cell.char == "0")
    cells = [{"index": index, "set": k, "strokes": s} for k, s in enumerate(sets)]
    path = tmp_path / f"k{keep}-{len(sets)}.ttf"
    build_capture_font({"width": 0.04, "cells": cells}, spec, path, keep=keep)
    return path


def _box(path, name=None):
    font = TTFont(str(path))
    glyf = font["glyf"]
    name = name or font.getBestCmap()[ord("0")]
    glyph = glyf[name]
    glyph.recalcBounds(glyf)
    return glyph.yMin, glyph.yMax


def test_guide_box_has_baseline():
    x0, y0, x1, y1, base = guide_box("0")
    assert 0 < x0 < x1 < 1 and 0 < y0 < y1 < 1
    assert y0 < base <= 1


def test_keep_zero_snaps_to_guide_size(spec, tmp_path):
    lo, hi = _box(_zero(spec, tmp_path, 0.0))
    _x0, y0, _x1, y1, base = guide_box("0")
    # Cell height maps to 1000 units; guide height measured on the same scale.
    assert abs((hi - lo) - (y1 - y0) * 1000) < 60
    assert abs(hi - (base - y0) * 1000) < 60


def test_keep_blends_between_raw_and_guide(spec, tmp_path):
    snap = _box(_zero(spec, tmp_path, 0.0))
    part = _box(_zero(spec, tmp_path, 0.3))
    raw = _box(_zero(spec, tmp_path, 1.0))
    heights = [hi - lo for lo, hi in (snap, part, raw)]
    assert heights[0] > heights[1] > heights[2]
    # keep=1 is the original behaviour: the glyph's bottom sits at y=60.
    assert abs(raw[0] - 60) < 2


def test_replace_keeps_size_variation_between_sets(spec, font_path, tmp_path):
    cap = _zero(spec, tmp_path, 1.0, sets=(SMALL_CORNER, BIG))
    out = tmp_path / "merged.ttf"
    merge_fonts(font_path, cap, out, replace=True)
    font = TTFont(str(out))
    base = font.getBestCmap()[ord("0")]
    alt = read_alternates(font)[base][0]
    h_base = (lambda b: b[1] - b[0])(_box(out, base))
    h_alt = (lambda b: b[1] - b[0])(_box(out, alt))
    assert h_alt > h_base * 2
