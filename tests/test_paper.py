import cv2
import numpy as np
import pytest
from PIL import Image

from handnote import paper
from handnote.config import RenderConfig
from handnote.markdown import parse
from handnote.render import Renderer

TEXT = ("# 标题\n\n正文第一行，写得长一点好让它换行。abc 和公式 $x^2$ 混排。"
        "\n\n$$a+b=c$$\n\n最后一行")


def _with(cfg, name, **extra):
    return cfg.replace(**{**paper.preset(name), **extra})


def test_every_preset_is_a_valid_config(cfg):
    for item in paper.presets():
        merged = RenderConfig.from_mapping({**cfg.as_dict(), **item["config"]})
        assert merged.paper_style == item["config"]["paper_style"]
    with pytest.raises(ValueError):
        paper.preset("nope")
    with pytest.raises(ValueError):
        cfg.replace(paper_style="wavy").validate()
    with pytest.raises(ValueError):
        cfg.replace(rule_color="blue").validate()


def test_plain_preset_matches_the_default_page(cfg):
    blocks = parse(TEXT)
    default = Renderer(cfg).render(blocks)[0].tobytes()
    plain = Renderer(_with(cfg, "plain")).render(blocks)[0].tobytes()
    assert default == plain


@pytest.mark.parametrize("name", ["lined", "grid", "dots", "essay", "tianzi"])
def test_baselines_land_on_rules(cfg, name):
    renderer = Renderer(_with(cfg, name))
    g = renderer.grid
    assert g.snap and g.pitch > 0
    y = g.top
    for line in renderer.layout(parse(TEXT)):
        assert line["h"] % g.pitch == pytest.approx(0, abs=1e-6) or line["h"] == 0
        if "size" in line:
            baseline = y + line["pad"] + line["size"] * 1.28
            rows_down = (baseline + g.drop - g.first) / g.pitch
            assert rows_down == pytest.approx(round(rows_down), abs=1e-6)
        y += line["h"]


def test_rules_are_printed_where_the_grid_says(cfg):
    page = _with(cfg, "lined", paper=False, page_numbers=False)
    renderer = Renderer(page)
    image = np.asarray(renderer.render(parse("字"))[0].convert("RGB")).astype(int)
    g = renderer.grid
    rows = g.rows()
    y = int(g.rule(rows[-1]))
    x = page.page_width // 2
    r, gr, b = image[y, x]
    assert b > r + 20, "rule colour (blue) at the rule"
    assert tuple(image[y + 8, x]) == (255, 255, 255), "white between rules"


def test_essay_paper_puts_one_character_per_cell(cfg):
    renderer = Renderer(_with(cfg, "essay"))
    cell = renderer.grid.cell
    assert renderer.char_metrics("中", 20)[1] == pytest.approx(cell)
    assert renderer.char_metrics("a", 20)[1] == pytest.approx(cell / 2)
    # One ASCII letter leaves half a cell; the next character starts a new cell.
    assert renderer._cell_run("a中", 20, 0.0)[0] == pytest.approx(2 * cell)
    assert renderer._cell_run("ab中", 20, 0.0)[0] == pytest.approx(2 * cell)
    # A space before a full-width character does not leave an empty cell.
    assert renderer._cell_run("1.  中", 20, 0.0)[0] == pytest.approx(2 * cell)
    assert renderer._cell_run("abc 中", 20, 0.0)[0] == pytest.approx(3 * cell)


def test_vector_pdf_has_rules_and_colour(cfg):
    pdf = Renderer(_with(cfg, "eyecare")).pdf(parse(TEXT))
    assert b"/DeviceRGB" in pdf and b"%PDF" in pdf
    plain = Renderer(cfg).pdf(parse(TEXT))
    assert len(pdf) > len(plain)


def _photo(lines=True):
    sheet = Image.new("RGB", (600, 840), (236, 233, 225))
    arr = np.asarray(sheet).copy()
    if lines:
        for y in np.arange(90.0, 800.0, 31.0):
            arr[int(y): int(y) + 2, 30:570] = (140, 165, 205)
    src = np.float32([[0, 0], [599, 0], [599, 839], [0, 839]])
    dst = np.float32([[120, 90], [760, 130], [800, 1080], [80, 1040]])
    m = cv2.getPerspectiveTransform(src, dst)
    photo = cv2.warpPerspective(arr, m, (900, 1180), borderValue=(55, 50, 45)).astype(np.float32)
    photo *= np.linspace(0.75, 1.0, 900, dtype=np.float32)[None, :, None]
    return Image.fromarray(photo.clip(0, 255).astype(np.uint8)), dst


