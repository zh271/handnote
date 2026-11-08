import numpy as np

from handnote.markdown import parse
from handnote.render import Renderer, render_markdown

DOC = """## 例题

设 $A=\\{1,2,3\\}$，定义 $R$。

$$R=\\{(1,2),(2,2)\\}$$

- 第一条
- 第二条 $x^2$

$$M = \\begin{bmatrix} 0 & 1 \\\\ 1 & 0 \\end{bmatrix}$$
"""


def test_page_size(cfg):
    images = Renderer(cfg).render(parse(DOC))
    assert len(images) == 1
    assert images[0].size == (cfg.page_width, cfg.page_height)


def test_deterministic_for_same_seed(cfg):
    a = Renderer(cfg).render(parse(DOC))[0]
    b = Renderer(cfg).render(parse(DOC))[0]
    assert a.tobytes() == b.tobytes()


def test_seed_changes_layout(cfg):
    a = Renderer(cfg).render(parse(DOC))[0]
    b = Renderer(cfg.replace(seed=99)).render(parse(DOC))[0]
    assert a.tobytes() != b.tobytes()


def test_pagebreak_splits(cfg):
    images = Renderer(cfg).render(parse("甲\n\n---\n\n乙"))
    assert len(images) == 2


def test_long_text_paginates(cfg):
    text = "\n\n".join([f"第 {i} 段正文，用来把页面撑满。" for i in range(60)])
    assert len(Renderer(cfg).render(parse(text))) > 1


def test_display_and_matrix_render_without_warnings(cfg):
    renderer = Renderer(cfg)
    renderer.render(parse(DOC))
    assert renderer.warnings == []


def test_invalid_formula_falls_back(cfg):
    renderer = Renderer(cfg)
    images = renderer.render(parse("前文\n\n$$\\frac{a}{$$\n\n后文"))
    assert images and renderer.warnings, "a broken formula must not raise"


def test_coverage_flags_missing_symbol(cfg):
    report = Renderer(cfg).coverage(parse("$x\\in A$"))
    assert "∈" in [item["char"] for item in report["missing"]]
    assert report["coverage"] < 1


def test_coverage_full_for_plain_text(cfg):
    report = Renderer(cfg).coverage(parse("中文与 $x=1$ 都在字体里"))
    assert report["missing"] == []


def test_pdf_header(cfg):
    renderer = Renderer(cfg)
    pdf = renderer.to_pdf(renderer.render(parse(DOC)))
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_paper_can_be_disabled(cfg):
    image = Renderer(cfg.replace(paper=False)).render(parse("字"))[0].convert("RGB")
    assert image.getpixel((0, 0)) == (255, 255, 255)


def test_body_size_changes_line_count(cfg):
    renderer = Renderer(cfg)
    small = len(renderer.paginate(renderer.layout(parse("很长的一段文字" * 20))))
    renderer = Renderer(cfg.replace(body_size=40))
    large = len(renderer.paginate(renderer.layout(parse("很长的一段文字" * 20))))
    assert large >= small


def test_render_markdown_helper(cfg):
    images, warnings = render_markdown("你好 $x$", cfg)
    assert len(images) == 1
    assert warnings == []


INLINE_MATRIX = r"\begin{bmatrix} 0 & 1 & 1 \\ 0 & 1 & 1 \end{bmatrix}"


def test_inline_matrix_renders_and_grows_line(cfg):
    renderer = Renderer(cfg)
    lines = renderer.layout(parse(f"关系矩阵 ${INLINE_MATRIX}$ 如上"))
    plain = renderer.layout(parse("关系矩阵如上"))
    assert lines[0]["h"] > plain[0]["h"]
    renderer.render(parse(f"关系矩阵 ${INLINE_MATRIX}$ 如上"))
    assert renderer.warnings == []


def test_display_with_two_matrices_renders(cfg):
    renderer = Renderer(cfg)
    tex = r"A = \begin{bmatrix} 1 \\ 2 \end{bmatrix}, B = \begin{pmatrix} 3 & 4 \end{pmatrix}"
    images = renderer.render(parse(f"$${tex}$$"))
    assert len(images) == 1
    assert renderer.warnings == []


def test_glyph_sets_change_output_deterministically(cfg):
    doc = "的的的的 $x+x+x+x$\n\n$$" + INLINE_MATRIX + "$$"
    one = Renderer(cfg.replace(glyph_sets=1)).render(parse(doc))[0]
    four_a = Renderer(cfg.replace(glyph_sets=4)).render(parse(doc))[0]
    four_b = Renderer(cfg.replace(glyph_sets=4)).render(parse(doc))[0]
    assert four_a.tobytes() == four_b.tobytes()
    assert one.tobytes() != four_a.tobytes()


def test_repeated_formula_glyphs_differ(cfg):
    renderer = Renderer(cfg.replace(glyph_sets=4, glyph_strength=1.5))
    verts = renderer._math_path("$xxxx$", 100, "xxxx").vertices
    quarter = len(verts) // 4
    shapes = [verts[i * quarter:(i + 1) * quarter] for i in range(4)]
    shapes = [s - s.mean(axis=0) for s in shapes]
    assert any(not np.allclose(shapes[0], s, atol=0.5) for s in shapes[1:])


def test_single_set_formula_matches_plain_textpath(cfg):
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath

    renderer = Renderer(cfg.replace(glyph_sets=1))
    ours = renderer._math_path("$x^2+\\frac{a}{b}$", 90, "k").vertices
    ref = TextPath((0, 0), "$x^2+\\frac{a}{b}$", size=90,
                   prop=FontProperties(size=90)).vertices
    assert np.allclose(ours, ref)
