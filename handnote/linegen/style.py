"""Style references and text checks for DiffBrush (numpy + Pillow only).

DiffBrush reads one handwritten line, 64 px high, as the style to imitate.
Upstream picks a reference wider than 512 px; shorter lines are tiled here.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .weights import LETTERS

HEIGHT = 64
MAX_WIDTH = 1024  # the model's fixed canvas width
MIN_WIDTH = 512   # upstream's "critical width" for style references
MAX_CHARS = 70    # upstream generates 35-61 characters per line
_IMAGES = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")


def unsupported(text: str) -> list[str]:
    """Characters the English checkpoint cannot write, in order of appearance."""
    seen: list[str] = []
    for ch in text:
        if ch not in LETTERS and ch not in seen:
            seen.append(ch)
    return seen


def check_text(text: str) -> str:
    """Return ``text`` or raise ``ValueError`` naming what cannot be written."""
    bad = unsupported(text)
    if bad:
        shown = " ".join(repr(c) for c in bad[:10])
        raise ValueError(f"the DiffBrush checkpoint only writes English (IAM letters); "
                         f"unsupported: {shown}")
    if not text.strip():
        raise ValueError("empty line")
    return text


MIN_CHARS = 35  # shorter lines get the rest of the canvas filled with repeated text


def wrap(text: str, width: int = 60) -> list[str]:
    """Word wrap into lines of at most ``width`` characters, balanced in length.

    The model was trained on lines of 35-61 characters and fills a short line's
    leftover canvas with repeated text, so each paragraph is spread evenly over
    the fewest lines instead of leaving a short last line.
    """
    width = max(1, min(width, MAX_CHARS))
    lines: list[str] = []
    for para in text.splitlines():
        greedy = _greedy(para, width)
        target = width
        if len(greedy) > 1:
            target = -(-len(" ".join(greedy)) // len(greedy))
            while len(_greedy(para, target)) > len(greedy):
                target += 1
        lines += _greedy(para, min(target, width))
    return lines


def _greedy(para: str, width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in para.split():
        while len(word) > width:
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:width])
            word = word[width:]
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    if line:
        lines.append(line)
    return lines


def pick_reference(path: str | Path) -> Path:
    """A file, or the first image (by name) wider than 512 px in a folder, else the widest."""
    path = Path(path)
    if path.is_file():
        return path
    files = sorted(p for p in path.iterdir() if p.suffix.lower() in _IMAGES)
    if not files:
        raise ValueError(f"no images in {path}")
    widths = []
    for f in files:
        with Image.open(f) as im:
            w = im.width * HEIGHT / max(im.height, 1)
        if w > MIN_WIDTH:
            return f
        widths.append(w)
    return files[int(np.argmax(widths))]


def load_style(path: str | Path) -> np.ndarray:
    """``(64, W)`` float32 in [0, 1] (1 = paper), ``512 < W <= 1024``."""
    with Image.open(pick_reference(path)) as im:
        gray = im.convert("L")
    return prepare_style(np.asarray(gray))


def prepare_style(gray: np.ndarray) -> np.ndarray:
    """Scale a uint8 grayscale line to 64 px high, cap at 1024 wide, tile if narrow."""
    h, w = gray.shape
    if (h, w) != (HEIGHT, w):
        new_w = max(1, round(w * HEIGHT / h))
        gray = np.asarray(Image.fromarray(gray).resize((new_w, HEIGHT), Image.LANCZOS))
    img = gray.astype(np.float32) / 255.0
    img = img[:, :MAX_WIDTH]
    if img.shape[1] <= MIN_WIDTH:
        gap = np.ones((HEIGHT, max(8, img.shape[1] // 8)), np.float32)
        tile = np.concatenate([img, gap], axis=1)
        reps = MIN_WIDTH // tile.shape[1] + 1
        img = np.concatenate([tile] * reps, axis=1)[:, :MAX_WIDTH]
    return img


def trim(gray: np.ndarray, threshold: int = 200, margin: int = 8) -> np.ndarray:
    """Cut the blank canvas to the right of the ink (uint8, 255 = paper)."""
    cols = np.where((gray < threshold).any(axis=0))[0]
    if cols.size == 0:
        return gray[:, :margin]
    return gray[:, : min(gray.shape[1], cols[-1] + 1 + margin)]
