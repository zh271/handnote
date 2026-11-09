"""Download and cache the SDT checkpoint and its content glyphs.

Files live in ``~/.cache/handnote/sdt/`` (or ``$HANDNOTE_CACHE/sdt``):

- ``checkpoint-iter199999.pth``: the authors' Chinese SDT weights (273 MB),
  trained on CASIA-OLHWDB, which is licensed for research use only. They are
  downloaded from the authors' Google Drive at runtime and never shipped.
- ``content.npz`` + ``chars.txt``: the 64x64 standard glyph SDT reads as the
  "what to write" input for each of the 6763 GB2312 characters. Upstream
  keeps them as pickles inside a 5.66 GB dataset zip; only those two members
  are read, by HTTP range requests, and the pickles are opened with an
  unpickler that accepts numpy arrays and nothing else.

If Google Drive cannot be reached, put ``checkpoint-iter199999.pth``,
``Chinese_content.pkl`` and ``character_dict.pkl`` (from the Baidu Netdisk
mirror in the SDT README) into the cache folder and run fetch again.
"""

from __future__ import annotations

import hashlib
import io
import os
import pickle
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

import numpy as np

_DRIVE = "https://drive.usercontent.google.com/download?id={}&export=download&confirm=t"
CHECKPOINT = "checkpoint-iter199999.pth"
CHECKPOINT_ID = "1OAjW463bVgreCuDKT_aT8a94Cx-53ZKu"
CHECKPOINT_SIZE = 273587031
CHECKPOINT_SHA256 = "e23ae03af8099c9fa09bc4f1b80dad3f3457cc73e651e4f6e23ff6aa1e41aa89"
DATASET_ID = "1-1XO8Mluy0Z9DVCpviWUPKfqZsHJ4f7z"  # CASIA_CHINESE.zip
DATASET_PREFIX = "home/mdisk/daigang/StyleWriting/data/CASIA_CHINESE/"
CONTENT, CHARS = "content.npz", "chars.txt"
MIRROR = "https://pan.baidu.com/s/1RNQSRhBAEFPe2kFXsHZfLA?pwd=xu9u"
USER_AGENT = "handnote"

Progress = Callable[[str, int, int], None]


class WeightsError(RuntimeError):
    """The model files are missing and could not be downloaded."""


def cache_dir() -> Path:
    root = Path(os.environ.get("HANDNOTE_CACHE") or Path.home() / ".cache" / "handnote")
    return root / "sdt"


def status() -> dict:
    """Which model files are present."""
    folder = cache_dir()
    ckpt = folder / CHECKPOINT
    return {"folder": str(folder),
            "checkpoint": ckpt.is_file() and ckpt.stat().st_size == CHECKPOINT_SIZE,
            "content": (folder / CONTENT).is_file() and (folder / CHARS).is_file()}


def ready() -> bool:
    s = status()
    return s["checkpoint"] and s["content"]


def checkpoint_path() -> Path:
    path = cache_dir() / CHECKPOINT
    if not status()["checkpoint"]:
        raise WeightsError(f"SDT weights not found in {path.parent}; run "
                           "`python -m handnote hanzi model-fetch`")
    return path


def load_content() -> tuple[str, np.ndarray]:
    """``(chars, glyphs)``: glyph ``i`` is a 64x64 uint8 image of ``chars[i]``."""
    folder = cache_dir()
    if not status()["content"]:
        raise WeightsError(f"SDT content glyphs not found in {folder}; run "
                           "`python -m handnote hanzi model-fetch`")
    chars = (folder / CHARS).read_text(encoding="utf-8")
    with np.load(folder / CONTENT) as npz:
        glyphs = npz["glyphs"]
    if len(chars) != len(glyphs):
        raise WeightsError("content cache is inconsistent; delete it and fetch again")
    return chars, glyphs


# -- fetching -----------------------------------------------------------------------
def fetch(progress: Progress | None = None, *, timeout: float = 60.0) -> dict:
    """Make sure the checkpoint and content glyphs are cached; returns :func:`status`."""
    folder = cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if not status()["content"]:
        _fetch_content(folder, timeout, progress)
    if not status()["checkpoint"]:
        _fetch_checkpoint(folder, timeout, progress)
    return status()


RETRIES = 5


