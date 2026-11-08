"""Handwritten glyph sets: capture/scan -> alternates -> merge -> renderer."""

from copy import deepcopy

import numpy as np
import pytest
from fontTools.ttLib import TTFont
from matplotlib import ft2font

from handnote.capture import CaptureError, build_capture_font, normalize, normalize_sets
from handnote.config import RenderConfig
from handnote.opentype import add_alternates, read_alternates
from handnote.render import Renderer
from handnote.symbols import Template, build_font, build_template, merge_fonts, simulate_scan
from handnote.variants import GlyphSets, handwritten_sets

STROKES = [
    [[[0.8, 0.2], [0.2, 0.5], [0.8, 0.8]], [[0.2, 0.5], [0.7, 0.5]]],
    [[[0.85, 0.15], [0.15, 0.5], [0.85, 0.85]], [[0.25, 0.48], [0.8, 0.52]]],
    [[[0.75, 0.25], [0.25, 0.55], [0.75, 0.8]], [[0.2, 0.55], [0.65, 0.5]]],
]


@pytest.fixture(scope="module")
def spec():
    return build_template(cell_mm=12, dpi=150)[1]


@pytest.fixture(scope="module")
def merged(spec, font_path, tmp_path_factory):
    """Default font plus three handwritten ∈ and two handwritten ⊆."""
    out = tmp_path_factory.mktemp("sets")
    idx = {cell.char: cell.index for cell in spec.cells}
    cells = [{"index": idx["∈"], "set": k, "strokes": s} for k, s in enumerate(STROKES)]
    cells += [{"index": idx["⊆"], "set": k, "strokes": STROKES[k]} for k in (0, 2)]
    report = build_capture_font({"cells": cells}, spec, out / "cap.ttf")
    result = merge_fonts(font_path, out / "cap.ttf", out / "merged.ttf")
    return report, result, out / "merged.ttf"


def _layout_names(path, text, features=None):
    if not hasattr(ft2font.FT2Font, "_layout"):
        pytest.skip("matplotlib without HarfBuzz layout")
    font = ft2font.FT2Font(str(path))
    font.set_size(48, 72)
    order = TTFont(str(path)).getGlyphOrder()
    items = font._layout(text, ft2font.LoadFlags.NO_HINTING, features=features)
    return [order[item.glyph_index] for item in items]


def test_normalize_sets_groups_by_set():
    data = {"cells": [
        {"index": 1, "strokes": [[[0.1, 0.1], [0.5, 0.5]]]},
        {"index": 1, "set": 2, "strokes": [[[0.2, 0.2], [0.6, 0.6]]]},
    ]}
    sets = normalize_sets(data)
    assert sorted(sets) == [0, 2]
    assert list(normalize(data)) == [1]


@pytest.mark.parametrize("bad", [-1, 8, 1.5, "1", True])
def test_bad_set_number_rejected(bad):
    with pytest.raises(CaptureError):
        normalize_sets({"cells": [{"index": 0, "set": bad, "strokes": [[[0, 0]]]}]})


def test_capture_sets_become_alternates(merged):
    report, result, _path = merged
    assert report["glyphs"] == 2
    assert report["sets"] == 3
    assert report["alternates"] == 3
    assert report["captured_sets"] == 3
    assert result["added"] == 2
    assert result["alternates"] == 3


def test_merged_font_keeps_alternates(merged, font_path):
    _report, _result, path = merged
    font = TTFont(str(path))
    cmap = font.getBestCmap()
    alts = read_alternates(font)
    assert len(alts[cmap[ord("∈")]]) == 2
    assert len(alts[cmap[ord("⊆")]]) == 1
    # Merging only adds: every original code point still maps to its own glyph.
    base = TTFont(str(font_path)).getBestCmap()
    assert all(cmap[code] == name for code, name in base.items())


def test_calt_cycles_and_rand_is_present(merged):
    _report, _result, path = merged
    font = TTFont(str(path))
    base = font.getBestCmap()[ord("∈")]
    alt1, alt2 = read_alternates(font)[base]
    cycled = _layout_names(path, "∈∈∈∈", features=("-rand",))
    assert cycled == [base, alt1, alt2, base]
    plain = _layout_names(path, "∈∈∈∈", features=("-rand", "-calt"))
    assert plain == [base] * 4
    tags = {rec.FeatureTag for rec in font["GSUB"].table.FeatureList.FeatureRecord}
    assert {"rand", "calt"} <= tags


