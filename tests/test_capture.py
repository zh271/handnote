import json

import numpy as np
import pytest

from handnote.capture import (
    CaptureError,
    build_capture_font,
    capture_crops,
    normalize,
    rasterize_strokes,
)
from handnote.symbols import build_template, coverage_of, merge_fonts


@pytest.fixture(scope="module")
def spec():
    _pages, template = build_template(cell_mm=12, dpi=150)
    return template


def ink(arr):
    return arr < 128


def test_rasterize_horizontal_stroke():
    arr = rasterize_strokes([([(0.1, 0.5, 1.0), (0.9, 0.5, 1.0)], 0.06)], 128)
    assert arr.dtype == np.uint8
    mask = ink(arr)
    ys, xs = np.where(mask)
    assert xs.min() < 20 and xs.max() > 105
    assert 60 <= ys.mean() <= 68
    assert mask.sum() > 200


def test_rasterize_keeps_empty_cell_white():
    arr = rasterize_strokes([], 96)
    assert arr.min() == 255


def test_rasterize_single_point_is_a_dot():
    arr = rasterize_strokes([([(0.5, 0.5, 1.0)], 0.08)], 96)
    mask = ink(arr)
    assert 0 < mask.sum() < 400


def test_normalize_clamps_and_defaults_pressure():
    grouped = normalize({"width": 0.04, "cells": [
        {"index": 3, "strokes": [[[-0.5, 1.7], [0.5, 0.5]]]},
    ]})
    points, width = grouped[3][0]
    assert points[0][:2] == (0.0, 1.0)
    assert points[0][2] == 1.0
    assert width == 0.04


def test_normalize_accepts_stroke_objects():
    grouped = normalize({"cells": [
        {"index": 1, "strokes": [{"points": [[0.2, 0.2]], "width": 0.09}]},
    ]})
    assert grouped[1][0][1] == 0.09


@pytest.mark.parametrize("payload", [
    {},
    {"cells": []},
    {"cells": [{"index": -1, "strokes": [[[0, 0]]]}]},
    {"cells": [{"index": 1.5, "strokes": [[[0, 0]]]}]},
    {"cells": [{"index": 0, "strokes": [[["a", 0]]]}]},
    {"cells": [{"index": 0, "strokes": [[[0.1, float("nan")]]]}]},
])
def test_normalize_rejects_bad_payloads(payload):
    with pytest.raises(CaptureError):
        normalize(payload)


def test_index_outside_template(spec):
    with pytest.raises(CaptureError):
        capture_crops({"cells": [{"index": len(spec.cells) + 5,
                                  "strokes": [[[0.5, 0.5]]]}]}, spec)


def test_build_capture_font(spec, tmp_path):
    data = {"version": 1, "width": 0.05, "cells": [
        {"index": 0, "strokes": [[[0.2, 0.1], [0.2, 0.9]]]},
        {"index": 1, "strokes": [[[0.1, 0.5], [0.9, 0.5]]]},
    ]}
    report = build_capture_font(data, spec, tmp_path / "capture.ttf")
    assert report["glyphs"] == 2
    assert report["captured"] == 2
    assert report["size"] == 256
    assert report["chars"] == spec.cells[0].char + spec.cells[1].char
    assert len(report["missing"]) == len(spec.cells) - 2


def test_capture_merges_into_base_font(spec, font_path, tmp_path):
    by_char = {cell.char: cell.index for cell in spec.cells}
    data = {"cells": [
        {"index": by_char["∈"], "strokes": [[[0.1, 0.4], [0.9, 0.6]]]},
        {"index": by_char["⊆"], "strokes": [[[0.5, 0.1], [0.5, 0.9]]]},
    ]}
    build_capture_font(data, spec, tmp_path / "capture.ttf")
    merged = merge_fonts(font_path, tmp_path / "capture.ttf", tmp_path / "merged.ttf")
    assert merged["added"] == 2
    assert coverage_of(tmp_path / "merged.ttf", "∈⊆")["missing"] == []


def test_capture_json_round_trip(spec, tmp_path):
    data = {"version": 1, "source": "browser", "width": 0.06, "cells": [
        {"index": 7, "strokes": [[[0.1, 0.2], [0.4, 0.5]], [[0.6, 0.2], [0.8, 0.9]]]},
    ]}
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    again = json.loads(path.read_text(encoding="utf-8"))
    report = build_capture_font(again, spec, tmp_path / "c.ttf")
    assert report["glyphs"] == 1
