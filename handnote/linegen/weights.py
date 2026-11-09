"""Download and cache the DiffBrush checkpoint, the SD1.5 VAE and the content glyphs.

Files live in ``~/.cache/handnote/diffbrush/`` (or ``$HANDNOTE_CACHE/diffbrush``):

- ``diffbrush.pt``: the authors' English text-line weights (1.17 GB), trained on
  IAM, which is licensed for research use only. Downloaded from the authors'
  Google Drive at runtime and never shipped.
- ``vae/``: the Stable Diffusion v1.5 VAE (CreativeML OpenRAIL-M) from Hugging Face.
- ``content.npz``: the 16x16 GNU Unifont glyph of each supported letter, the
  "what to write" input. Upstream keeps all of Unifont as a 17 MB pickle; it is
  opened with the numpy-only unpickler and only the needed letters are kept.

If Google Drive cannot be reached, download the checkpoint from the Baidu
Netdisk mirror in the DiffBrush README, save it as ``diffbrush.pt`` in the
cache folder and run fetch again.

Only numpy and the standard library are imported here, so status checks work
without torch.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import numpy as np

from ..model.weights import Progress, WeightsError, _request, load_array_pickle

_DRIVE = "https://drive.usercontent.google.com/download?id={}&export=download&confirm=t"
_HF = "https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/main/vae/{}"
_UPSTREAM = "https://raw.githubusercontent.com/dailenson/DiffBrush/" \
            "da9addc1140bdfec463b2d41a974a0a392f80798/files/unifont.pickle"
MIRROR = "https://pan.baidu.com/s/1awe0z7LEixJhVQ1se0VikA?pwd=7udy"

# Supported characters, in the order of the checkpoint's content tokens (IAM).
LETTERS = " _!\"#&'()*+,-./0123456789:;?ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

CHECKPOINT = "diffbrush.pt"
CONTENT = "content.npz"
# name -> (url, size, sha256)
FILES = {
    CHECKPOINT: (_DRIVE.format("1EWzBmLtnQ42cTf3k_CYQ-nF3RXCb35I6"), 1165546469,
                 "ed0d6059a3b0a68c18476c02ba2410d607fc478eaead597faa2483406c0082fa"),
    "vae/config.json": (_HF.format("config.json"), 547,
                        "786a7d21647ddea6a04b9675c03d3cb45e90a2f3c6da5fbda2c54ade040036de"),
    "vae/diffusion_pytorch_model.safetensors": (
        _HF.format("diffusion_pytorch_model.safetensors"), 334643276,
        "a2b5134f4dbc140d9c11f11cba3233099e00af40f262f136c691fb7d38d2194c"),
}
UNIFONT = (_UPSTREAM, 16978300,
           "0804979068f0d169b343fbe0fe8d7ff478165d07a671fcf52e20f625db8e7f9f")


def cache_dir() -> Path:
    root = Path(os.environ.get("HANDNOTE_CACHE") or Path.home() / ".cache" / "handnote")
    return root / "diffbrush"


def status() -> dict:
    """Which files are present (by size; hashes are checked when downloading)."""
    folder = cache_dir()
    out = {"folder": str(folder)}
    for name, (_, size, _) in FILES.items():
        path = folder / name
        out[name] = path.is_file() and path.stat().st_size == size
    out[CONTENT] = (folder / CONTENT).is_file()
    return out


def ready() -> bool:
    return all(v for k, v in status().items() if k != "folder")


def require() -> Path:
    """The cache folder, or :class:`WeightsError` if anything is missing."""
    if not ready():
        raise WeightsError(f"DiffBrush files not found in {cache_dir()}; run "
                           "`python -m handnote line fetch`")
    return cache_dir()


def load_content() -> np.ndarray:
    """``(len(LETTERS), 16, 16)`` float32 glyphs, 1 = ink."""
    path = require() / CONTENT
    with np.load(path) as npz:
        glyphs = npz["glyphs"]
    if glyphs.shape[0] != len(LETTERS):
        raise WeightsError("content cache is inconsistent; delete it and fetch again")
    return glyphs.astype(np.float32)


def fetch(progress: Progress | None = None, *, timeout: float = 60.0) -> dict:
    """Download whatever is missing; returns :func:`status`."""
    folder = cache_dir()
    for name, spec in FILES.items():
        if not status()[name]:
            _download(name, *spec, folder / name, timeout, progress)
    if not status()[CONTENT]:
        raw = folder / "unifont.pickle"
        if not (raw.is_file() and raw.stat().st_size == UNIFONT[1]):
            _download("unifont", *UNIFONT, raw, timeout, progress)
        glyphs = content_from_unifont(raw.read_bytes())
        np.savez_compressed(folder / CONTENT, glyphs=glyphs)
        raw.unlink()
    return status()


def content_from_unifont(data: bytes) -> np.ndarray:
    """Pick the :data:`LETTERS` glyphs out of upstream's ``unifont.pickle``."""
    symbols = load_array_pickle(data)
    if not isinstance(symbols, list):
        raise WeightsError("unexpected unifont data")
    table = {}
    for sym in symbols:
        if isinstance(sym, dict) and "idx" in sym and "mat" in sym:
            table[int(sym["idx"][0])] = np.asarray(sym["mat"], dtype=np.uint8)
    missing = [c for c in LETTERS if ord(c) not in table]
    if missing:
        raise WeightsError(f"unifont data lacks {missing!r}")
    return np.stack([table[ord(c)] for c in LETTERS])


def _download(label: str, url: str, size: int, sha256: str, target: Path, timeout: float,
              progress: Progress | None) -> None:
    """Stream ``url`` to ``target``, resuming dropped connections; checks size and hash."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    done = 0
    retries = 5
    with open(part, "wb") as out:
        for attempt in range(retries):
            headers = {"Range": f"bytes={done}-"} if done else None
            try:
                with _request(url, headers, timeout) as resp:
                    if done and resp.status != 206:
                        raise WeightsError("server cannot resume the download")
                    while chunk := resp.read(1 << 20):
                        out.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        if progress:
                            progress(label, done, size)
                break
            except OSError as exc:  # connection dropped mid-stream: resume
                if attempt == retries - 1:
                    raise WeightsError(f"download of {label} failed ({exc}); see {MIRROR} "
                                       f"and put the files into {cache_dir()}") from exc
                time.sleep(2 ** attempt)
    if done != size or digest.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise WeightsError(f"{label} download is incomplete or corrupt; try again")
    part.replace(target)
