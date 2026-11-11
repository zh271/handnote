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