def test_add_alternates_keeps_existing_features(dejavu_path, tmp_path):
    font = TTFont(str(dejavu_path))
    table = font["GSUB"].table
    before = {(rec.FeatureTag, tuple(rec.Feature.LookupListIndex))
              for rec in table.FeatureList.FeatureRecord}
    order = font.getGlyphOrder()
    font["glyf"].glyphs["a.s1"] = deepcopy(font["glyf"]["a"])
    font["hmtx"].metrics["a.s1"] = font["hmtx"]["a"]
    font.setGlyphOrder(order + ["a.s1"])
    font["maxp"].numGlyphs = len(order) + 1
    assert add_alternates(font, {"a": ["a.s1"]}) == 3
    font.save(str(tmp_path / "dv.ttf"))

    again = TTFont(str(tmp_path / "dv.ttf"))
    records = again["GSUB"].table.FeatureList.FeatureRecord
    after = {(rec.FeatureTag, tuple(rec.Feature.LookupListIndex)) for rec in records}
    assert before <= after
    assert [rec.FeatureTag for rec in records] == sorted(rec.FeatureTag for rec in records)
    assert _layout_names(tmp_path / "dv.ttf", "aaa", features=("-rand",)) == ["a", "a.s1", "a"]


def test_template_prints_one_grid_per_set():
    pages, spec = build_template("∈⊆", cell_mm=12, dpi=150, sets=3)
    assert spec.pages == 1 and spec.sets == 3
    assert len(pages) == 3
    assert Template.from_dict(spec.to_dict()).sets == 3


def test_scans_beyond_one_template_are_sets(dejavu_path, tmp_path):
    pages, spec = build_template("∈⊆", cell_mm=20, dpi=150)
    scans = []
    for k in range(2):
        scans += simulate_scan(pages, spec, dejavu_path, [tmp_path / f"s{k}-{i}.jpg"
                                                        for i in range(len(pages))],
                               jitter_px=3.0, seed=k + 3)
    # Natural filename order: s0-0, s1-0 -> set 0 then set 1.
    report = build_font(scans, spec, tmp_path / "scan.ttf")
    assert report["scanned_sets"] == 2
    assert report["sets"] == 2
    font = TTFont(str(tmp_path / "scan.ttf"))
    assert len(read_alternates(font)) == report["glyphs"]


def test_renderer_uses_handwritten_sets(merged, cfg):
    _report, _result, path = merged
    assert handwritten_sets(path, ord("∈")) == 3
    assert handwritten_sets(path, ord("的")) == 0
    sets = GlyphSets(path, "∈⊆的", 4, 1.0, 11)
    assert sets.choices("∈") == 3
    assert sets.choices("⊆") == 2
    assert sets.choices("的") == 4

    renderer = Renderer(cfg.replace(font_path=str(path), glyph_sets=4))
    verts = renderer._math_path(r"$\in\in\in\in$", 100, "k").vertices
    quarter = len(verts) // 4
    shapes = [verts[i * quarter:(i + 1) * quarter] for i in range(4)]
    shapes = [s - s.mean(axis=0) for s in shapes]
    assert any(not np.allclose(shapes[0], s, atol=0.5) for s in shapes[1:])
    pages = renderer.render_markdown("∈∈∈∈ $a \\in b \\in c$")
    assert len(pages) == 1 and renderer.warnings == []


def test_single_set_turns_handwritten_sets_off(merged, cfg):
    _report, _result, path = merged
    one = cfg.replace(font_path=str(path), glyph_sets=1)
    a = Renderer(one).render_markdown("∈∈∈∈")[0]
    b = Renderer(RenderConfig.from_mapping(one.as_dict())).render_markdown("∈∈∈∈")[0]
    assert a.tobytes() == b.tobytes()
    renderer = Renderer(one)
    renderer.render_markdown("∈∈")
    assert renderer._sets is None
