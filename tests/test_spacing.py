"""Character spacing, punctuation squeeze and inline formula gaps."""

import pytest

from handnote.capture import build_capture_font
from handnote.markdown import parse
from handnote.render import Renderer, _drop_math_spaces, _tokenize
from handnote.symbols import build_template, merge_fonts


@pytest.fixture(scope="module")
def merged_wide_zero(font_path, tmp_path_factory):
    """Default font with a narrow and a very wide handwritten 0."""
    _pages, spec = build_template(cell_mm=12, dpi=150)
    index = next(cell.index for cell in spec.cells if cell.char == "0")
    narrow = [[[0.45, 0.2], [0.35, 0.5], [0.45, 0.85], [0.55, 0.5], [0.45, 0.2]]]
    wide = [[[0.5, 0.3], [0.05, 0.55], [0.5, 0.8], [0.95, 0.55], [0.5, 0.3]]]
    out = tmp_path_factory.mktemp("wide")
    build_capture_font({"cells": [{"index": index, "set": 0, "strokes": narrow},
                                  {"index": index, "set": 1, "strokes": wide}]},
                       spec, out / "cap.ttf")
    merge_fonts(font_path, out / "cap.ttf", out / "merged.ttf", replace=True)
    return out / "merged.ttf"


def _line_width(renderer, text, size=20):
    toks = _drop_math_spaces(_tokenize(parse(text)[0].segments))
    total = 0.0
    for kind, value in toks:
        if kind == "m":
            total += renderer.math_advance(value, size)[1]
        else:
            total += sum(renderer.char_metrics(ch, size)[1] for ch in value)
    return total


def test_char_spacing_widens_cjk_more_than_latin(cfg):
    tight = Renderer(cfg.replace(char_spacing=0.0))
    loose = Renderer(cfg.replace(char_spacing=0.2))
    cjk = _line_width(loose, "汉字间距") - _line_width(tight, "汉字间距")
    latin = _line_width(loose, "abcd") - _line_width(tight, "abcd")
    assert abs(cjk - 4 * 0.2 * 20) < 0.01
    assert 0 < latin < cjk / 2


def test_punctuation_is_squeezed_to_its_ink(cfg):
    renderer = Renderer(cfg)
    full = renderer.font(40).getlength("，")
    shift, advance = renderer.char_metrics("，", 40)
    assert advance < full * 0.7
    assert shift < 0  # the comma moves left, next to the previous character
    _shift, open_adv = renderer.char_metrics("（", 40)
    assert open_adv < full
    # Squeeze off: punctuation is an ordinary glyph plus character spacing.
    plain = Renderer(cfg.replace(punct_squeeze=0.0))
    assert plain.char_metrics("，", 40) == (0.0, full + cfg.char_spacing * 40)


def test_spaces_next_to_math_follow_the_script():
    toks = _tokenize(parse("设集合 $A$ 上的 where $x$ is")[0].segments)
    kept = _drop_math_spaces(toks)
    assert ("t", "合") in kept and ("t", "上") in kept
    texts = [value for kind, value in kept if kind == "t"]
    # CJK neighbours lose the typed space, Latin ones keep it.
    assert texts[:2] == ["设", "集"]
    idx_a = kept.index(("m", "A"))
    assert kept[idx_a - 1] == ("t", "合") and kept[idx_a + 1] == ("t", "上")
    idx_x = kept.index(("m", "x"))
    assert kept[idx_x - 1] == ("t", " ") and kept[idx_x + 1] == ("t", " ")


def test_digit_and_symbol_spacing_are_separate(cfg):
    base = Renderer(cfg.replace(digit_spacing=0.0, symbol_spacing=0.0))
    wide = Renderer(cfg.replace(digit_spacing=0.3, symbol_spacing=0.2))
    assert abs(wide.char_metrics("7", 20)[1] - base.char_metrics("7", 20)[1] - 6) < 0.01
    assert abs(wide.char_metrics("+", 20)[1] - base.char_metrics("+", 20)[1] - 4) < 0.01
    assert abs(wide.char_metrics("∈", 20)[1] - base.char_metrics("∈", 20)[1] - 4) < 0.01
    # CJK characters and letters do not follow these two settings.
    assert wide.char_metrics("汉", 20) == base.char_metrics("汉", 20)
    assert wide.char_metrics("a", 20) == base.char_metrics("a", 20)


def test_pen_follows_the_drawn_set_width(merged_wide_zero, cfg):
    renderer = Renderer(cfg.replace(font_path=str(merged_wide_zero), glyph_sets=4))
    renderer._prepare_sets(parse("0000"))
    widths = {round(renderer.char_metrics("0", 40, k)[1], 1)
              for k in range(renderer._sets.choices("0"))}
    assert len(widths) > 1
    # Layout plans for the widest set so lines never overrun the margin.
    assert renderer.char_metrics("0", 40)[1] == max(widths) or \
        abs(renderer.char_metrics("0", 40)[1] - max(widths)) < 0.11


def test_digit_spacing_applies_inside_formulas(cfg):
    def width(tex, digit=0.0, symbol=0.0):
        r = Renderer(cfg.replace(glyph_sets=1, digit_spacing=digit, symbol_spacing=symbol))
        return r._math_path(f"${tex}$", 100, "k").get_extents().width

    # Two digit-digit gaps in "123" at a 100px formula size.
    assert abs(width("123", digit=0.3) - width("123") - 2 * 0.3 * 100) < 2
    # "{1,2,3}": no digit pairs, two commas get the symbol spacing.
    assert abs(width("\\{1,2,3\\}", digit=0.3) - width("\\{1,2,3\\}")) < 1
    assert abs(width("\\{1,2,3\\}", symbol=0.2) - width("\\{1,2,3\\}") - 2 * 0.2 * 100) < 2


def test_math_gap_controls_formula_spacing(cfg):
    narrow = Renderer(cfg.replace(math_gap=0.0)).math_advance("x+y", 20)[1]
    wide = Renderer(cfg.replace(math_gap=0.5)).math_advance("x+y", 20)[1]
    assert abs((wide - narrow) - 2 * 0.5 * 20) < 0.01


def test_spacing_keeps_render_deterministic(cfg):
    doc = "设集合 $A=\\{1,2,3\\}$，定义（关系）。"
    a = Renderer(cfg).render_markdown(doc)[0]
    b = Renderer(cfg).render_markdown(doc)[0]
    assert a.tobytes() == b.tobytes()
