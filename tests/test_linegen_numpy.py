"""DiffBrush line generation helpers that need only numpy and Pillow (no torch)."""

import pickle

import numpy as np
import pytest
from PIL import Image

from handnote.linegen import bench, style, weights
from handnote.linegen.weights import LETTERS
from handnote.model.weights import WeightsError

# -- text checks -------------------------------------------------------------------


def test_check_text_accepts_iam_letters():
    assert style.check_text(LETTERS) == LETTERS
    text = "The quick brown fox, \"jumps\" over 12 lazy dogs?"
    assert style.check_text(text) == text


def test_check_text_rejects_chinese_naming_the_char():
    with pytest.raises(ValueError, match="你"):
        style.check_text("Hello 你好")


def test_check_text_rejects_blank():
    with pytest.raises(ValueError):
        style.check_text("   ")


def test_unsupported_keeps_order_and_dedups():
    assert style.unsupported("ab你c好你$好~") == ["你", "好", "$", "~"]
    assert style.unsupported("plain text.") == []


# -- wrapping ----------------------------------------------------------------------

_WORDS = ("lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
          "tempor incididunt ut labore et dolore magna aliqua ut enim ad minim veniam "
          "quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo")


def test_wrap_empty():
    assert style.wrap("") == []
    assert style.wrap("   \n\n ") == []


@pytest.mark.parametrize("width", [10, 25, 40, 60, 70])
def test_wrap_respects_width_and_keeps_words(width):
    lines = style.wrap(_WORDS, width)
    assert lines and all(len(line) <= width for line in lines)
    assert "".join(lines).replace(" ", "") == _WORDS.replace(" ", "")
    if width >= max(len(w) for w in _WORDS.split()):
        assert " ".join(lines).split() == _WORDS.split()


def test_wrap_never_exceeds_70():
    lines = style.wrap(_WORDS * 3, 500)
    assert lines and max(len(line) for line in lines) <= style.MAX_CHARS


def test_wrap_two_lines_are_balanced():
    text = _WORDS[:85].rsplit(" ", 1)[0]
    assert len(style._greedy(text, 60)) == 2
    lines = style.wrap(text, 60)
    assert len(lines) == 2
    assert abs(len(lines[0]) - len(lines[1])) <= 12  # a word or so, not 60 vs 20


def test_wrap_balanced_never_adds_lines():
    for n in range(20, len(_WORDS), 7):
        text = _WORDS[:n]
        assert len(style.wrap(text, 60)) == len(style._greedy(text, 60))


def test_wrap_splits_long_words():
    lines = style.wrap("a " + "x" * 25 + " b", 10)
    assert all(len(line) <= 10 for line in lines)
    assert "".join(lines).replace(" ", "") == "a" + "x" * 25 + "b"


def test_wrap_paragraphs_start_new_lines():
    lines = style.wrap("first paragraph\nsecond one\n\nthird", 60)
    assert lines == ["first paragraph", "second one", "third"]


# -- style references --------------------------------------------------------------


def _line(h, w, value=255):
    return np.full((h, w), value, np.uint8)


def _check_style(img):
    assert img.dtype == np.float32
    assert img.shape[0] == style.HEIGHT
    assert style.MIN_WIDTH < img.shape[1] <= style.MAX_WIDTH
    assert img.min() >= 0.0 and img.max() <= 1.0


def test_prepare_style_wide_line_is_kept_then_capped():
    gray = _line(64, 700, 0)
    out = style.prepare_style(gray)
    _check_style(out)
    assert out.shape[1] == 700
    _check_style(style.prepare_style(_line(64, 3000)))
    assert style.prepare_style(_line(64, 3000)).shape[1] == style.MAX_WIDTH


def test_prepare_style_tiles_narrow_line():
    gray = _line(64, 100)
    gray[20:40, 10:90] = 0
    out = style.prepare_style(gray)
    _check_style(out)
    assert out.shape[1] > 512
    # Ink repeats: the second tile starts after the original plus a gap.
    assert (out[:, :100] < 0.5).sum() == (gray < 128).sum()
    assert (out[:, 100:].min()) < 0.5


def test_prepare_style_scales_tall_line():
    gray = _line(256, 2400)
    out = style.prepare_style(gray)
    _check_style(out)
    assert out.shape[1] == 600  # 2400 * 64 / 256
    tall = style.prepare_style(_line(400, 120))  # 19 px wide after scaling, then tiled
    _check_style(tall)


@pytest.mark.parametrize("w", [1, 64, 511, 512, 513, 1024, 1025])
def test_prepare_style_width_edges(w):
    _check_style(style.prepare_style(_line(64, w, 128)))


def _save(path, w, h):
    Image.new("L", (w, h), 255).save(path)
    return path


def test_pick_reference_file_and_folder(tmp_path):
    f = _save(tmp_path / "only.png", 100, 64)
    assert style.pick_reference(f) == f

    folder = tmp_path / "refs"
    folder.mkdir()
    _save(folder / "a.png", 300, 64)      # 300 wide at 64 high
    _save(folder / "b.jpg", 400, 32)      # 800 wide at 64 high: first wide enough
    _save(folder / "c.png", 2000, 64)     # wider still, but later by name
    (folder / "notes.txt").write_text("not an image")
    assert style.pick_reference(folder) == folder / "b.jpg"


