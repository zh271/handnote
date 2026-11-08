
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
