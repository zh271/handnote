"""Time DiffBrush on this machine and estimate other hardware from FLOP counts.

The estimate is rough on purpose: work per line (FLOPs, counted by torch) divided
by an assumed sustained fraction of each card's peak. Batch-1 diffusion on a
small latent rarely gets near peak, so measure on the real card when you can.
"""

from __future__ import annotations

import time

import numpy as np

# name -> (peak FP32 TFLOPS, peak FP16 TFLOPS, dense, vendor spec sheets; approximate)
REFERENCE = {
    "AMD RX 9070 XT": (48.7, 97.3),
    "NVIDIA RTX 5060": (19.2, 38.0),
    "NVIDIA RTX 4090 (cloud)": (82.6, 165.2),
}
EFFICIENCY = 0.3  # assumed sustained fraction of peak for batch-1 sampling


def count_flops(gen, text: str, style: np.ndarray) -> dict:
    """FLOPs of the conditioning, one UNet step and the VAE decode."""
    import torch
    from torch.utils.flop_counter import FlopCounterMode

    with torch.no_grad():
        with FlopCounterMode(display=False) as fc:
            context = gen.unet.encode_context(gen._style(style), gen._content(text))
        ctx = fc.get_total_flops()
        x = gen._noise(0)
        t = torch.full((1,), 500, dtype=torch.long, device=gen.device)
        with FlopCounterMode(display=False) as fc:
            gen.unet(x, t, context=context)
        step = fc.get_total_flops()
        with FlopCounterMode(display=False) as fc:
            gen.vae.decode(x)
        dec = fc.get_total_flops()
    return {"context": ctx, "step": step, "decode": dec}


def per_line(flops: dict, steps: int) -> float:
    return flops["context"] + steps * flops["step"] + flops["decode"]


def estimate(flops: dict, steps: int, lines_per_page: int = 25) -> list[dict]:
    """Seconds per line / page for the :data:`REFERENCE` cards."""
    work = per_line(flops, steps)
    rows = []
    for name, (fp32, fp16) in REFERENCE.items():
        for label, peak in (("fp32", fp32), ("fp16", fp16)):
            sec = work / (peak * 1e12 * EFFICIENCY)
            rows.append({"device": name, "precision": label, "sec_per_line": round(sec, 2),
                         "sec_per_page": round(sec * lines_per_page, 1)})
    return rows


def run(device: str = "auto", *, steps: int = 50, repeats: int = 2, half: bool = False,
        style: np.ndarray | None = None, lines_per_page: int = 25) -> dict:
    """Measure a 50-character line on ``device`` and estimate the reference cards."""
    from .generate import LineGenerator
    from .style import prepare_style

    t0 = time.perf_counter()
    gen = LineGenerator(device, steps=steps, half=half)
    load = time.perf_counter() - t0
    if style is None:  # a synthetic scribble: speed does not depend on the style
        rng = np.random.default_rng(0)
        style = prepare_style(np.where(rng.random((64, 900)) < 0.05, 0, 255).astype(np.uint8))
    text = "The quick brown fox jumps over the lazy dog, again."
    gen.write(text, style, seed=0)  # warm-up (kernel selection, allocations)
    runs = []
    for i in range(repeats):
        gen.write(text, style, seed=i + 1)
        runs.append(gen.timings)
    best = min(runs, key=lambda r: r["total"])
    flops = count_flops(gen, text, style)
    measured = per_line(flops, steps) / best["total"]
    return {
        "device": gen.device_label,
        "precision": "fp16" if half else "fp32",
        "steps": steps,
        "load_sec": round(load, 1),
        "sec_per_line": round(best["total"], 2),
        "sec_per_step": round(best["sampling"] / best["steps"], 3),
        "decode_sec": round(best["decode"], 2),
        "sec_per_page": round(best["total"] * lines_per_page, 1),
        "lines_per_page": lines_per_page,
        "gflop_per_line": round(per_line(flops, steps) / 1e9, 1),
        "measured_tflops": round(measured / 1e12, 3),
        "estimates": estimate(flops, steps, lines_per_page),
        "estimate_note": f"peak spec x {EFFICIENCY:.0%} assumed sustained; measure to confirm",
    }