def test_pick_reference_falls_back_to_widest(tmp_path):
    _save(tmp_path / "a.png", 300, 64)
    _save(tmp_path / "b.png", 900, 128)   # 450 at 64 high
    _save(tmp_path / "c.png", 200, 64)
    assert style.pick_reference(tmp_path) == tmp_path / "b.png"


def test_pick_reference_empty_folder(tmp_path):
    (tmp_path / "x.txt").write_text("")
    with pytest.raises(ValueError):
        style.pick_reference(tmp_path)


def test_load_style(tmp_path):
    _save(tmp_path / "a.png", 300, 128)
    _check_style(style.load_style(tmp_path))


def test_trim():
    gray = _line(64, 1024)
    gray[10:20, 100:200] = 0
    out = style.trim(gray)
    assert out.shape == (64, 208)
    blank = style.trim(_line(64, 1024))
    assert blank.shape == (64, 8)
    gray[:, 1020] = 0
    assert style.trim(gray).shape == (64, 1024)


# -- weights -----------------------------------------------------------------------


def _unifont(letters):
    syms = [{"idx": np.array([ord(c)]), "mat": np.eye(16, dtype=bool)} for c in letters]
    syms.append({"idx": np.array([0x4E00]), "mat": np.zeros((16, 16), bool)})
    return pickle.dumps(syms)


def test_content_from_unifont():
    glyphs = weights.content_from_unifont(_unifont(LETTERS))
    assert glyphs.shape == (len(LETTERS), 16, 16) == (80, 16, 16)
    assert glyphs.dtype == np.uint8 and glyphs.sum() == 80 * 16


def test_content_from_unifont_missing_letter():
    with pytest.raises(WeightsError, match="'Q'"):
        weights.content_from_unifont(_unifont(LETTERS.replace("Q", "")))


def test_content_from_unifont_refuses_other_objects():
    with pytest.raises(Exception):  # noqa: B017 - pickle refuses before anything runs
        weights.content_from_unifont(pickle.dumps([{"idx": np.array([65]), "mat": print}]))
    with pytest.raises(WeightsError):
        weights.content_from_unifont(pickle.dumps({"not": "a list"}))


def test_status_with_empty_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDNOTE_CACHE", str(tmp_path))
    info = weights.status()
    assert info["folder"] == str(tmp_path / "diffbrush")
    assert not any(v for k, v in info.items() if k != "folder")
    assert not weights.ready()
    with pytest.raises(WeightsError, match="line fetch"):
        weights.require()
    with pytest.raises(WeightsError):
        weights.load_content()


def test_status_checks_sizes(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDNOTE_CACHE", str(tmp_path))
    folder = tmp_path / "diffbrush"
    (folder / "vae").mkdir(parents=True)
    (folder / "vae" / "config.json").write_bytes(b"x" * weights.FILES["vae/config.json"][1])
    (folder / weights.CHECKPOINT).write_bytes(b"truncated")
    np.savez_compressed(folder / weights.CONTENT, glyphs=np.zeros((80, 16, 16), np.uint8))
    info = weights.status()
    assert info["vae/config.json"] and info[weights.CONTENT]
    assert not info[weights.CHECKPOINT]
    assert not weights.ready()


def test_load_content(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDNOTE_CACHE", str(tmp_path))
    monkeypatch.setattr(weights, "ready", lambda: True)
    folder = tmp_path / "diffbrush"
    folder.mkdir()
    np.savez_compressed(folder / weights.CONTENT, glyphs=np.ones((80, 16, 16), np.uint8))
    out = weights.load_content()
    assert out.shape == (80, 16, 16) and out.dtype == np.float32
    np.savez_compressed(folder / weights.CONTENT, glyphs=np.ones((79, 16, 16), np.uint8))
    with pytest.raises(WeightsError):
        weights.load_content()


# -- bench arithmetic --------------------------------------------------------------

_FLOPS = {"context": 1e9, "step": 1e10, "decode": 5e10}


def test_per_line():
    assert bench.per_line(_FLOPS, 50) == pytest.approx(1e9 + 50 * 1e10 + 5e10)
    assert bench.per_line(_FLOPS, 0) == pytest.approx(5.1e10)


def test_estimate():
    rows = bench.estimate(_FLOPS, 50, lines_per_page=10)
    assert len(rows) == 2 * len(bench.REFERENCE)
    work = bench.per_line(_FLOPS, 50)
    for row in rows:
        fp32, fp16 = bench.REFERENCE[row["device"]]
        peak = fp32 if row["precision"] == "fp32" else fp16
        sec = work / (peak * 1e12 * bench.EFFICIENCY)
        assert row["sec_per_line"] == pytest.approx(sec, abs=0.006)
        assert row["sec_per_page"] == pytest.approx(sec * 10, abs=0.06)
    by = {(r["device"], r["precision"]): r for r in rows}
    for name in bench.REFERENCE:
        assert by[(name, "fp16")]["sec_per_line"] <= by[(name, "fp32")]["sec_per_line"]
