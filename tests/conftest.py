import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from handnote.config import RenderConfig  # noqa: E402
from handnote.fonts import default_font, ensure_font  # noqa: E402


@pytest.fixture(scope="session")
def font_path() -> Path:
    found = default_font()
    if found:
        return found
    return ensure_font("zhimang")


@pytest.fixture(scope="session")
def dejavu_path() -> Path:
    import matplotlib

    return Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"


@pytest.fixture
def cfg(font_path) -> RenderConfig:
    return RenderConfig.from_mapping({
        "font_path": str(font_path),
        "page_width": 620,
        "page_height": 877,
        "margin_x": 46,
        "margin_top": 48,
        "margin_bottom": 48,
        "body_size": 20,
        "paper_noise": 1.0,
    })
