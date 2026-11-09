"""Write English text lines in a reference handwriting with DiffBrush.

Pipeline (upstream ``generate.py`` + ``Diffusion.ddim_sample``, single process):
style line (64 x W) + Unifont glyphs of the text -> conditioning, computed once;
seeded Gaussian latent ``(1, 4, 8, 128)`` -> deterministic DDIM (eta = 0) with
the UNet -> SD1.5 VAE decode -> grayscale 64 x 1024 -> right side trimmed.

The same checkpoint, style image, text, seed and step count give the same
image on the same device.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from . import style as _style
from . import weights
from .weights import LETTERS

LATENT = (4, _style.HEIGHT // 8, _style.MAX_WIDTH // 8)
NOISE_STEPS = 1000
VAE_SCALE = 0.18215
WRITERS = 496  # IAM training writers; only sizes the (unused) proxy parameters


def build_unet():
    """The UNet with upstream's IAM configuration (``configs/IAM.yml``)."""
    from .net.unet import UNetModel

    return UNetModel(in_channels=4, model_channels=512, out_channels=4, num_res_blocks=1,
                     attention_resolutions=(1, 1), channel_mult=(1, 1), num_heads=4,
                     context_dim=512, nb_classes=WRITERS)


def ddim_times(steps: int) -> list[tuple[int, int]]:
    """Upstream's DDIM timestep pairs from ``linspace(-1, 999, steps + 1)``.

    With 50 steps: ``(999, 979), ..., (19, -1)``; the last pair returns the x0 estimate.
    """
    import torch

    times = torch.linspace(-1, NOISE_STEPS - 1, steps=steps + 1).int().tolist()
    times = list(reversed(times))
    return list(zip(times[:-1], times[1:], strict=True))


class LineGenerator:
    """Loads the networks once; :meth:`write` produces one line per call.

    Args:
        device: ``auto``, ``cuda``, ``dml`` or ``cpu`` (see :func:`.device.pick_device`).
        steps: DDIM steps (upstream default 50; fewer is faster and rougher).
        half: run in float16 (GPU only).
        unet / vae / content: injected networks and glyphs, for tests.
    """

    def __init__(self, device: str = "auto", *, steps: int = 50, half: bool = False,
                 unet=None, vae=None, content: np.ndarray | None = None):
        import torch

        from .device import pick_device

        self.torch = torch
        self.device, self.device_label = pick_device(device)
        if half and self.device.type == "cpu":
            raise ValueError("float16 is only supported on a GPU")
        self.dtype = torch.float16 if half else torch.float32
        self.steps = steps
        if unet is None:
            folder = weights.require()
            unet = build_unet()
            state = torch.load(folder / weights.CHECKPOINT, map_location="cpu",
                               weights_only=True)
            unet.load_state_dict(state, strict=True)
        if vae is None:
            from diffusers import AutoencoderKL
            from diffusers.utils import logging as dlog

            dlog.set_verbosity_error()  # accelerate / dtype hints are noise here

            vae = AutoencoderKL.from_pretrained(weights.require() / "vae", torch_dtype=self.dtype)
        self.unet = unet.to(self.device, self.dtype).eval()
        self.vae = vae.to(self.device, self.dtype).eval()
        glyphs = weights.load_content() if content is None else content
        self.content = torch.from_numpy(1.0 - np.asarray(glyphs, np.float32))  # 1 = paper
        beta = torch.linspace(1e-4, 0.02, NOISE_STEPS)  # float32, as upstream
        self.alpha_hat = torch.cumprod(1.0 - beta, dim=0)
        self.timings: dict = {}

    # -- inputs ----------------------------------------------------------------------
    def _content(self, text: str):
        idx = [LETTERS.index(c) for c in _style.check_text(text)]
        return self.content[idx].unsqueeze(0).to(self.device, self.dtype)  # (1, T, 16, 16)

    def _style(self, style: np.ndarray):
        return self.torch.from_numpy(style).to(self.device, self.dtype)[None, None]

    def _noise(self, seed: int):
        gen = self.torch.Generator().manual_seed(seed)
        return self.torch.randn((1, *LATENT), generator=gen).to(self.device, self.dtype)

    # -- sampling --------------------------------------------------------------------
    def write(self, text: str, style: np.ndarray, *, seed: int = 0, trim: bool = True,
              progress: Callable[[int, int], None] | None = None) -> Image.Image:
        """One line of ``text`` imitating ``style`` (from :func:`.style.load_style`)."""
        torch = self.torch
        sync = self._sync
        with torch.no_grad():
            t0 = time.perf_counter()
            context = self.unet.encode_context(self._style(style), self._content(text))
            sync()
            t1 = time.perf_counter()
            x = self._noise(seed)
            pairs = ddim_times(self.steps)
            for i, (t, t_next) in enumerate(pairs, 1):
                tt = torch.full((1,), t, dtype=torch.long, device=self.device)
                eps = self.unet(x, tt, context=context)
                a = float(self.alpha_hat[t])
                x0 = (x - (1 - a) ** 0.5 * eps) / a ** 0.5
                if t_next < 0:
                    x = x0
                else:
                    a_next = float(self.alpha_hat[t_next])
                    x = x0 * a_next ** 0.5 + (1 - a_next) ** 0.5 * eps
                if progress:
                    progress(i, len(pairs))
            sync()
            t2 = time.perf_counter()
            img = self.vae.decode(x / VAE_SCALE).sample
            img = (img.float() / 2 + 0.5).clamp(0, 1)[0].permute(1, 2, 0).cpu().numpy()
            sync()
            t3 = time.perf_counter()
        self.timings = {"context": t1 - t0, "sampling": t2 - t1, "decode": t3 - t2,
                        "steps": len(pairs), "total": t3 - t0}
        gray = np.asarray(Image.fromarray((img * 255).round().astype(np.uint8)).convert("L"))
        return Image.fromarray(_style.trim(gray) if trim else gray)

    def _sync(self) -> None:
        if self.device.type == "cuda":
            self.torch.cuda.synchronize()


def stack(lines: list[Image.Image], gap: int = 12) -> Image.Image:
    """Lines one under another, left aligned, on white."""
    width = max(im.width for im in lines)
    height = sum(im.height for im in lines) + gap * (len(lines) - 1)
    page = Image.new("L", (width, height), 255)
    y = 0
    for im in lines:
        page.paste(im, (0, y))
        y += im.height + gap
    return page


def generate_lines(texts: list[str], style_path: str | Path, *, device: str = "auto",
                   steps: int = 50, seed: int = 0, half: bool = False,
                   progress: Callable[[dict], None] | None = None) -> list[Image.Image]:
    """Write each of ``texts`` (line ``i`` uses seed ``seed + i``)."""
    for t in texts:
        _style.check_text(t)
    gen = LineGenerator(device, steps=steps, half=half)
    ref = _style.load_style(style_path)
    out = []
    for i, text in enumerate(texts):
        out.append(gen.write(text, ref, seed=seed + i))
        if progress:
            progress({"line": i + 1, "lines": len(texts), "device": gen.device_label,
                      **{k: round(v, 3) for k, v in gen.timings.items()}})
    return out
