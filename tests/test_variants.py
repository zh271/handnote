import random

import numpy as np

from handnote.variants import GlyphSets, SetPicker, make_warp, warp_points


def test_set_zero_and_zero_strength_are_identity():
    assert make_warp(0x7684, 0, 11, 1.0) is None
    assert make_warp(0x7684, 2, 11, 0.0) is None
    pts = np.array([[0.0, 0.0], [500.0, 700.0], [900.0, 100.0]])
    assert np.array_equal(warp_points(pts, None, 1000), pts)


def test_warp_is_deterministic_and_set_specific():
    pts = np.array([[100.0, 0.0], [500.0, 800.0], [900.0, 100.0], [480.0, 400.0]])
    a1 = warp_points(pts, make_warp(65, 1, 11, 1.0), 1000)
    a2 = warp_points(pts, make_warp(65, 1, 11, 1.0), 1000)
    b = warp_points(pts, make_warp(65, 2, 11, 1.0), 1000)
    assert np.array_equal(a1, a2)
    assert not np.allclose(a1, b)
    # A warp nudges the outline; it does not move the glyph away.
    assert np.abs(a1 - pts).max() < 150


def test_picker_never_repeats_a_set_back_to_back():
    picker = SetPicker(3, random.Random(5))
    picks = [picker.pick(0x7684) for _ in range(60)]
    assert all(a != b for a, b in zip(picks, picks[1:], strict=False))
    assert set(picks) == {0, 1, 2}
    assert SetPicker(1, random.Random(5)).pick(1) == 0


def test_glyph_sets_build_distinct_fonts(font_path):
    sets = GlyphSets(font_path, "的a1 ", 3, 1.5, 11)
    assert sets.has("的") and sets.has("a")
    assert not sets.has(" ")
    masks = [np.asarray(sets.font(k, 80).getmask("的")) for k in range(3)]
    assert any(m.shape != masks[0].shape or not np.array_equal(m, masks[0])
               for m in masks[1:])
