"""JSON API behind the handwritten-hanzi page (``/hanzi``).

Routes (all under ``/api/hanzi/``):

- ``GET status``: whether the stroke data is downloaded.
- ``GET char?c=清江``: stroke count, reference medians and top-level parts.
- ``POST check``: ``{char, strokes}`` -> stroke check result.
- ``POST plan``: ``{capture, n, prefer}`` -> coverage and next characters.
- ``POST preview``: ``{capture, chars}`` -> composed PNGs and their sources.
- ``POST build``: ``{capture, sets, merge, replace}`` -> background job id.
- ``GET build/<id>``: job progress and, when done, the build report.
"""

from __future__ import annotations

import base64
import io
import threading
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

from .. import hanzi, hanzi_data
from ..capture import rasterize_strokes
from ..fonts import resolve_font
from ..symbols import merge_fonts

MAX_QUERY_CHARS = 64
MAX_PREVIEW = 24
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def handle_get(handler, path: str, query: dict[str, list[str]]) -> bool:
    """Serve a GET route; returns False when ``path`` is not a hanzi route."""
    if path == "/api/hanzi/status":
        handler._json({"available": hanzi_data.available() or hanzi_data._LOADED is not None})
        return True
    if path == "/api/hanzi/char":
        chars = (query.get("c", [""])[0])[:MAX_QUERY_CHARS]
        data = hanzi_data.load()
        handler._json({ch: _char_info(data, ch) for ch in dict.fromkeys(chars)})
        return True
    if path.startswith("/api/hanzi/build/"):
        job_id = path.rsplit("/", 1)[-1]
        with _JOBS_LOCK:
            job = dict(_JOBS.get(job_id) or {})
        if not job:
            handler._error("no such job", 404)
        else:
            handler._json(job)
        return True
    return False


def handle_post(handler, path: str) -> bool:
    """Serve a POST route; returns False when ``path`` is not a hanzi route."""
    routes = {"/api/hanzi/check": _check, "/api/hanzi/plan": _plan,
              "/api/hanzi/preview": _preview, "/api/hanzi/build": _build}
    route = routes.get(path)
    if route is None:
        return False
    handler._json(route(handler, handler._payload()))
    return True


def _char_info(data: hanzi_data.HanziData, ch: str) -> dict | None:
    entry = data.get(ch)
    if entry is None:
        return None
    parts = []
    if entry.tree is not None:
        for i, kid in enumerate(entry.tree[1]):
            parts.append({"key": hanzi_data.node_key(kid),
                          "strokes": data.strokes_of(ch, (i,))})
    return {"strokes": entry.strokes,
            "medians": [[[round(float(u), 4), round(float(v), 4)] for u, v in m]
                        for m in entry.medians],
            "parts": parts}


def _library(capture: Any, data: hanzi_data.HanziData) -> hanzi.Library:
    if not capture or not capture.get("chars"):
        return hanzi.Library()
    return hanzi.harvest(capture, data)


def _check(_handler, payload: dict) -> dict:
    ch = payload.get("char") or ""
    sets = hanzi.normalize_hanzi({"chars": [{"char": ch, "strokes": payload.get("strokes")}]})
    return hanzi.match_strokes(sets[0][ch], ch, hanzi_data.load()).to_dict()


def _plan(_handler, payload: dict) -> dict:
    data = hanzi_data.load()
    lib = _library(payload.get("capture"), data)
    n = min(max(int(payload.get("n") or 60), 1), 500)
    report = hanzi.coverage(lib, data)
    report["missing"] = report["missing"][:400]
    report["next"] = "".join(hanzi.recommend(lib, data, n=n,
                                             prefer=str(payload.get("prefer") or "")))
    report["unsplit"] = {ch: res.reason for ch, by in lib.checks.items()
                         for res in by.values() if not res.ok}
    return report


def _png(strokes: list, size: int = 128) -> str:
    image = Image.fromarray(rasterize_strokes(strokes, size))
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _preview(_handler, payload: dict) -> dict:
    data = hanzi_data.load()
    composer = hanzi.Composer(_library(payload.get("capture"), data), data)
    sets = min(max(int(payload.get("sets") or 1), 1), 4)
    out = []
    for ch in list(dict.fromkeys(str(payload.get("chars") or "")))[:MAX_PREVIEW]:
        variants = []
        for k in range(sets):
            result = composer.compose(ch, k)
            if result is None:
                break
            variants.append({"png": _png(result.strokes), "written": result.written,
                             "parts": [{"key": key, "source": src, "set": s}
                                       for key, src, s in result.parts]})
        out.append({"char": ch, "ok": bool(variants), "variants": variants})
    return {"chars": out}


def _build(_handler, payload: dict) -> dict:
    from . import server

    capture = payload.get("capture")
    hanzi.normalize_hanzi(capture or {})  # fail fast on bad input
    merge_into = payload.get("merge")
    if merge_into is None or merge_into is True:
        try:
            merge_into = str(resolve_font(""))
        except FileNotFoundError:
            merge_into = ""
    merge_into = str(merge_into or "")
    if merge_into and not Path(merge_into).is_file():
        raise ValueError(f"font not found: {merge_into}")
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[job_id] = {"state": "running", "done": 0, "total": 0}
        for old in list(_JOBS)[:-8]:
            if _JOBS[old]["state"] != "running":
                _JOBS.pop(old)
    args = (job_id, capture, int(payload.get("sets") or 3), merge_into,
            bool(payload.get("replace", True)), server.UPLOAD_DIR)
    threading.Thread(target=_run_build, args=args, daemon=True).start()
    return {"job": job_id}


def _run_build(job_id: str, capture: dict, sets: int, merge_into: str, replace: bool,
               upload_dir: Path) -> None:
    def progress(done: int, total: int) -> None:
        with _JOBS_LOCK:
            _JOBS[job_id].update(done=done, total=total)

    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
        tag = uuid.uuid4().hex[:8]
        raw = upload_dir / f"hanzi-{tag}.ttf"
        report = hanzi.build_hanzi_font(capture, raw, sets=sets, progress=progress)
        report.pop("chars", None)
        if merge_into and report["glyphs"]:
            merged = upload_dir / f"merged-{tag}.ttf"
            result = merge_fonts(merge_into, raw, merged, replace=replace,
                                 family_suffix=" Hand")
            report["font"] = str(merged)
            report["merged_into"] = merge_into
            report["merged_replaced"] = result["replaced"]
            report["merged_added"] = result["added"]
        report["url"] = "/api/font-file/" + Path(report["font"]).name
        with _JOBS_LOCK:
            _JOBS[job_id].update(state="done", report=report)
    except Exception as exc:  # noqa: BLE001 - reported to the page
        with _JOBS_LOCK:
            _JOBS[job_id].update(state="error", error=f"{type(exc).__name__}: {exc}")
