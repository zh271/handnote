"""DiffBrush line generation plumbing (linegen extra; no weights, no network).

A small random-init UNet stands in for the 291M-parameter checkpoint and a tiny
upsampling module stands in for the SD1.5 VAE.
"""

import io
from types import SimpleNamespace

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("torchvision")
pytest.importorskip("einops")
pytest.importorskip("diffusers")

from torch import nn  # noqa: E402

from handnote import cli  # noqa: E402
from handnote.linegen import available, generate, style  # noqa: E402
from handnote.linegen.device import pick_device  # noqa: E402
from handnote.linegen.generate import LineGenerator, ddim_times, stack  # noqa: E402
from handnote.linegen.weights import LETTERS  # noqa: E402


def _small_unet():
    from handnote.linegen.net.unet import UNetModel

    torch.manual_seed(0)
    # Mix_TR hardcodes 512-wide MLPs, so context_dim stays 512; shrink the rest.
    return UNetModel(in_channels=4, model_channels=32, out_channels=4, num_res_blocks=1,
                     attention_resolutions=(1, 1), channel_mult=(1, 1), num_heads=4,
                     context_dim=512, nb_classes=4)


class _FakeVAE(nn.Module):
    """``decode`` maps a (1, 4, 8, 128) latent to a (1, 3, 64, 1024) image in [-1, 1]."""

    def __init__(self):
        super().__init__()
        torch.manual_seed(1)
        self.conv = nn.Conv2d(4, 3, 3, padding=1)
        self.up = nn.Upsample(scale_factor=8, mode="nearest")

    def decode(self, x):
        return SimpleNamespace(sample=torch.tanh(self.up(self.conv(x))))


@pytest.fixture(scope="module")
def gen():
    return LineGenerator("cpu", steps=2, unet=_small_unet(), vae=_FakeVAE(),
                         content=np.zeros((len(LETTERS), 16, 16), np.float32))


@pytest.fixture(scope="module")
def ref():
    rng = np.random.default_rng(0)
    return style.prepare_style(np.where(rng.random((64, 300)) < 0.1, 0, 255).astype(np.uint8))


def _png(im):
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_available():
    assert available()


def test_write_line(gen, ref):
    calls = []
    im = gen.write("Hello world.", ref, seed=0, progress=lambda i, n: calls.append((i, n)))
    assert im.mode == "L" and im.height == 64 and 0 < im.width <= 1024
    assert calls == [(1, 2), (2, 2)]
    assert gen.timings["steps"] == 2 and gen.timings["total"] > 0


def test_write_is_deterministic(gen, ref):
    a = gen.write("Hello world.", ref, seed=3, trim=False)
    b = gen.write("Hello world.", ref, seed=3, trim=False)
    c = gen.write("Hello world.", ref, seed=4, trim=False)
    assert a.tobytes() == b.tobytes()
    assert a.tobytes() != c.tobytes()


def test_write_untrimmed_is_full_canvas(gen, ref):
    im = gen.write("abc", ref, seed=0, trim=False)
    assert im.size == (1024, 64)


def test_write_rejects_chinese(gen, ref):
    with pytest.raises(ValueError, match="你"):
        gen.write("Hello 你好", ref)


def test_half_on_cpu_is_refused():
    with pytest.raises(ValueError):
        LineGenerator("cpu", half=True, unet=nn.Identity(), vae=nn.Identity(),
                      content=np.zeros((len(LETTERS), 16, 16)))


def test_generate_lines_checks_text_before_loading(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDNOTE_CACHE", str(tmp_path))  # no weights here
    with pytest.raises(ValueError, match="不"):
        generate.generate_lines(["fine", "不行"], tmp_path, device="cpu")


def test_ddim_times():
    pairs = ddim_times(50)
    assert len(pairs) == 50
    # As upstream: linspace(-1, 999, steps + 1), so 50 steps end at (19, -1).
    assert pairs[0] == (999, 979) and pairs[-1] == (19, -1)
    assert ddim_times(1000)[-1] == (0, -1)
    assert all(t > t_next for t, t_next in pairs)
    assert all(a[1] == b[0] for a, b in zip(pairs, pairs[1:], strict=False))


def test_pick_device():
    device, label = pick_device("cpu")
    assert device.type == "cpu" and label.startswith("cpu")
    with pytest.raises(ValueError):
        pick_device("tpu")


def test_stack():
    from PIL import Image

    lines = [Image.new("L", (300, 64), 0), Image.new("L", (500, 40), 0)]
    page = stack(lines, gap=10)
    assert page.mode == "L" and page.size == (500, 114)
    assert page.getpixel((400, 10)) == 255  # right of the short first line: paper
    assert page.getpixel((400, 80)) == 0


def test_unet_construction_does_not_download(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access during construction")

    import torchvision.models._api as tv_api

    monkeypatch.setattr(torch.hub, "load_state_dict_from_url", boom)
    monkeypatch.setattr(tv_api, "load_state_dict_from_url", boom, raising=False)
    monkeypatch.setattr("urllib.request.urlopen", boom)
    unet = _small_unet()
    with torch.no_grad():
        ctx = unet.encode_context(torch.ones(1, 1, 64, 600), torch.ones(1, 5, 16, 16))
    assert ctx.shape == (1, 5, 512)


def test_cli_rejects_chinese_without_weights(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDNOTE_CACHE", str(tmp_path))

    def no_load(*a, **k):
        raise AssertionError("text must be checked before loading the model")

    monkeypatch.setattr(generate, "generate_lines", no_load)
    with pytest.raises(SystemExit) as exc:
        cli.main(["line", "generate", "--style", str(tmp_path), "--text", "Hello 你好",
                  "-o", str(tmp_path / "out.png")])
    assert "你" in str(exc.value.code) and "English" in str(exc.value.code)
    assert not (tmp_path / "out.png").exists()


def test_cli_empty_text(tmp_path):
    with pytest.raises(SystemExit, match="nothing to write"):
        cli.main(["line", "generate", "--style", str(tmp_path), "--text", "  ",
                  "-o", str(tmp_path / "out.png")])
