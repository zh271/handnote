"""Font discovery: user uploads, local cache, and on-demand download."""

from __future__ import annotations

import os
import time
import urllib.request
from pathlib import Path

CACHE_DIR = Path(os.environ.get("HANDNOTE_CACHE") or Path.home() / ".cache" / "handnote") / "fonts"

#: key -> (file name, download URL). All are SIL OFL 1.1 except xkcd (CC BY-NC 3.0);
#: see CREDITS.md.
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
    for name, _url in SOURCES.values():
        path = CACHE_DIR / name
        if path.is_file():
            found[name] = {"name": name, "path": str(path), "source": "cache"}
    fonts = sorted(found.values(), key=lambda f: f["name"].lower())
    # Built and uploaded fonts, newest first. Raw symbol-only fonts
    # (capture-*/symbols-*) lack CJK glyphs, so only merged ones are listed.
    uploads: list[tuple[float, dict[str, str]]] = []
    for d in extra_dirs or []:
        folder = Path(d)
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.suffix.lower() not in (".ttf", ".otf", ".ttc") or not path.is_file():
                continue
            if path.name.startswith(("capture-", "symbols-")):
                continue
            stamp = path.stat().st_mtime
            label = path.name
            if path.name.startswith("merged-"):
                label = "手写合并 " + time.strftime("%m-%d %H:%M", time.localtime(stamp))
            uploads.append((stamp, {"name": label, "path": str(path), "source": "upload"}))
    uploads.sort(key=lambda item: item[0], reverse=True)
    return fonts + [entry for _, entry in uploads]


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
