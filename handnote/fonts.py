"""Font discovery: user uploads, local cache, and on-demand download."""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

CACHE_DIR = Path(os.environ.get("HANDNOTE_CACHE") or Path.home() / ".cache" / "handnote") / "fonts"

#: key -> (file name, download URL). All fonts are redistributable.
SOURCES: dict[str, tuple[str, str]] = {
    "zhimang": (
        "ZhiMangXing-Regular.ttf",
        "https://github.com/google/fonts/raw/main/ofl/zhimangxing/ZhiMangXing-Regular.ttf",
    ),
    "mashanzheng": (
        "MaShanZheng-Regular.ttf",
        "https://github.com/google/fonts/raw/main/ofl/mashanzheng/MaShanZheng-Regular.ttf",
    ),
    "patrick": (
        "PatrickHand-Regular.ttf",
        "https://github.com/google/fonts/raw/main/ofl/patrickhand/PatrickHand-Regular.ttf",
    ),
    "caveat": (
        "Caveat-Regular.ttf",
        "https://github.com/google/fonts/raw/main/ofl/caveat/Caveat%5Bwght%5D.ttf",
    ),
    "xkcd": (
        "xkcd-script.ttf",
        "https://github.com/ipython/xkcd-font/raw/master/xkcd-script/font/xkcd-script.ttf",
    ),
    "noto": (
        "NotoSansSC-Regular.otf",
        "https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansSC-Regular.otf",
    ),
}

#: Preference order for the default render font.
DEFAULT_ORDER = ("zhimang", "mashanzheng", "patrick", "noto")


def cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def list_fonts(extra_dirs: list[Path] | None = None) -> list[dict[str, str]]:
    """Return known fonts as ``[{"name", "path", "source"}]``."""
    found: dict[str, dict[str, str]] = {}
    for key, (name, _url) in SOURCES.items():
        path = CACHE_DIR / name
        if path.is_file():
            found[name] = {"name": name, "path": str(path), "source": "cache"}
        for d in extra_dirs or []:
            cand = Path(d) / name
            if cand.is_file():
                found[name] = {"name": name, "path": str(cand), "source": "upload"}
    return sorted(found.values(), key=lambda f: f["name"].lower())


def ensure_font(key: str, timeout: float = 60.0) -> Path:
    """Return a cached font path, downloading it on first use."""
    if key not in SOURCES:
        raise KeyError(f"unknown font {key!r}; known: {', '.join(SOURCES)}")
    name, url = SOURCES[key]
    path = CACHE_DIR / name
    if path.is_file() and path.stat().st_size > 1024:
        return path
    cache_dir()
    tmp = path.with_suffix(path.suffix + ".part")
    with urllib.request.urlopen(url, timeout=timeout) as resp, open(tmp, "wb") as fh:
        fh.write(resp.read())
    tmp.replace(path)
    return path


def default_font() -> Path | None:
    """First cached font from :data:`DEFAULT_ORDER`, or ``None``."""
    for key in DEFAULT_ORDER:
        name = SOURCES[key][0]
        path = CACHE_DIR / name
        if path.is_file():
            return path
    return None


def resolve_font(path: str | None) -> Path:
    """Resolve ``path`` (empty -> default), raising a readable error."""
    if path:
        p = Path(path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"font not found: {p}")
        return p
    found = default_font()
    if found:
        return found
    try:
        return ensure_font(DEFAULT_ORDER[0])
    except Exception as exc:  # noqa: BLE001 - network is best effort
        raise FileNotFoundError(
            "no font available: pass --font /path/to/font.ttf or run "
            "`python -m handnote fonts fetch zhimang`"
        ) from exc
