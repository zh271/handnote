"""JSON API behind the handwritten-hanzi page (``/hanzi``).

Routes (all under ``/api/hanzi/``):

- ``GET status``: whether the stroke data is downloaded; the recommended list.
- ``GET char?c=清江``: stroke count and reference medians (the trace guide).
- ``POST check``: ``{char, strokes}`` -> stroke check result.
- ``POST model/status``: ``{capture}`` -> torch installed, weights present,
  whether a model for this capture is trained, the running job.
- ``POST model/fetch``: download the SDT weights (job).
- ``POST model/train``: ``{capture, iters}`` -> fine-tune (job); reuses a model
  already trained on the same capture.
- ``POST model/generate``: ``{capture, targets, sets, merge, replace, pressure}``
  -> generate, build the font and merge it (job).
- ``GET job/<id>``: job progress and, when done, its report.

Training and generation take minutes, so they run in background threads, one
heavy job at a time.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import hanzi_data
from ..fonts import resolve_font
from ..hanzi_font import RECOMMENDED, build_hanzi_font, match_strokes, normalize_hanzi
from ..symbols import merge_fonts

MAX_QUERY_CHARS = 64
MAX_TARGETS = 8000
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_HEAVY = threading.Lock()  # one training/generation at a time (CPU bound)


def handle_get(handler, path: str, query: dict[str, list[str]]) -> bool:
    """Serve a GET route; returns False when ``path`` is not a hanzi route."""
    if path == "/api/hanzi/status":
        handler._json({"available": hanzi_data.available() or hanzi_data._LOADED is not None,
                       "recommended": RECOMMENDED})
        return True
    if path == "/api/hanzi/char":
        chars = (query.get("c", [""])[0])[:MAX_QUERY_CHARS]
        data = hanzi_data.load()
        handler._json({ch: _char_info(data, ch) for ch in dict.fromkeys(chars)})
        return True
    if path.startswith("/api/hanzi/job/"):
        with _JOBS_LOCK:
            job = dict(_JOBS.get(path.rsplit("/", 1)[-1]) or {})
        if not job:
            handler._error("no such job", 404)
        else:
            handler._json(job)
        return True
    return False


def handle_post(handler, path: str) -> bool:
    """Serve a POST route; returns False when ``path`` is not a hanzi route."""
    routes = {"/api/hanzi/check": _check, "/api/hanzi/model/status": _model_status,
              "/api/hanzi/model/fetch": _model_fetch, "/api/hanzi/model/train": _model_train,
              "/api/hanzi/model/generate": _model_generate}
    route = routes.get(path)
    if route is None:
        return False
    handler._json(route(handler._payload()))
    return True


def _char_info(data: hanzi_data.HanziData, ch: str) -> dict | None:
    entry = data.get(ch)
    if entry is None:
        return None
    return {"strokes": entry.strokes,
            "medians": [[[round(float(u), 4), round(float(v), 4)] for u, v in m]
                        for m in entry.medians]}


def _check(payload: dict) -> dict:
    ch = payload.get("char") or ""
    sets = normalize_hanzi({"chars": [{"char": ch, "strokes": payload.get("strokes")}]})
    return match_strokes(sets[0][ch], ch, hanzi_data.load()).to_dict()


# -- model -----------------------------------------------------------------------
def _torch() -> bool:
    from ..model import torch_available

    return torch_available()


def _models_dir() -> Path:
    from ..model import weights

    return weights.cache_dir() / "models"


def _model_for(capture: dict) -> Path:
    """Where the model trained on ``capture`` lives (keyed by its content)."""
    from ..model.train import capture_hash

    return _models_dir() / f"{capture_hash(capture)}.pth"


def _running() -> dict | None:
    with _JOBS_LOCK:
        for job_id, job in _JOBS.items():
            if job["state"] == "running" and job["kind"] != "fetch":
                return {"id": job_id, "kind": job["kind"]}
    return None


def _model_status(payload: dict) -> dict:
    from ..model import weights

    out: dict[str, Any] = {"torch": _torch(), "weights": weights.status(), "model": None,
                           "running": _running()}
    capture = payload.get("capture")
    if out["torch"] and capture and capture.get("chars"):
        from ..model.train import load_meta

        path = _model_for(capture)
        if path.is_file():
            meta = load_meta(path)
            out["model"] = {"chars": meta.get("chars"), "iters": meta.get("iters"),
                            "seconds": meta.get("seconds"),
                            "pressure": "learned" if meta.get("pressure") else "rule"}
    return out


def _start(kind: str, work: Callable[[Callable[[dict], None]], dict], heavy: bool) -> dict:
    """Run ``work(progress)`` in a thread; its return value becomes the report."""
    if heavy and not _HEAVY.acquire(blocking=False):
        raise ValueError("已有训练或生成任务在运行，请等它结束")
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[job_id] = {"state": "running", "kind": kind, "progress": {}}
        for old in list(_JOBS)[:-12]:
            if _JOBS[old]["state"] != "running":
                _JOBS.pop(old)

    def progress(info: dict) -> None:
        with _JOBS_LOCK:
            _JOBS[job_id]["progress"] = info

    def run() -> None:
        try:
            report = work(progress)
            with _JOBS_LOCK:
                _JOBS[job_id].update(state="done", report=report)
        except Exception as exc:  # noqa: BLE001 - reported to the page
            with _JOBS_LOCK:
                _JOBS[job_id].update(state="error", error=f"{type(exc).__name__}: {exc}")
        finally:
            if heavy:
                _HEAVY.release()

    threading.Thread(target=run, daemon=True).start()
    return {"job": job_id}


def _need_torch() -> None:
    if not _torch():
        raise ValueError("需要先安装模型依赖：pip install -e \".[model]\" "
                         "--extra-index-url https://download.pytorch.org/whl/cpu")


def _model_fetch(_payload: dict) -> dict:
    from ..model import weights

    def work(progress):
        hanzi_data.load()
        return weights.fetch(lambda what, done, total: progress(
            {"file": what, "done": done, "total": total}))

    return _start("fetch", work, heavy=False)


def _model_train(payload: dict) -> dict:
    _need_torch()
    capture = payload.get("capture") or {}
    normalize_hanzi(capture)  # fail fast on bad input
    target = _model_for(capture)
    if target.is_file() and not payload.get("force"):
        return {"job": None, "cached": True}
    iters = min(max(int(payload.get("iters") or 300), 25), 2000)

    def work(progress):
        report = train_model(capture, target, iters, progress)
        report.pop("log", None)
        return report

    return _start("train", work, heavy=True)


def _model_generate(payload: dict) -> dict:
    from . import server

    _need_torch()
    capture = payload.get("capture") or {}
    normalize_hanzi(capture)
    model = _model_for(capture)
    if not model.is_file():
        raise ValueError("还没有用当前写的字训练模型")
    targets = str(payload.get("targets") or "")[:MAX_TARGETS]
    if payload.get("gb1"):
        targets = "".join(hanzi_data.gb2312_level1()) + targets
    if not any("㐀" <= ch <= "鿿" for ch in targets):
        raise ValueError("没有要生成的汉字")
    sets = min(max(int(payload.get("sets") or 1), 1), 4)
    merge_into = _merge_target(payload.get("merge"))
    replace = bool(payload.get("replace", True))
    gain = payload.get("pressure")
    gain = 1.0 if gain is None else min(max(float(gain), 0.0), 2.0)
    upload_dir = server.UPLOAD_DIR

    def work(progress):
        result = generate_chars(capture, model, targets, sets, progress)
        progress({"phase": "build"})
        upload_dir.mkdir(parents=True, exist_ok=True)
        tag = uuid.uuid4().hex[:8]
        raw = upload_dir / f"hanzi-{tag}.ttf"
        report = {**result["report"], **build_hanzi_font(result["capture"], raw, pressure=gain)}
        report.pop("chars", None)
        if merge_into and report["glyphs"]:
            merged = upload_dir / f"merged-{tag}.ttf"
            outcome = merge_fonts(merge_into, raw, merged, replace=replace,
                                  family_suffix=" Hand")
            report.update(font=str(merged), merged_into=merge_into,
                          merged_replaced=outcome["replaced"], merged_added=outcome["added"])
        report["url"] = "/api/font-file/" + Path(report["font"]).name
        return report

    return _start("generate", work, heavy=True)


def _merge_target(value: Any) -> str:
    if value is None or value is True:
        try:
            return str(resolve_font(""))
        except FileNotFoundError:
            return ""
    value = str(value or "")
    if value and not Path(value).is_file():
        raise ValueError(f"font not found: {value}")
    return value


# Indirection so tests can stand in for the minutes-long model calls.
def train_model(capture: dict, target: Path, iters: int, progress) -> dict:
    from ..model.train import finetune

    return finetune(capture, target, iters=iters, progress=progress)


def generate_chars(capture: dict, model: Path, targets: str, sets: int, progress) -> dict:
    from ..model.generate import generate

    cache = model.with_name(f"{model.stem}-partial.json")
    return generate(capture, model, targets, sets=sets, cache_path=cache, progress=progress)
