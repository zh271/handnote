"""Handwritten glyphs replacing the base font's own (digits, letters)."""

import pytest
from fontTools.ttLib import TTFont

from handnote.capture import build_capture_font
from handnote.fonts import list_fonts
from handnote.opentype import read_alternates
from handnote.render import Renderer
from handnote.symbols import build_template, merge_fonts

ZERO = [[[0.5, 0.15], [0.25, 0.3], [0.2, 0.6], [0.35, 0.85], [0.6, 0.85],
         [0.78, 0.6], [0.75, 0.3], [0.5, 0.15]]]
ZERO_B = [[[0.5, 0.12], [0.22, 0.35], [0.25, 0.7], [0.45, 0.88], [0.7, 0.8],
           [0.8, 0.5], [0.68, 0.2], [0.5, 0.12]]]
DASH = [[[0.2, 0.55], [0.8, 0.53]]]


@pytest.fixture(scope="module")
def capture_font(tmp_path_factory):
    _pages, spec = build_template(cell_mm=12, dpi=150)
    idx = {cell.char: cell.index for cell in spec.cells}
    data = {"cells": [
        {"index": idx["0"], "set": 0, "strokes": ZERO},
        {"index": idx["0"], "set": 1, "strokes": ZERO_B},
        {"index": idx["-"], "strokes": DASH},
        {"index": idx["∈"], "strokes": DASH},
    ]}
    out = tmp_path_factory.mktemp("replace") / "cap.ttf"
    build_capture_font(data, spec, out)
    return out


def _bounds(font, name):
    glyph = font["glyf"][name]
    glyph.recalcBounds(font["glyf"])
    return glyph.xMin, glyph.yMin, glyph.xMax, glyph.yMax


def test_default_merge_keeps_base_digits(capture_font, font_path, tmp_path):
    result = merge_fonts(font_path, capture_font, tmp_path / "keep.ttf")
    assert result["replaced"] == 0
    assert "0" in result["kept"] and result["chars"] == "∈"
    assert TTFont(str(tmp_path / "keep.ttf")).getBestCmap()[ord("0")] == \
        TTFont(str(font_path)).getBestCmap()[ord("0")]


def test_replace_remaps_only_written_chars(capture_font, font_path, tmp_path):
    result = merge_fonts(font_path, capture_font, tmp_path / "rep.ttf", replace=True)
    assert set(result["replaced_chars"]) == {"0", "-"}
    assert result["chars"] == "∈"
    base = TTFont(str(font_path))
    merged = TTFont(str(tmp_path / "rep.ttf"))
    # post 3.0 drops names, so compare glyph ids: merging only appends glyphs.
    old_ids = {code: base.getGlyphID(name) for code, name in base.getBestCmap().items()}
    new_ids = {code: merged.getGlyphID(name) for code, name in merged.getBestCmap().items()}
    assert new_ids[ord("0")] >= len(base.getGlyphOrder())
    # Everything not handwritten, CJK included, still maps to the base glyph.
    for code, gid in old_ids.items():
        if chr(code) not in "0-":
            assert new_ids[code] == gid
    # The old glyph is still there for composites and GSUB.
    assert len(merged.getGlyphOrder()) > len(base.getGlyphOrder())


def test_replaced_glyph_fits_old_box(capture_font, font_path, tmp_path):
    merge_fonts(font_path, capture_font, tmp_path / "fit.ttf", replace=True)
    base = TTFont(str(font_path))
    merged = TTFont(str(tmp_path / "fit.ttf"))
    for ch in "0-":
        ox0, oy0, ox1, oy1 = _bounds(base, base.getBestCmap()[ord(ch)])
        nx0, ny0, nx1, ny1 = _bounds(merged, merged.getBestCmap()[ord(ch)])
        assert abs((ny0 + ny1) / 2 - (oy0 + oy1) / 2) < 15, ch
        assert abs(nx0 - ox0) < 15, ch
    ox0, oy0, ox1, oy1 = _bounds(base, base.getBestCmap()[ord("0")])
    nx0, ny0, nx1, ny1 = _bounds(merged, merged.getBestCmap()[ord("0")])
    assert abs((ny1 - ny0) - (oy1 - oy0)) < 20


def test_replaced_char_keeps_its_sets(capture_font, font_path, tmp_path):
    result = merge_fonts(font_path, capture_font, tmp_path / "sets.ttf", replace=True)
    assert result["alternates"] == 1
    merged = TTFont(str(tmp_path / "sets.ttf"))
    zero = merged.getBestCmap()[ord("0")]
    assert len(read_alternates(merged)[zero]) == 1


def test_renderer_draws_handwritten_zero(capture_font, font_path, tmp_path, cfg):
    merge_fonts(font_path, capture_font, tmp_path / "draw.ttf", replace=True)
    plain = cfg.replace(paper=False, page_numbers=False, glyph_sets=1)
    base = Renderer(plain).render_markdown("000")[0]
    swapped = Renderer(plain.replace(font_path=str(tmp_path / "draw.ttf"))).render_markdown(
        "000")[0]
    assert base.tobytes() != swapped.tobytes()


def test_list_fonts_shows_merged_but_not_raw(tmp_path):
    (tmp_path / "merged-abc.ttf").write_bytes(b"\0" * 16)
    (tmp_path / "capture-abc.ttf").write_bytes(b"\0" * 16)
    (tmp_path / "notes.txt").write_text("x")
    uploads = [f for f in list_fonts([tmp_path]) if f["source"] == "upload"]
    assert [f["path"] for f in uploads] == [str(tmp_path / "merged-abc.ttf")]
    assert uploads[0]["name"].startswith("手写合并")
