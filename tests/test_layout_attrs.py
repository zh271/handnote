"""Alignment, indents and blank lines from ``Block.payload`` in the renderer layout."""

import numpy as np
import pytest

from handnote import paper
from handnote.markdown import Block, parse
from handnote.render import Renderer

LONG = "很长的一段文字用来换行" * 8


def _text_lines(renderer, text):
    return [line for line in renderer.layout(parse(text)) if line["kind"] != "gap"]


def test_center_and_right_shift_the_line(cfg):
    renderer = Renderer(cfg)
    (plain,) = _text_lines(renderer, "短句")
    (left,) = _text_lines(renderer, "短句 {.left}")
    (center,) = _text_lines(renderer, "短句 {.center}")
    (right,) = _text_lines(renderer, "短句 {.right}")
    assert plain["indent"] == left["indent"] == 0
    assert 0 < center["indent"] < right["indent"]
    assert center["indent"] == pytest.approx(right["indent"] / 2, abs=1)
    # The text itself does not change, only its left edge.
    assert center["toks"] == right["toks"] == plain["toks"]


def test_right_aligned_ink_is_on_the_right(cfg):
    renderer = Renderer(cfg.replace(page_numbers=False))
    alpha = np.asarray(renderer.render(parse("短句 {.right}"))[0])[..., 3]
    cols = np.nonzero(alpha.max(axis=0))[0]
    assert cols.size and cols.min() > cfg.page_width / 2


def test_indent_and_first_line(cfg):
    renderer = Renderer(cfg)
    plain = _text_lines(renderer, LONG)
    lines = _text_lines(renderer, LONG + " {indent=2 first=2}")
    assert len(plain) > 2 and all(line["indent"] == 0 for line in plain)
    first, rest = lines[0]["indent"], {line["indent"] for line in lines[1:]}
    assert len(rest) == 1
    (following,) = rest
    assert following > 0 and first == pytest.approx(2 * following)

    hanging = _text_lines(renderer, LONG + " {indent=4 first=-2}")
    assert hanging[0]["indent"] == pytest.approx(following)
    assert all(line["indent"] == pytest.approx(2 * following) for line in hanging[1:])
    # Indented lines hold fewer characters, so the paragraph is no shorter.
    assert len(lines) >= len(plain)


def test_heading_and_bullet_indents(cfg):
    renderer = Renderer(cfg)
    (bullet,) = _text_lines(renderer, "- 项目")
    (nested,) = _text_lines(renderer, "- 项目 {indent=2}")
    assert bullet["indent"] == 16, "free layout keeps the bullet indent"
    assert nested["indent"] > bullet["indent"]
    (heading,) = _text_lines(renderer, "## 标题 {.center}")
    assert heading["indent"] > 0


def test_blocks_without_payload_keep_the_old_layout(cfg):
    for preset in ("plain", "lined", "essay"):
        renderer = Renderer(cfg.replace(**paper.preset(preset)))
        text = "# 标题\n\n" + LONG + "\n\n- 项目\n\n1. 步骤"
        parsed = parse(text)
        bare = [Block(b.kind, b.segments) for b in parsed]
        assert all(not b.payload for b in parsed)
        assert renderer.layout(parsed) == renderer.layout(bare)
        expected = 0 if renderer.grid.align else 16
        bullets = [line for line in renderer.layout(parsed) if line["kind"] == "bullet"]
        assert bullets and all(line["indent"] == expected for line in bullets)


def test_blank_line_adds_height(cfg):
    for preset in ("plain", "lined"):
        renderer = Renderer(cfg.replace(**paper.preset(preset)))
        without = renderer.layout(parse("甲\n\n乙"))
        with_blank = renderer.layout(parse("甲\n\n&nbsp;\n\n乙"))
        blank = [line for line in with_blank if line["kind"] == "blank"]
        assert len(blank) == 1 and blank[0]["h"] > 0
        assert (sum(line["h"] for line in with_blank)
                == pytest.approx(sum(line["h"] for line in without) + blank[0]["h"]))
        if renderer.grid.snap:
            assert blank[0]["h"] == pytest.approx(renderer.grid.pitch)
        else:
            assert blank[0]["h"] == round(cfg.body_size * cfg.line_height)
        assert len(renderer.render(parse("甲\n\n&nbsp;\n\n乙"))) == 1


@pytest.mark.parametrize("name", ["essay", "tianzi"])
def test_cell_paper_offsets_are_whole_cells(cfg, name):
    renderer = Renderer(cfg.replace(**paper.preset(name)))
    cell = renderer.grid.align
    assert cell > 0
    text = ("短句 {.center}\n\n短句 {.right}\n\n三个字 {.center}\n\n"
            + LONG + " {indent=1.5 first=2}\n\n" + LONG + " {indent=3 first=-1}")
    lines = _text_lines(renderer, text)
    assert any(line["indent"] > 0 for line in lines)
    for line in lines:
        cells = line["indent"] / cell
        assert cells == pytest.approx(round(cells), abs=1e-6)
    indented = _text_lines(renderer, LONG + " {indent=1.5 first=2}")
    assert indented[0]["indent"] == pytest.approx(3 * cell), "3.5 cells round down"
    assert indented[1]["indent"] == pytest.approx(cell)


