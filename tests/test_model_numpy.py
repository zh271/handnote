"""Model helpers that need no torch: pen pressure and the weight cache."""

import io
import pickle
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from handnote.model import pressure, weights


def _stroke(n=40, light_ends=True, seed=0):
    """A gently curved stroke with pressure light at both ends (or constant)."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0, 1, n)
    x = 0.2 + 0.6 * t
    y = 0.5 + 0.05 * np.sin(3 * t) + rng.normal(0, 0.001, n)
    p = 0.3 + 0.35 * np.sin(np.pi * t) if light_ends else np.full(n, 0.5)
    return np.column_stack([x, y, p])


def test_resample_keeps_shape_and_spacing():
    s = _stroke()
    r = pressure.resample(s)
    assert np.allclose(r[0], s[0]) and np.allclose(r[-1], s[-1])
    gaps = np.hypot(*np.diff(r[:, :2], axis=0).T)
    assert gaps.max() <= pressure.STEP * 1.01 and gaps.std() < 1e-3
    # Every resampled point lies on the original curve.
    assert np.abs(np.interp(r[:, 0], s[:, 0], s[:, 1]) - r[:, 1]).max() < 2e-3


def test_learned_pressure_is_light_at_both_ends():
    strokes = [_stroke(seed=i) for i in range(30)]
    model = pressure.fit(strokes)
    assert model is not None
    again = pressure.PressureModel.from_json(model.to_json())
    p = again.predict(pressure.resample(_stroke(seed=99)[:, :2]))
    n = len(p)
    assert p[: n // 10].mean() < p[n // 3: 2 * n // 3].mean() - 0.1
    assert p[-n // 10:].mean() < p[n // 3: 2 * n // 3].mean() - 0.1
    assert ((p >= 0) & (p <= 1)).all()


def test_no_pressure_in_capture_falls_back_to_rule():
    assert pressure.fit([_stroke(light_ends=False) for _ in range(5)]) is None
    assert pressure.fit([_stroke()[:, :2] for _ in range(5)]) is None
    dense = pressure.resample(_stroke()[:, :2])
    p = pressure.fallback(dense)
    n = len(p)
    assert ((p >= 0) & (p <= 1)).all()
    assert p[0] < p[n // 2] and p[-1] < p[n // 2]
    fast = pressure.fallback(dense, np.full(n, 3.0)), pressure.fallback(dense, np.full(n, 1.0))
    assert fast[0].mean() <= fast[1].mean()


def test_array_unpickler_refuses_code():
    arrays = {"清": np.zeros((64, 64), np.uint8)}
    assert weights.load_array_pickle(pickle.dumps(arrays))["清"].shape == (64, 64)
    assert weights.load_array_pickle(pickle.dumps("一二三")) == "一二三"

    class Evil:
        def __reduce__(self):
            return (print, ("pwned",))

    with pytest.raises(pickle.UnpicklingError):
        weights.load_array_pickle(pickle.dumps(Evil()))


class _RangeHandler(BaseHTTPRequestHandler):
    payload = b""

    def do_GET(self):  # noqa: N802
        start, _, end = self.headers["Range"].removeprefix("bytes=").partition("-")
        start = int(start)
        end = min(int(end) if end else len(self.payload) - 1, len(self.payload) - 1)
        body = self.payload[start:end + 1]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(self.payload)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_range_file_reads_zip_members():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.bin", np.random.default_rng(0).bytes(300_000))
        zf.writestr("data/wanted.txt", "你好")
    _RangeHandler.payload = buffer.getvalue()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/x.zip"
        with weights.RangeFile(url) as remote, zipfile.ZipFile(remote) as zf:
            assert zf.read("data/wanted.txt").decode() == "你好"
            assert remote.size == len(_RangeHandler.payload)
    finally:
        server.shutdown()


def test_content_from_local_pickles(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDNOTE_CACHE", str(tmp_path))
    folder = weights.cache_dir()
    folder.mkdir(parents=True)
    glyphs = {ch: np.full((64, 64), i, np.uint8) for i, ch in enumerate("一丁七")}
    (folder / "Chinese_content.pkl").write_bytes(pickle.dumps(glyphs))
    (folder / "character_dict.pkl").write_bytes(pickle.dumps("一丁七万"))
    assert not weights.ready()
    weights._fetch_content(folder, 5, None)
    chars, arrays = weights.load_content()
    assert chars == "一丁七" and arrays.shape == (3, 64, 64) and arrays[2, 0, 0] == 2
    assert weights.status()["content"] and not weights.status()["checkpoint"]
    with pytest.raises(weights.WeightsError):
        weights.checkpoint_path()
