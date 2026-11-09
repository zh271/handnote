"""SDT model plumbing (needs the model extra; no weights, no network)."""

import random
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("torchvision")
pytest.importorskip("einops")

from handnote.model import data, generate, sdt  # noqa: E402


def _char(seed=0):
    rng = np.random.default_rng(seed)
    a = np.column_stack([np.linspace(0.2, 0.8, 30), np.full(30, 0.3) + rng.normal(0, 0.002, 30),
                         np.full(30, 0.5)])
    b = np.column_stack([np.full(25, 0.5), np.linspace(0.2, 0.85, 25), np.full(25, 0.6)])
    c = np.column_stack([np.linspace(0.3, 0.45, 2), np.linspace(0.6, 0.62, 2), [0.5, 0.5]])
    return (a, b, c)


def test_sequence_round_trip():
    strokes = _char()
    seq = data.to_sequence(strokes)
    assert seq.shape[1] == 5 and seq.dtype == np.float32
    assert list(seq[0]) == [0, 0, 1, 0, 0]
    assert int(seq[:, 3].sum()) == 3 and seq[-1, 3] == 1
    assert len(seq) < sum(len(s) for s in strokes)  # simplified
    back = data.from_sequence(seq)
    assert len(back) == 3
    # Same shape up to translation and scale: aspect of the ink box is kept.
    def aspect(arrs):
        allp = np.vstack([a[:, :2] for a in arrs])
        w, h = np.ptp(allp, axis=0)
        return w / h
    assert aspect(back) == pytest.approx(aspect(strokes), rel=0.05)
    jittered = data.to_sequence(strokes, rng=random.Random(1))
    assert jittered.shape[1] == 5 and not np.allclose(jittered[:4], seq[:4])


def test_from_sequence_turns_single_points_into_dots():
    # Origin -> (1, 0) lifts; (1, 1) is written alone and lifts again: a dot.
    seq = np.array([[1, 0, 0, 1, 0], [0, 1, 0, 1, 0], [0, 0, 0, 0, 1]], dtype=float)
    strokes = data.from_sequence(seq)
    assert [len(s) for s in strokes] == [2, 2]
    assert np.allclose(strokes[1][0], [1, 1]) and not np.allclose(strokes[1][1], [1, 1])


def test_style_image_layout():
    img = data.style_image(_char())
    assert img.shape == (64, 64) and img.dtype == np.float32
    ys, xs = np.nonzero(img < 0.8)
    assert xs.min() >= data.STYLE_MARGIN - 1 and ys.min() >= data.STYLE_MARGIN - 1
    assert max(xs.max() - xs.min(), ys.max() - ys.min()) >= data.STYLE_FIT - 3
    assert data.style_subset(40, 15, (0, 1)) == data.style_subset(40, 15, (0, 1))
    assert data.style_subset(40, 15, (0, 1)) != data.style_subset(40, 15, (0, 2))
    assert data.style_subset(10, 15, (0, 0)) == list(range(10))


def test_chaikin_keeps_endpoints():
    pts = np.array([[0, 0], [1, 0], [1, 1]], dtype=float)
    out = data.chaikin(pts, 2)
    assert np.allclose(out[0], pts[0]) and np.allclose(out[-1], pts[-1]) and len(out) > 3


def test_place_centres_on_reference_box():
    strokes = [np.array([[-2.0, -1.0], [2.0, -1.0]]), np.array([[0.0, -1.0], [0.0, 1.0]])]
    box = (0.2, 0.3, 0.6, 0.5)
    placed = generate.place(strokes, box)
    allp = np.vstack(placed)
    (x0, y0), (x1, y1) = allp.min(axis=0), allp.max(axis=0)
    assert (x0 + x1) / 2 == pytest.approx(0.4) and (y0 + y1) / 2 == pytest.approx(0.4)
    assert (x1 - x0) * (y1 - y0) == pytest.approx(0.4 * 0.2, rel=1e-6)
    huge = np.vstack(generate.place(strokes, (0.0, 0.0, 1.0, 1.0), scale=3.0))
    assert huge.min() >= generate.CELL_LO - 1e-9 and huge.max() <= generate.CELL_HI + 1e-9


def test_shade_adds_pressure():
    placed = [np.array([[0.2, 0.5], [0.5, 0.52], [0.8, 0.5]])]
    shaded = generate.shade(placed, None)
    assert len(shaded[0]) > 3 and all(len(p) == 3 and 0 <= p[2] <= 1 for p in shaded[0])


def test_pick_best_retries_missing_strokes():
    calls = []
    # Strokes written per (char, attempt): 江 needs 6, gets 4, then 5, then 6.
    table = {("江", 0): 4, ("江", 1): 5, ("江", 2): 6, ("工", 0): 3, ("口", 0): 1, ("口", 1): 1,
             ("口", 2): 2, ("口", 3): 1}
    need = {"江": 6, "工": 3, "口": 3}

    def write(chars, attempt):
        calls.append((tuple(chars), attempt))
        return [[None] * table[(c, attempt)] for c in chars]

    best = generate.pick_best(["江", "工", "口"], write, lambda c, s: need[c] - len(s), 3)
    assert len(best["江"]) == 6 and len(best["工"]) == 3 and len(best["口"]) == 2
    assert calls[0] == (("江", "工", "口"), 0) and calls[1] == (("江", "口"), 1)
    assert calls[-1] == (("口",), 3)


def test_network_runs_without_cuda():
    source = Path(sdt.__file__).read_text(encoding="utf-8")
    assert ".cuda(" not in source.replace("every hard-coded ``.cuda()``", "")
    torch.manual_seed(0)
    model = sdt.SDTGenerator()
    style = torch.rand(2, 3, 1, 64, 64)
    content = torch.rand(2, 1, 64, 64)
    model.eval()
    out = model.inference(style, content, max_len=4)
    assert out.shape[0] == 2 and out.shape[1] <= 4 and out.shape[2] == 5
    model.train()
    seq = torch.zeros(2, 6, 5)
    seq[:, :, 2] = 1
    seq[:, -1] = torch.tensor([0, 0, 0, 0, 1.0])
    preds = model.decode(style, seq[:, 1:-1], content)
    assert preds.shape == (2, 5, sdt.GMM_OUT)
    loss = sdt.pen_loss(preds, seq[:, 1:], torch.tensor([5.0, 5.0]))
    loss.backward()
    assert torch.isfinite(loss)
    assert model.EmbtoSeq.fc_2.weight.grad is not None


def test_prefix_decoding_matches_full_length():
    """Running the decoders on the prefix equals upstream's full-length pass."""
    torch.manual_seed(1)
    model = sdt.SDTGenerator().eval()
    style = torch.rand(1, 2, 1, 64, 64)
    content = torch.rand(1, 1, 64, 64)
    with torch.no_grad():
        writer, glyph = model._styles(style)
        src = torch.zeros(4, 1, sdt.D_MODEL)
        src[0] = model._content(content)
        src[0] = model.add_position(src[0], step=0)
        src[1] = torch.rand(1, sdt.D_MODEL)
        src[1] = model.add_position(src[1], step=1)
        mask = sdt.causal_mask(4)
        full = model.gly_decoder(model.wri_decoder(src, writer, tgt_mask=mask)[-1], glyph,
                                 tgt_mask=mask)[-1][1]
        pre = model.gly_decoder(model.wri_decoder(src[:2], writer, tgt_mask=mask[:2, :2])[-1],
                                glyph, tgt_mask=mask[:2, :2])[-1][1]
    assert torch.allclose(full, pre, atol=1e-5)