def test_extreme_indents_stay_on_the_page(cfg):
    renderer = Renderer(cfg)
    full = cfg.page_width - 2 * cfg.margin_x
    for attrs in ("{indent=20 first=20}", "{first=-20}", "{indent=20 .right}"):
        for line in _text_lines(renderer, LONG + " " + attrs):
            assert 0 <= line["indent"] <= full


def test_deterministic_with_attrs(cfg):
    text = ("# 标题 {.center}\n\n　　" + LONG + "\n\n&nbsp;\n\n"
            "- 项目 {indent=2}\n\n落款 {.right}")
    for preset in ("plain", "essay"):
        page = cfg.replace(**paper.preset(preset))
        a = Renderer(page).render(parse(text))
        b = Renderer(page).render(parse(text))
        assert [im.tobytes() for im in a] == [im.tobytes() for im in b]


def test_indent_unit_is_a_cjk_advance(cfg):
    renderer = Renderer(cfg)
    unit = renderer.char_metrics("中", cfg.body_size)[1]
    (plain,) = _text_lines(renderer, "短句")
    (indented,) = _text_lines(renderer, "短句 {indent=2}")
    assert indented["indent"] - plain["indent"] == pytest.approx(2 * unit)
    (first,) = _text_lines(renderer, "短句 {first=1.5}")
    assert first["indent"] == pytest.approx(1.5 * unit)
    (bullet,) = _text_lines(renderer, "- 项目 {indent=1}")
    assert bullet["indent"] == pytest.approx(16 + unit)


def _kinds(pages):
    return [[line["kind"] for line in page] for page in pages]


def test_trailing_blank_line_does_not_open_a_page(cfg):
    renderer = Renderer(cfg)
    full = "\n\n".join(["一行"] * 21)
    assert len(renderer.paginate(renderer.layout(parse(full)))) == 1
    pages = renderer.paginate(renderer.layout(parse(full + "\n\n&nbsp;")))
    assert len(pages) == 1, _kinds(pages)


def test_blank_line_never_starts_a_page(cfg):
    renderer = Renderer(cfg)
    pages = renderer.paginate(renderer.layout(parse("甲\n\n---\n\n&nbsp;\n\n乙")))
    assert _kinds(pages) == [["p"], ["p"]]
    pages = renderer.paginate(renderer.layout(parse("&nbsp;\n\n甲")))
    assert _kinds(pages) == [["p"]]


def test_blank_line_that_overflows_is_dropped(cfg):
    renderer = Renderer(cfg)
    text = "\n\n".join(["一行"] * 21) + "\n\n&nbsp;\n\n乙"
    pages = renderer.paginate(renderer.layout(parse(text)))
    assert len(pages) == 2 and _kinds(pages)[1][0] == "p", _kinds(pages)


def test_blank_line_mid_page_adds_height(cfg):
    renderer = Renderer(cfg)
    (page,) = renderer.paginate(renderer.layout(parse("甲\n\n&nbsp;\n\n乙")))
    assert [line["kind"] for line in page] == ["p", "blank", "p"]
    (plain,) = renderer.paginate(renderer.layout(parse("甲\n\n乙")))
    assert sum(line["h"] for line in page) > sum(line["h"] for line in plain)


# -- .left on text, marks on display formulas and matrices ------------------------
def test_explicit_left_equals_no_align(cfg):
    for preset in ("plain", "essay"):
        renderer = Renderer(cfg.replace(**paper.preset(preset)))
        for text in ("短句", LONG, "- 项目", "## 标题", "1. 步骤"):
            plain = renderer.layout(parse(text))
            left = renderer.layout(parse(text + " {.left}"))
            assert left == plain, (preset, text)


MATRIX = r"$$M=\begin{bmatrix}1&0\\0&1\end{bmatrix}$$"


def _text_area(renderer, cfg):
    cell = renderer.grid.align
    origin = renderer.grid.x0 if cell else float(cfg.margin_x)
    full = renderer.grid.cols * cell if cell else cfg.page_width - 2 * cfg.margin_x
    return origin, full


def test_unmarked_formulas_have_no_position_keys(cfg):
    renderer = Renderer(cfg)
    (display,) = renderer.layout(parse("$$x+y$$"))
    (matrix,) = renderer.layout(parse(MATRIX))
    assert "x" not in display and "cx" not in matrix
    # first= alone is dropped by the parser, so it does not move the formula either.
    (first,) = renderer.layout(parse("$$x+y$$ {first=2}"))
    assert "x" not in first


