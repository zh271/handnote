"""Vector PDF: well-formed, deterministic, and drawn where the raster page has ink."""

import re

import numpy as np
from PIL import Image

from handnote.markdown import parse
from handnote.render import Renderer
from handnote.vector import compose, path_ops, rotate_about

DOC = """## 例题

设集合 $A=\\{1,2,3\\}$，定义（关系）$R$。

$$R=\\{(1,2),(2,2)\\}$$

$$M=\\begin{bmatrix}1&0\\\\0&1\\end{bmatrix}$$

- 行内矩阵 $\\begin{pmatrix}a&b\\\\c&d\\end{pmatrix}$ 与 $\\frac{1}{2}$
"""


def _check_xref(pdf: bytes) -> int:
    """Every xref entry must point at its object; returns the object count."""
    start = int(re.search(rb"startxref\n(\d+)", pdf).group(1))
    table = pdf[start:].split(b"trailer")[0].split(b"\n")
    count = int(table[1].split()[1])
    for num, line in enumerate(table[3:3 + count - 1], 1):
        offset = int(line.split()[0])
        assert pdf[offset:].startswith(f"{num} 0 obj".encode()), num
    return count


def test_vector_pdf_is_well_formed(cfg):
    renderer = Renderer(cfg)
    blocks = parse(DOC + "\n---\n\n第二页")
    pages = renderer.render_vector(blocks)
    pdf = renderer.to_vector_pdf(pages)
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert _check_xref(pdf) > 10
    assert re.search(rb"/Type /Pages /Kids \[[^\]]*\] /Count 2", pdf)
    assert len(pages) == len(renderer.render(blocks)) == 2
    # Text and formulas are outlines, not images.
    assert b"/Subtype /Form" in pdf and b"/Subtype /Image" not in pdf


def test_vector_pdf_is_deterministic(cfg):
    blocks = parse(DOC)
    a = Renderer(cfg)
    b = Renderer(cfg)
    assert a.to_vector_pdf(a.render_vector(blocks)) == b.to_vector_pdf(b.render_vector(blocks))


def test_vector_pass_leaves_raster_unchanged(cfg):
    blocks = parse(DOC)
    renderer = Renderer(cfg)
    before = renderer.render(blocks)[0].tobytes()
    renderer.render_vector(blocks)
    assert renderer.render(blocks)[0].tobytes() == before
    assert Renderer(cfg).render(blocks)[0].tobytes() == before


def _apply(m, pts):
    a, b, c, d, e, f = m
    return np.column_stack([a * pts[:, 0] + c * pts[:, 1] + e, b * pts[:, 0] + d * pts[:, 1] + f])


def test_vector_items_sit_on_raster_ink(cfg):
    """Each recorded glyph or formula covers ink in the raster page at the same spot."""
    blocks = parse(DOC)
    renderer = Renderer(cfg)
    image = renderer.render(blocks)[0]
    page = renderer.render_vector(blocks)[0]
    ink = np.asarray(image.convert("L")) < 160
    kinds = {item[0] for item in page.items}
    assert kinds == {"path", "stroke"}
    hits = total = 0
    for item in page.items:
        if item[0] == "path":
            shape = renderer._outlines[item[1]]
            pts = _apply(item[2], shape.verts[shape.codes != 79])
        else:
            pts = _apply(item[2], np.asarray(item[1]))
        (x0, y0), (x1, y1) = np.floor(pts.min(axis=0)), np.ceil(pts.max(axis=0))
        box = ink[max(0, int(y0)):int(y1) + 1, max(0, int(x0)):int(x1) + 1]
        total += 1
        # Some ink inside the item's box: a misplaced item lands on blank paper.
        hits += box.size > 0 and box.mean() > 0.05
    assert total > 30
    assert hits / total > 0.95


def test_bitmap_pdf_still_available(cfg):
    blocks = parse(DOC)
    pdf = Renderer(cfg.replace(vector_pdf=False)).pdf(blocks)
    assert pdf.startswith(b"%PDF") and b"/Subtype /Form" not in pdf
    vector = Renderer(cfg).pdf(blocks)
    assert b"/Subtype /Form" in vector


def test_quadratic_curves_become_cubic():
    verts = np.array([(0, 0), (3, 3), (6, 0), (0, 0)], dtype=float)
    codes = np.array([1, 3, 3, 79])
    assert path_ops(verts, codes, 0) == "0 0 m\n2 2 4 2 6 0 c\nh"


def test_rotation_matches_pil():
    # The raster tile is turned with Image.rotate; the vector matrix must agree.
    img = Image.new("L", (41, 41), 0)
    img.putpixel((30, 12), 255)
    turned = np.asarray(img.rotate(70, center=(15, 20)))
    ys, xs = np.nonzero(turned > 40)
    x, y = _apply(rotate_about(70, 15, 20), np.array([[30.5, 12.5]]))[0]
    assert abs(xs.mean() + 0.5 - x) < 1.0 and abs(ys.mean() + 0.5 - y) < 1.0
    m = rotate_about(70, 15, 20)
    assert compose(m, (1, 0, 0, 1, 0, 0)) == m
