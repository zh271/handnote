from pathlib import Path

import pytest

from handnote.markdown import parse
from handnote.render import Renderer

EXAMPLES = sorted((Path(__file__).resolve().parents[1] / "examples").glob("*.md"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_renders(path, cfg):
    renderer = Renderer(cfg)
    images = renderer.render(parse(path.read_text(encoding="utf-8")))
    assert images
    assert all(image.size == (cfg.page_width, cfg.page_height) for image in images)


def test_examples_exist():
    assert len(EXAMPLES) >= 3
