"""Stroke weight: outline offsets for hanzi and for digits/letters/symbols."""

import numpy as np
from matplotlib.path import Path as MplPath

from handnote.config import RenderConfig
from handnote.markdown import parse
from handnote.render import Renderer
from handnote.variants import is_wide, offset_path, offset_points, weight_offset

SQUARE_CCW = np.array([(0, 0), (10, 0), (10, 10), (0, 10)], dtype=float)


def _area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _ink(image) -> float:
    return float(np.asarray(image.getchannel("A"), dtype=float).sum()) / 255


def test_offset_grows_both_windings():
    for square in (SQUARE_CCW, SQUARE_CCW[::-1]):
        out = offset_points(square, [4], 1.0)
        assert abs(abs(_area(out)) - 144.0) < 1e-6  # 12 x 12
        thin = offset_points(square, [4], -1.0)
        assert abs(abs(_area(thin)) - 64.0) < 1e-6


def test_offset_shrinks_holes():
    hole = (SQUARE_CCW * 0.4 + 3)[::-1]  # opposite winding: a counter
    pts = np.concatenate([SQUARE_CCW, hole])
    out = offset_points(pts, [4, 4], 0.5)
    assert abs(_area(out[:4])) > 100
    assert abs(_area(out[4:])) < abs(_area(hole))


def test_offset_path_keeps_closepoly_and_repeated_points():
    verts = np.array([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0), (0, 0)], dtype=float)
    codes = np.array([MplPath.MOVETO] + [MplPath.LINETO] * 4 + [MplPath.CLOSEPOLY])
    out = offset_path(verts, codes, 1.0)
    assert np.allclose(out[0], (-1, -1))  # counter-clockwise outer contour grows
    assert np.allclose(out[0], out[4])  # the closing point moves with the start
    assert np.allclose(out[-1], (0, 0))  # CLOSEPOLY's dummy vertex is untouched
    assert abs(abs(_area(out[:4])) - 144.0) < 1e-6


def test_weight_classes():
    assert is_wide(ord("字")) and is_wide(ord("，"))
    assert not is_wide(ord("7")) and not is_wide(ord("x")) and not is_wide(ord("≤"))
    assert weight_offset(ord("字"), (4.0, -2.0), 1000) == 20.0
    assert weight_offset(ord("7"), (4.0, -2.0), 1000) == -10.0


def test_weights_are_clamped(font_path):
    cfg = RenderConfig.from_mapping({"font_path": str(font_path),
                                     "hanzi_weight": 10, "symbol_weight": "-9"})
    assert cfg.weight == (6.0, -3.0)


def test_symbol_weight_changes_formula_ink(cfg):
    doc = parse("$$\\frac{x^2+1}{y-3}$$")
    inks = [_ink(Renderer(cfg.replace(symbol_weight=w)).render(doc)[0]) for w in (-3, 0, 4)]
    assert inks[0] < inks[1] < inks[2]


def test_classes_are_independent(cfg):
    one = cfg.replace(glyph_sets=1, page_numbers=False)  # page numbers are digits
    hanzi = parse("汉字书写")
    digits = parse("2024 abc")
    plain_h = Renderer(one).render(hanzi)[0]
    plain_d = Renderer(one).render(digits)[0]
    heavy_symbols = Renderer(one.replace(symbol_weight=4))
    assert heavy_symbols.render(hanzi)[0].tobytes() == plain_h.tobytes()
    assert _ink(heavy_symbols.render(digits)[0]) > _ink(plain_d) * 1.05
    heavy_hanzi = Renderer(one.replace(hanzi_weight=4))
    assert heavy_hanzi.render(digits)[0].tobytes() == plain_d.tobytes()
    assert _ink(heavy_hanzi.render(hanzi)[0]) > _ink(plain_h) * 1.05


def test_weight_keeps_layout_and_random_stream(cfg):
    doc = parse("第 1 题：设 $x^2=4$，求 x。\n\n答案为 2 或 -2。" * 3)
    for sets in (1, 4):
        base = Renderer(cfg.replace(glyph_sets=sets))
        heavy = Renderer(cfg.replace(glyph_sets=sets, hanzi_weight=3, symbol_weight=-2))
        states = []
        for r in (base, heavy):
            pages, state = r._start(doc)
            for i, page in enumerate(pages):
                r.render_page(page, i + 1, len(pages), state)
            states.append((pages, state.rng.getstate()))
        (pages_a, rng_a), (pages_b, rng_b) = states
        assert [[ln["h"] for ln in p] for p in pages_a] == [[ln["h"] for ln in p] for p in pages_b]
        assert [[ln.get("toks") for ln in p] for p in pages_a] == \
            [[ln.get("toks") for ln in p] for p in pages_b]
        assert rng_a == rng_b


def test_weighted_render_is_deterministic(cfg):
    doc = parse("权重 123 $\\sqrt{a}+b$\n\n$$\\begin{pmatrix}1&2\\\\3&4\\end{pmatrix}$$")
    for sets in (1, 4):
        heavy = cfg.replace(glyph_sets=sets, hanzi_weight=2, symbol_weight=3)
        a, b = Renderer(heavy), Renderer(heavy)
        assert a.render(doc)[0].tobytes() == b.render(doc)[0].tobytes()
        pdf = a.pdf(doc)
        assert pdf == b.pdf(doc)
        assert pdf.startswith(b"%PDF")
