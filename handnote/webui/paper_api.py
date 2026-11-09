"""JSON API for custom paper: scanned sheets turned into page backgrounds.

Routes (all under ``/api/paper/``):

- ``POST scan``: multipart ``file`` (a photo or scan) -> ``{id, width, height,
  corners}``; the corners are the detected sheet outline in image pixels.
- ``GET raw/<id>``: downscaled JPEG of the uploaded photo, for the corner editor.
- ``POST apply``: ``{id, corners, width, height, evenness, whiten, gray}`` ->
  ``{path, url, rules}``; warps the sheet to the page size and looks for
  printed lines (``rules`` is ``{pitch, top, ...}`` or ``null``).
- ``GET file/<name>``: a processed sheet (``paper-*.png``).
"""

from __future__ import annotations

import io
import re
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from .. import paper

_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_FILE_RE = re.compile(r"^paper-[0-9a-f]{12}(-[0-9a-f]{6})?\.png$")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
#: Uploaded photos are stored at most this many pixels on the long side.
MAX_SIDE = 4000
PREVIEW_SIDE = 1100


def _upload_dir() -> Path:
    from . import server  # late import: tests patch server.UPLOAD_DIR

    server.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return server.UPLOAD_DIR


def _raw_path(scan_id: str) -> Path:
    if not _ID_RE.match(scan_id or ""):
        raise ValueError("bad scan id")
    path = _upload_dir() / f"paperscan-{scan_id}.png"
    if not path.is_file():
        raise FileNotFoundError("scan expired; upload it again")
    return path


def handle_get(handler, path: str) -> bool:
    """Serve a GET route; returns False when ``path`` is not a paper route."""
    if path.startswith("/api/paper/raw/"):
        try:
            raw = _raw_path(path.rsplit("/", 1)[-1])
        except (ValueError, FileNotFoundError) as exc:
            handler._error(str(exc), 404)
            return True
        with Image.open(raw) as img:
            img = img.convert("RGB")
            img.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
        handler._send(200, buffer.getvalue(), "image/jpeg")
        return True
    if path.startswith("/api/paper/file/"):
        name = path.rsplit("/", 1)[-1]
        target = _upload_dir() / name
        if not _FILE_RE.match(name) or not target.is_file():
            handler._error("not found", 404)
        else:
            handler._send(200, target.read_bytes(), "image/png")
        return True
    return False


def handle_post(handler, path: str) -> bool:
    """Serve a POST route; returns False when ``path`` is not a paper route."""
    if path == "/api/paper/scan":
        _scan(handler)
        return True
    if path == "/api/paper/apply":
        _apply(handler)
        return True
    return False


def _scan(handler) -> None:
    from .server import parse_multipart

    ctype = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in ctype:
        raise ValueError("expected multipart/form-data")
    parts = [p for p in parse_multipart(handler._body(), ctype) if p.filename]
    if not parts:
        raise ValueError("no image in request")
    part = parts[0]
    if Path(part.filename or "").suffix.lower() not in _IMAGE_SUFFIXES:
        raise ValueError("expected a PNG, JPEG, WebP, BMP or TIFF image")
    with Image.open(io.BytesIO(part.data)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    scan_id = uuid.uuid4().hex[:12]
    img.save(_upload_dir() / f"paperscan-{scan_id}.png")
    handler._json({"id": scan_id, "width": img.width, "height": img.height,
                   "corners": paper.detect_page(img),
                   "preview": f"/api/paper/raw/{scan_id}"})


def _apply(handler) -> None:
    payload = handler._payload()
    raw = _raw_path(str(payload.get("id", "")))
    width = int(payload.get("width") or 1240)
    height = int(payload.get("height") or 1754)
    if not (240 <= width <= 5000 and 240 <= height <= 8000):
        raise ValueError("page size out of range")
    corners = payload.get("corners")
    if not isinstance(corners, list) or len(corners) != 4:
        raise ValueError("expected four corners")
    corners = [[float(x), float(y)] for x, y in corners]
    with Image.open(raw) as img:
        sheet = paper.flatten_scan(img, corners, (width, height),
                                   evenness=float(payload.get("evenness", 0.8)),
                                   whiten=float(payload.get("whiten", 0.6)),
                                   gray=bool(payload.get("gray", False)))
    # A new name per apply, so the renderer's image cache never serves an old sheet.
    name = f"paper-{raw.stem.split('-', 1)[1]}-{uuid.uuid4().hex[:6]}.png"
    target = _upload_dir() / name
    sheet.save(target, optimize=True)
    handler._json({"path": str(target), "url": f"/api/paper/file/{name}",
                   "rules": paper.detect_rules(sheet)})