def test_scan_is_found_flattened_and_its_lines_measured():
    photo, truth = _photo()
    corners = np.asarray(paper.detect_page(photo))
    assert np.abs(corners - truth).max() < 8
    flat = paper.flatten_scan(photo, corners.tolist(), (600, 840))
    gray = np.asarray(flat.convert("L"), dtype=float)
    # Uneven light is evened out and the paper brightened.
    assert abs(gray[415, 40:80].mean() - gray[415, 520:560].mean()) < 12
    assert gray[415, 300] > 225
    rules = paper.detect_rules(flat)
    assert rules is not None
    assert rules["pitch"] == pytest.approx(31.0, abs=0.6)
    assert rules["top"] == pytest.approx(91.0, abs=3)
    assert rules["count"] >= 18


def test_blank_scan_has_no_rules():
    photo, _ = _photo(lines=False)
    flat = paper.flatten_scan(photo, paper.detect_page(photo), (600, 840))
    assert paper.detect_rules(flat) is None


def test_scanned_paper_is_the_background(cfg, tmp_path):
    sheet = Image.new("RGB", (cfg.page_width, cfg.page_height), (230, 200, 150))
    path = tmp_path / "paper-test.png"
    sheet.save(path)
    page = cfg.replace(paper_style="image", paper_image=str(path), page_numbers=False,
                       rule_pitch=40.0, rule_top=120.0)
    renderer = Renderer(page)
    assert renderer.grid.snap and renderer.grid.pitch == 40.0
    image = renderer.render(parse("字"))[0].convert("RGB")
    assert image.getpixel((5, page.page_height - 5)) == (230, 200, 150)
    pdf = renderer.pdf(parse("字"))
    assert b"/DCTDecode" in pdf
    missing = Renderer(page.replace(paper_image=str(tmp_path / "gone.png")))
    assert any("paper image" in w for w in missing.warnings)


def test_counted_essay_paper_has_fixed_cells_and_running_counts(cfg):
    page = _with(cfg, "essay")  # 20 × 20 = 400
    renderer = Renderer(page)
    g = renderer.grid
    assert g.count == (20, 20) and g.cols == 20 and len(g.rows()) == 20
    assert g.x0 + 20 * g.cell <= page.page_width - page.margin_x + 1e-6
    assert renderer.cfg.body_size == paper.fit_body(page)
    labels = [r.label for r in paper.rules(renderer.cfg, g, 1) if r.kind == "text"]
    assert labels == ["100", "200", "300", "400", "20×20=400"]
    labels = [r.label for r in paper.rules(renderer.cfg, g, 2) if r.kind == "text"]
    assert labels[0] == "500" and labels[-2] == "800"
    no_marks = paper.rules(renderer.cfg.replace(count_marks=False), g, 1)
    assert not [r for r in no_marks if r.kind == "text"]
    from handnote.vector import _rule_ops

    ops = _rule_ops(paper.rules(renderer.cfg, g, 1), lambda a: f"A{a}")
    assert " f Q" in ops and " re " not in ops, "labels are filled outlines in the PDF"
    assert renderer.pdf(parse(TEXT)).startswith(b"%PDF")


def test_cell_alignment_is_optional(cfg):
    free = Renderer(_with(cfg, "essay", cell_snap=False))
    assert free.grid.cell and not free.grid.align
    assert free.char_metrics("中", 20)[1] != pytest.approx(free.grid.cell)
    squares = Renderer(_with(cfg, "grid", cell_snap=True))
    g = squares.grid
    assert g.align and g.unit == g.cell and g.pitch % g.cell == 0
    assert squares.char_metrics("中", 20)[1] == pytest.approx(g.cell)
    plain_grid = Renderer(_with(cfg, "grid", cell_snap=False))
    assert not plain_grid.grid.align