def _request(url: str, headers: dict | None = None, timeout: float = 60.0):
    """Open ``url``, retrying dropped connections (proxies reset them often)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(RETRIES):
        try:
            return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - fixed https URL
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == RETRIES - 1:
                raise WeightsError(_failed(exc)) from exc
        except (urllib.error.URLError, OSError) as exc:
            if attempt == RETRIES - 1:
                raise WeightsError(_failed(exc)) from exc
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def _failed(exc: Exception) -> str:
    return (f"download failed ({exc}); see {MIRROR} for a mirror and put the files "
            f"into {cache_dir()}")


def _fetch_checkpoint(folder: Path, timeout: float, progress: Progress | None) -> None:
    target = folder / CHECKPOINT
    part = target.with_suffix(".part")
    digest = hashlib.sha256()
    done = 0
    url = _DRIVE.format(CHECKPOINT_ID)
    with open(part, "wb") as out:
        for attempt in range(RETRIES):
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
                            progress("checkpoint", done, CHECKPOINT_SIZE)
                break
            except OSError as exc:  # connection dropped mid-stream: resume
                if attempt == RETRIES - 1:
                    raise WeightsError(_failed(exc)) from exc
                time.sleep(2 ** attempt)
    if done != CHECKPOINT_SIZE or digest.hexdigest() != CHECKPOINT_SHA256:
        part.unlink(missing_ok=True)
        raise WeightsError("SDT checkpoint download is incomplete or corrupt; try again")
    part.replace(target)


def _fetch_content(folder: Path, timeout: float, progress: Progress | None) -> None:
    local = {name: folder / name for name in ("Chinese_content.pkl", "character_dict.pkl")}
    if all(p.is_file() for p in local.values()):
        raw = {name: p.read_bytes() for name, p in local.items()}
    else:
        if progress:
            progress("content", 0, 2)
        with RangeFile(_DRIVE.format(DATASET_ID), timeout=timeout) as remote, \
                zipfile.ZipFile(remote) as archive:
            raw = {}
            for i, name in enumerate(local, 1):
                raw[name] = archive.read(DATASET_PREFIX + name)
                if progress:
                    progress("content", i, 2)
    content = load_array_pickle(raw["Chinese_content.pkl"])
    chars = load_array_pickle(raw["character_dict.pkl"])
    if not isinstance(content, dict) or not isinstance(chars, str):
        raise WeightsError("unexpected SDT content data")
    order = "".join(ch for ch in chars if ch in content)
    glyphs = np.stack([np.asarray(content[ch], dtype=np.uint8) for ch in order])
    if glyphs.shape[1:] != (64, 64):
        raise WeightsError(f"unexpected content glyph shape {glyphs.shape}")
    np.savez_compressed(folder / CONTENT, glyphs=glyphs)
    (folder / CHARS).write_text(order, encoding="utf-8")


# -- safe unpickling ----------------------------------------------------------------
_ALLOWED = {
    ("numpy.core.multiarray", "_reconstruct"),
    ("numpy._core.multiarray", "_reconstruct"),
    ("numpy.core.numeric", "_frombuffer"),  # newer numpy, pickle protocol 5
    ("numpy._core.numeric", "_frombuffer"),
    ("numpy", "ndarray"),
    ("numpy", "dtype"),
}


class _ArrayUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str):
        if (module, name) not in _ALLOWED:
            raise pickle.UnpicklingError(f"refusing to load {module}.{name}")
        if module.startswith("numpy.core"):
            module = module.replace("numpy.core", "numpy._core", 1)
        return super().find_class(module, name)


def load_array_pickle(data: bytes):
    """Unpickle plain containers of numpy arrays; anything else is refused."""
    return _ArrayUnpickler(io.BytesIO(data)).load()


# -- remote zip ---------------------------------------------------------------------
class RangeFile(io.RawIOBase):
    """Read-only, seekable view of a remote file through HTTP range requests.

    Enough for :class:`zipfile.ZipFile` to read a few members of a large
    archive without downloading it.
    """

    BLOCK = 1 << 16

    def __init__(self, url: str, *, timeout: float = 60.0):
        super().__init__()
        self.url, self.timeout = url, timeout
        self.pos = 0
        self._cache: tuple[int, bytes] = (0, b"")
        with _request(url, {"Range": "bytes=0-0"}, timeout) as resp:
            total = resp.headers.get("Content-Range", "").rpartition("/")[2]
            if resp.status != 206 or not total.isdigit():
                raise WeightsError("server does not support range requests")
            self.size = int(total)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        if n <= 0:
            return b""
        start, data = self._cache
        if not (start <= self.pos and self.pos + n <= start + len(data)):
            end = min(self.size, self.pos + max(n, self.BLOCK)) - 1
            for attempt in range(RETRIES):
                try:
                    with _request(self.url, {"Range": f"bytes={self.pos}-{end}"},
                                  self.timeout) as resp:
                        data = resp.read()
                    break
                except OSError as exc:  # dropped while reading the body
                    if attempt == RETRIES - 1:
                        raise WeightsError(_failed(exc)) from exc
                    time.sleep(2 ** attempt)
            if len(data) != end - self.pos + 1:
                raise WeightsError("short read from range request")
            start = self.pos
            self._cache = (start, data)
        out = data[self.pos - start:self.pos - start + n]
        self.pos += len(out)
        return out

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)
