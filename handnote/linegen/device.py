"""Pick the torch device: CUDA/ROCm, DirectML, or CPU."""

from __future__ import annotations

import importlib.util


def pick_device(name: str = "auto"):
    """``(device, label)`` for ``auto``, ``cuda``, ``dml`` or ``cpu``.

    ``auto`` tries CUDA first (AMD GPUs show up as CUDA in ROCm builds of
    torch), then DirectML (``pip install torch-directml``, Windows), then CPU.
    """
    import torch

    name = name.lower()
    if name in ("auto", "cuda", "rocm") and torch.cuda.is_available():
        backend = "rocm" if getattr(torch.version, "hip", None) else "cuda"
        return torch.device("cuda"), f"{backend}:{torch.cuda.get_device_name(0)}"
    if name in ("cuda", "rocm"):
        raise RuntimeError(f"torch {torch.__version__} sees no CUDA/ROCm GPU")
    if name in ("auto", "dml", "directml") and importlib.util.find_spec("torch_directml"):
        import torch_directml

        return torch_directml.device(), f"directml:{torch_directml.device_name(0)}"
    if name in ("dml", "directml"):
        raise RuntimeError("torch-directml is not installed (pip install torch-directml)")
    if name not in ("auto", "cpu"):
        raise ValueError(f"unknown device {name!r}; use auto, cuda, dml or cpu")
    return torch.device("cpu"), f"cpu:{torch.get_num_threads()} threads"
