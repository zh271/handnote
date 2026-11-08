import json

import numpy as np
import pytest
from PIL import Image

from handnote.symbols import (SYMBOL_GROUPS, Template, all_symbols, build_font,
                              build_template, coverage_of, merge_fonts,
                              simulate_scan)


@pytest.fixture(scope="module")
def template():
    return build_template(cell_mm=12, dpi=150)


def test_every_symbol_has_a_cell(template):
    _pages, spec = template
    chars = "".join(cell.char for cell in spec.cells)
    assert chars == all_symbols()
    assert len(spec.cells) == len(set(chars))


def test_groups_are_unique():
    seen = {}
    for name, group in SYMBOL_GROUPS.items():
        for ch in group:
            assert ch not in seen, f"{ch} duplicated in {name} and {seen.get(ch)}"
            seen[ch] = name


def test_grid_fits_page(template):
    pages, spec = template
    width, height = spec.page_size
    for page in pages:
        assert page.size == (width, height)
    for cell in spec.cells:
        x0, y0, x1, y1 = cell.bbox
        assert 0 <= x0 < x1 <= width
        assert 0 <= y0 < y1 <= height


def test_template_json_round_trip(template, tmp_path):
    _pages, spec = template
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec.to_dict(), ensure_ascii=False), encoding="utf-8")
    again = Template.from_dict(json.loads(path.read_text(encoding="utf-8")))
    assert [c.char for c in again.cells] == [c.char for c in spec.cells]
    assert again.cell_px == spec.cell_px


def test_scan_round_trip(template, dejavu_path, font_path, tmp_path):
    pages, spec = template
    scans = simulate_scan(pages, spec, dejavu_path,
                          [tmp_path / f"scan-{i}.jpg" for i in range(len(pages))])
    report = build_font(scans, spec, tmp_path / "symbols.ttf")
    assert report["glyphs"] == len(spec.cells)
    assert report["missing"] == []

    merged = merge_fonts(font_path, tmp_path / "symbols.ttf", tmp_path / "merged.ttf")
    assert merged["added"] > 90
    missing = coverage_of(tmp_path / "merged.ttf", "∈∪⊆⊊∨√∑∫απ的")
    assert missing["missing"] == []


def test_built_glyph_has_ink(template, dejavu_path, tmp_path):
    pages, spec = template
    scans = simulate_scan(pages, spec, dejavu_path,
                          [tmp_path / f"scan-{i}.jpg" for i in range(len(pages))],
                          jitter_px=0.0)
    build_font(scans, spec, tmp_path / "symbols.ttf")
    from PIL import ImageFont, ImageDraw

    font = ImageFont.truetype(str(tmp_path / "symbols.ttf"), 160)
    canvas = Image.new("L", (200, 200), 255)
    ImageDraw.Draw(canvas).text((100, 160), "∈", font=font, fill=0, anchor="ls")
    ink = np.asarray(canvas) < 128
    assert ink.sum() > 200, "traced glyph should contain a visible stroke"


def test_empty_cells_are_reported(tmp_path, template):
    _pages, spec = template
    blank = Image.new("L", spec.page_size, 255)
    paths = []
    for i in range(spec.pages):
        path = tmp_path / f"blank-{i}.png"
        blank.save(path)
        paths.append(path)
    report = build_font(paths, spec, tmp_path / "empty.ttf")
    assert report["glyphs"] == 0
    assert len(report["missing"]) == len(spec.cells)
