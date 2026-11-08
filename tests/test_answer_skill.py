"""The concise-answer skill only promises formulas the renderer can draw."""

import re
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from handnote.markdown import parse, parse_inline
from handnote.render import Renderer

SKILL = Path(__file__).resolve().parents[1] / "skills" / "concise-answer" / "SKILL.md"
_FENCE_RE = re.compile(r"```markdown\n(.*?)```", re.S)
_CODE_RE = re.compile(r"`([^`\n]+)`")


def _sections() -> tuple[str, str]:
    """Return (prose outside fences and outside the banned table, examples)."""
    text = SKILL.read_text(encoding="utf-8")
    examples = "\n".join(_FENCE_RE.findall(text))
    prose = _FENCE_RE.sub("", text)
    allowed, _, rest = prose.partition("### 禁用")
    _, _, after = rest.partition("## 5.")
    return allowed + after, examples


def _whitelisted() -> list[str]:
    prose, _ = _sections()
    out: list[str] = []
    for span in _CODE_RE.findall(prose):
        out += [value for kind, value in parse_inline(span) if kind == "m"]
    return out


def test_skill_lists_formulas():
    assert len(_whitelisted()) > 60


@pytest.mark.parametrize("tex", _whitelisted())
def test_whitelisted_formula_renders(cfg, tex):
    renderer = Renderer(cfg)
    renderer.math_image(tex, 40)
    assert renderer.warnings == []


def test_examples_render_cleanly(cfg):
    _, examples = _sections()
    renderer = Renderer(cfg)
    for doc in _FENCE_RE.findall(SKILL.read_text(encoding="utf-8")):
        renderer.render_markdown(doc)
    assert renderer.warnings == []
    # Every character outside $..$ must exist in the body font.
    cmap = TTFont(cfg.font_path).getBestCmap()
    text = "".join(block.text for block in parse(examples))
    missing = {ch for ch in text if not ch.isspace() and ord(ch) not in cmap}
    assert not missing
