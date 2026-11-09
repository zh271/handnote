"""Fine-tune SDT on one writer's browser strokes (CPU).

Only the pen loss is used (GMM offsets + pen states). The writer and glyph
contrastive losses compare many writers per batch and mean nothing for one
writer. The content encoder stays frozen so the model keeps what it knows
about character structure; everything else adapts to the writer.

A trained model is a pair of files: ``NAME.pth`` (weights) and ``NAME.json``
(capture hash, training log and the writer's pressure model).
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import torch

from . import data, pressure, sdt, weights

MIN_CHARS = 30
GOOD_CHARS = 60
N_STYLE = 15

Progress = Callable[[dict[str, Any]], None]


def capture_hash(capture: dict[str, Any]) -> str:
    """Hash of what the model learns from (order-independent)."""
    h = hashlib.sha256()
    for w in data.written_chars(capture):
        h.update(f"{w.char}{w.set}".encode())
        for s in w.strokes:
            h.update(np.round(s, 4).tobytes())
    return h.hexdigest()[:16]


def meta_path(model_path: str | Path) -> Path:
    return Path(model_path).with_suffix(".json")


def load_meta(model_path: str | Path) -> dict[str, Any]:
    path = meta_path(model_path)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _batch(items, glyphs, index, bank, rng, eps, augment):
    seqs = [data.to_sequence(w.strokes, eps, rng if augment else None) for w in items]
    length = max(len(s) for s in seqs) + 1
    coords = torch.zeros(len(items), length, 5)
    coords[:, :, -1] = 1  # padding is the end state
    lens = torch.zeros(len(items))
    for i, s in enumerate(seqs):
        coords[i, :len(s)] = torch.from_numpy(s)
        lens[i] = len(s)
    style = torch.stack([
        torch.from_numpy(bank[rng.sample(range(len(bank)), min(N_STYLE, len(bank)))])
        for _ in items]).unsqueeze(2)
    content = torch.from_numpy(
        np.stack([glyphs[index[w.char]] for w in items]).astype(np.float32) / 255.0
    ).unsqueeze(1)
    return coords, lens, style, content


def _loss(model, coords, lens, style, content):
    preds = model.decode(style, coords[:, 1:-1], content)
    return sdt.pen_loss(preds, coords[:, 1:], lens)


def finetune(
    capture: dict[str, Any],
    out_path: str | Path,
    *,
    iters: int = 300,
    lr: float = 5e-5,
    batch: int = 12,
    eps: float = data.RDP_EPS,
    holdout: int = 8,
    seed: int = 0,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Fine-tune the released SDT checkpoint on ``capture``; returns a report.

    Raises ``ValueError`` when fewer than :data:`MIN_CHARS` characters are
    written, and :class:`~.weights.WeightsError` when the weights are missing.
    """
    chars, glyphs = weights.load_content()
    index = {ch: i for i, ch in enumerate(chars)}
    written = data.written_chars(capture)
    usable = [w for w in written if w.char in index]
    distinct = len({w.char for w in usable})
    if distinct < MIN_CHARS:
        raise ValueError(f"写了 {distinct} 个字，至少需要 {MIN_CHARS} 个"
                         f"（建议 {GOOD_CHARS}–100 个）")
    torch.manual_seed(seed)
    rng = random.Random(seed)
    order = usable[:]
    rng.shuffle(order)
    held = {w.char for w in order[:holdout]}
    val = [w for w in order if w.char in held]
    train = [w for w in order if w.char not in held]
    bank = data.style_bank(written)

    model = sdt.SDTGenerator()
    model.load_state_dict(torch.load(weights.checkpoint_path(), map_location="cpu",
                                     weights_only=True))
    for p in model.content_encoder.parameters():
        p.requires_grad = False
    params = [p for p in model.parameters() if p.requires_grad]
    optim = torch.optim.Adam(params, lr=lr)

    def evaluate() -> float:
        model.eval()
        with torch.no_grad():
            loss = _loss(model, *_batch(val, glyphs, index, bank, random.Random(seed + 1),
                                        eps, augment=False)).item()
        model.train()
        return loss

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    best_path = out.with_name(out.stem + "-best.pth")
    log = [{"iter": 0, "val": evaluate()}]
    best = log[0]["val"]
    model.train()
    start, running = time.time(), []
    for step in range(1, iters + 1):
        picked = rng.sample(train, min(batch, len(train)))
        loss = _loss(model, *_batch(picked, glyphs, index, bank, rng, eps, augment=True))
        optim.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 5.0)
        optim.step()
        running.append(loss.item())
        if step % 25 == 0 or step == iters:
            entry = {"iter": step, "train": float(np.mean(running)), "val": evaluate()}
            running = []
            log.append(entry)
            if entry["val"] < best:
                best = entry["val"]
                torch.save(model.state_dict(), best_path)
        if progress:
            elapsed = time.time() - start
            progress({"iter": step, "iters": iters, "loss": loss.item(),
                      "val": log[-1]["val"], "eta": elapsed / step * (iters - step)})
    torch.save(model.state_dict(), out)
    if not best_path.exists():  # validation never improved on the base model
        torch.save(model.state_dict(), best_path)

    strokes = [s for w in written for s in w.strokes]
    pmodel = pressure.fit(strokes)
    warnings = []
    if distinct < GOOD_CHARS:
        warnings.append(f"只写了 {distinct} 个字；写到 {GOOD_CHARS}–100 个字效果更稳定")
    meta = {
        "version": 1,
        "capture": capture_hash(capture),
        "chars": distinct,
        "iters": iters,
        "lr": lr,
        "seed": seed,
        "val_chars": "".join(sorted(held)),
        "log": log,
        "seconds": round(time.time() - start, 1),
        "pressure": pmodel.to_json() if pmodel else None,
        "warnings": warnings,
    }
    meta_path(out).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"model": str(out), "best": str(best_path), **meta}