def test_display_formula_alignment(cfg):
    renderer = Renderer(cfg)
    origin, full = _text_area(renderer, cfg)
    unit = renderer.char_metrics("中", cfg.body_size)[1]

    def x_of(text):
        (line,) = renderer.layout(parse(text))
        return line["x"], line["img"].width

    x, w = x_of("$$x+y$$ {.left}")
    assert x == pytest.approx(origin)
    x, w = x_of("$$x+y$$ {.left indent=2}")
    assert x == pytest.approx(origin + 2 * unit)
    x, w = x_of("$$x+y$$ {.right}")
    assert x + w == pytest.approx(origin + full)
    x, w = x_of("$$x+y$$ {.center}")
    assert x == pytest.approx((cfg.page_width - w) / 2)
    # Centred in what is left after the indent.
    x, w = x_of("$$x+y$$ {.center indent=4}")
    left = origin + 4 * unit
    assert x == pytest.approx(left + (origin + full - left - w) / 2)
    assert x_of("$$x+y$$ {indent=4}")[0] == pytest.approx(x), "align defaults to centre"
    # Right alignment never goes left of the indent; the indent is capped at 75%.
    x, w = x_of("$$x+y$$ {.right indent=20}")
    assert x >= origin + min(20 * unit, 0.75 * full) - 1e-6
    assert x == pytest.approx(max(origin + min(20 * unit, 0.75 * full), origin + full - w))


def test_matrix_alignment_uses_its_drawn_width(cfg, monkeypatch):
    renderer = Renderer(cfg)
    origin, full = _text_area(renderer, cfg)
    (left,) = renderer.layout(parse(MATRIX + " {.left}"))
    (right,) = renderer.layout(parse(MATRIX + " {.right}"))
    width = renderer.matrix_width(left["prefix"], left["rows"])
    assert width > renderer.matrix_metrics(left["rows"])[2], "the prefix counts"
    assert left["cx"] - width / 2 == pytest.approx(origin)
    assert right["cx"] + width / 2 == pytest.approx(origin + full)

    # draw_matrix is handed cx and spans exactly matrix_width around it.
    seen = []
    original = Renderer.draw_matrix

    def spy(self, canvas, prefix, rows, env, x_center, top, state):
        seen.append((x_center, self.matrix_width(prefix, rows)))
        return original(self, canvas, prefix, rows, env, x_center, top, state)

    monkeypatch.setattr(Renderer, "draw_matrix", spy)
    renderer.render(parse(MATRIX + " {.right}"))
    renderer.render(parse(MATRIX))
    assert seen[0] == (pytest.approx(right["cx"]), pytest.approx(width))
    assert seen[1][0] == pytest.approx(cfg.page_width / 2)


def test_matrix_width_matches_draw_matrix_without_prefix(cfg):
    renderer = Renderer(cfg)
    rows = [["1", "0"], ["0", "1"]]
    assert renderer.matrix_width("", rows) == pytest.approx(renderer.matrix_metrics(rows)[2])


@pytest.mark.parametrize("name", ["essay", "tianzi"])
def test_formula_indent_counts_cells(cfg, name):
    renderer = Renderer(cfg.replace(**paper.preset(name)))
    cell = renderer.grid.align
    (line,) = renderer.layout(parse("$$x+y$$ {.left indent=2}"))
    assert line["x"] == pytest.approx(renderer.grid.x0 + 2 * cell)


def _ink_columns(cfg, text):
    page = Renderer(cfg.replace(page_numbers=False)).render(parse(text))[0]
    alpha = np.asarray(page)[..., 3]
    return np.nonzero(alpha.max(axis=0))[0]


def test_marked_formulas_move_their_ink(cfg):
    left = _ink_columns(cfg, "$$x+y=z$$ {.left}")
    right = _ink_columns(cfg, "$$x+y=z$$ {.right}")
    assert left.max() < cfg.page_width / 2 < right.min()
    left = _ink_columns(cfg, MATRIX + " {.left}")
    right = _ink_columns(cfg, MATRIX + " {.right}")
    assert left.max() < cfg.page_width / 2 < right.min()


def test_unmarked_formulas_render_as_before(cfg):
    # .center without indent is the old page-centred position, to the byte.
    for text in ("$$x+y$$", MATRIX):
        plain = Renderer(cfg).render(parse(text))[0].tobytes()
        assert Renderer(cfg).render(parse(text + " {.center}"))[0].tobytes() == plain
    (display,) = parse("$$x+y$$ {first=2}")
    plain = Renderer(cfg).render(parse("$$x+y$$"))[0].tobytes()
    assert Renderer(cfg).render([display])[0].tobytes() == plain


def test_deterministic_with_formula_marks(cfg):
    text = "$$x+y$$ {.left}\n\n" + MATRIX + " {.right indent=2}"
    a = Renderer(cfg).render(parse(text))[0].tobytes()
    b = Renderer(cfg).render(parse(text))[0].tobytes()
    assert a == b
