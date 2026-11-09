"""Write characters with a fine-tuned SDT model and turn them into a capture.

For every target character and set ``k``:

1. SDT writes it from 15 of the writer's style images, a subset chosen by
   ``k`` (greedy decoding, so the same model and subset give the same ink).
2. Stroke check: a character with fewer strokes than the reference is
   written again from other subsets (up to ``retries`` times) and the attempt
   closest to the reference count is kept. One stroke short is usually two
   strokes joined and is accepted; two or more short is dropped, and the
   base font keeps that character.
3. The centre lines are smoothed, placed on the reference glyph's box
   (scaled by how large the writer writes) and given pen pressure.

Characters the writer wrote are passed through unchanged. The result is a
hanzi capture that :func:`handnote.hanzi_font.build_hanzi_font` turns into a font.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import torch

from .. import hanzi_data
from . import data, pressure, sdt, train, weights

N_STYLE = train.N_STYLE
MAX_LEN = 120
CELL_LO, CELL_HI = 0.03, 0.97
#: Placement box for characters missing from the reference data.
DEFAULT_BOX = (0.1, 0.07, 0.9, 0.87)
SAVE_EVERY = 64

Progress = Callable[[dict[str, Any]], None]


def _is_cjk(ch: str) -> bool:
    return "㐀" <= ch <= "鿿"


def _bbox(strokes: list[np.ndarray]) -> tuple[float, float, float, float]:
    allp = np.vstack([s[:, :2] for s in strokes])
    x0, y0 = allp.min(axis=0)
    x1, y1 = allp.max(axis=0)
    return float(x0), float(y0), float(x1), float(y1)


def writer_scale(written: list[data.Written], ref: hanzi_data.HanziData) -> float:
    """How large the writer writes: median sqrt(ink area / reference area)."""
    ratios = []
    for w in written:
        if w.char not in ref:
            continue
        ux0, uy0, ux1, uy1 = _bbox(list(w.strokes))
        rx0, ry0, rx1, ry1 = ref.ref_box(w.char)
        area = (rx1 - rx0) * (ry1 - ry0)
        if area > 0 and ux1 > ux0 and uy1 > uy0:
            ratios.append(math.sqrt((ux1 - ux0) * (uy1 - uy0) / area))
    return float(statistics.median(ratios)) if ratios else 1.0


def place(strokes: list[np.ndarray], box: tuple[float, float, float, float] | None,
          scale: float = 1.0) -> list[np.ndarray]:
    """Map SDT-unit strokes into the cell: centred on ``box``, same area, never
    outside the cell."""
    gx0, gy0, gx1, gy1 = _bbox(strokes)
    gw, gh = max(gx1 - gx0, 1e-6), max(gy1 - gy0, 1e-6)
    bx0, by0, bx1, by1 = box or DEFAULT_BOX
    s = math.sqrt((bx1 - bx0) * (by1 - by0) / (gw * gh)) * scale
    span = CELL_HI - CELL_LO
    s = min(s, span / gw, span / gh)
    cx = min(max((bx0 + bx1) / 2, CELL_LO + gw * s / 2), CELL_HI - gw * s / 2)
    cy = min(max((by0 + by1) / 2, CELL_LO + gh * s / 2), CELL_HI - gh * s / 2)
    mx, my = (gx0 + gx1) / 2, (gy0 + gy1) / 2
    return [np.column_stack([cx + (st[:, 0] - mx) * s, cy + (st[:, 1] - my) * s])
            for st in strokes]


def shade(strokes: list[np.ndarray], model: pressure.PressureModel | None) -> list[list]:
    """Resample placed strokes and add pressure: ``[[u, v, p], ...]`` per stroke."""
    out = []
    for st in strokes:
        dense = pressure.resample(st)
        if model is not None:
            p = model.predict(dense)
        else:
            gaps = np.hypot(*np.diff(st, axis=0).T) if len(st) > 1 else np.zeros(1)
            # Spacing of the generator's own points around each dense point.
            seg_len = np.concatenate([gaps, gaps[-1:]])
            pos = np.concatenate([[0.0], np.cumsum(gaps)])
            at = np.linspace(0, pos[-1], len(dense))
            raw = seg_len[np.clip(np.searchsorted(pos, at, side="right") - 1, 0,
                                  len(seg_len) - 1)]
            p = pressure.fallback(dense, raw)
        out.append([[round(float(u), 4), round(float(v), 4), round(float(q), 3)]
                    for (u, v), q in zip(dense, p, strict=True)])
    return out


def pick_best(group: list[str], write: Callable[[list[str], int], list],
              deficit: Callable[[str, list], int], retries: int) -> dict[str, list]:
    """Write ``group``; rewrite characters missing strokes with attempts
    ``1..retries`` and keep, per character, the attempt with the smallest
    deficit (the earliest on ties)."""
    best = dict(zip(group, write(group, 0), strict=True))
    for attempt in range(1, retries + 1):
        again = [c for c in group if deficit(c, best[c]) >= 1]
        if not again:
            break
        for c, strokes in zip(again, write(again, attempt), strict=True):
            if deficit(c, strokes) < deficit(c, best[c]):
                best[c] = strokes
    return best


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()[:16]


class _Cache:
    """Raw generations saved as they come, so an interrupted run can resume."""

    def __init__(self, path: Path | None, key: str):
        self.path, self.key, self.done = path, key, {}
        if path and path.is_file():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = {}
            if saved.get("key") == key:
                self.done = saved.get("done", {})

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"key": self.key, "done": self.done}), encoding="utf-8")
            tmp.replace(self.path)


def generate(
    capture: dict[str, Any],
    model_path: str | Path,
    targets: str,
    *,
    sets: int = 1,
    retries: int = 3,
    batch: int = 16,
    cache_path: str | Path | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Generate ``targets`` in the writer's hand; returns ``{"capture", "report"}``."""
    start = time.time()
    model_path = Path(model_path)
    meta = train.load_meta(model_path)
    written = data.written_chars(capture)
    if not written:
        raise ValueError("capture has no written characters")
    own = {w.char for w in written}
    chars, glyphs = weights.load_content()
    index = {ch: i for i, ch in enumerate(chars)}
    ref = hanzi_data.load()
    wanted = [ch for ch in dict.fromkeys(targets) if _is_cjk(ch) and ch not in own]
    todo = [ch for ch in wanted if ch in index]
    unsupported = [ch for ch in wanted if ch not in index]
    sets = max(1, min(int(sets), 8))

    bank = torch.from_numpy(data.style_bank(written)).unsqueeze(1)
    model = sdt.SDTGenerator()
    model.load_state_dict(torch.load(model_path, map_location="cpu", weights_only=True))
    model.eval()

    key = f"{_file_hash(model_path)}:{train.capture_hash(capture)}:{retries}"
    cache = _Cache(Path(cache_path) if cache_path else None, key)
    total = len(todo) * sets
    count = [sum(1 for k in range(sets) for ch in todo if f"{k}:{ch}" in cache.done)]

    def run(group: list[str], subset: list[int]) -> list[list[np.ndarray]]:
        style = bank[subset].unsqueeze(0).expand(len(group), -1, -1, -1, -1).contiguous()
        content = torch.from_numpy(
            np.stack([glyphs[index[c]] for c in group]).astype(np.float32) / 255.0
        ).unsqueeze(1)
        return [data.from_sequence(seq) for seq in model.inference(style, content, MAX_LEN)]

    def deficit(ch: str, strokes: list[np.ndarray]) -> int:
        return ref.stroke_count(ch) - len(strokes) if ch in ref else 0

    for k in range(sets):
        pending = [ch for ch in todo if f"{k}:{ch}" not in cache.done]
        for i in range(0, len(pending), batch):
            group = pending[i:i + batch]
            best = pick_best(
                group,
                lambda chars, attempt, k=k: run(
                    chars, data.style_subset(len(bank), N_STYLE, (k, attempt))),
                deficit, retries)
            for c in group:
                cache.done[f"{k}:{c}"] = {"d": deficit(c, best[c]),
                                          "s": [np.round(s, 4).tolist() for s in best[c]]}
            count[0] += len(group)
            if count[0] % SAVE_EVERY < len(group) or i + batch >= len(pending):
                cache.save()
            if progress:
                elapsed = time.time() - start
                progress({"done": count[0], "total": total, "set": k,
                          "seconds": round(elapsed, 1)})
    cache.save()

    pmodel = (pressure.PressureModel.from_json(meta["pressure"])
              if meta.get("pressure") else None)
    scale = writer_scale(written, ref)
    out_chars = [{"char": w.char, "set": w.set,
                  "strokes": [np.round(s, 4).tolist() for s in w.strokes]} for w in written]
    skipped, short = [], []
    for ch in todo:
        for k in range(sets):
            item = cache.done[f"{k}:{ch}"]
            if item["d"] >= 2:
                if k == 0:
                    skipped.append(ch)
                continue
            if item["d"] == 1 and k == 0:
                short.append(ch)
            strokes = [np.asarray(s, dtype=np.float64) for s in item["s"]]
            strokes = [data.chaikin(s) for s in strokes]
            box = ref.ref_box(ch) if ch in ref else None
            out_chars.append({"char": ch, "set": k,
                              "strokes": shade(place(strokes, box, scale), pmodel)})
    elapsed = time.time() - start
    report = {
        "generated": len(todo) - len(skipped),
        "written": len(own),
        "sets": sets,
        "skipped": "".join(skipped),
        "one_short": len(short),
        "unsupported": "".join(unsupported),
        "pressure": "learned" if pmodel else "rule",
        "writer_scale": round(scale, 3),
        "seconds": round(elapsed, 1),
    }
    capture_out = {"version": 1, "kind": "hanzi",
                   "width": capture.get("width", 0.05), "chars": out_chars}
    return {"capture": capture_out, "report": report}
