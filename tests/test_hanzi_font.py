"""Hanzi capture format, stroke check, font building and CJK merging (toy data)."""

import numpy as np
import pytest
from fontTools.ttLib import TTFont

from handnote import hanzi_data
from handnote.capture import CaptureError
from handnote.hanzi_data import HanziData, gb2312_level1
from handnote.hanzi_font import RECOMMENDED, build_hanzi_font, match_strokes, normalize_hanzi
from handnote.opentype import read_alternates
from handnote.symbols import merge_fonts

# Reference strokes in makemeahanzi's frame after the y flip: (x, y) in 0..1024,
# y down. Each helper returns one median line.


def _h(x0, x1, y):
    return [[x0, y], [(x0 + x1) // 2, y], [x1, y]]


def _v(x, y0, y1):
    return [[x, y0], [x, (y0 + y1) // 2], [x, y1]]


WATER = [_v(150, 200, 260), _v(130, 450, 510), [[110, 820], [210, 700]]]   # 氵
WORK = [_h(200, 800, 250), _v(500, 250, 800), _h(150, 850, 800)]          # 工
BLUE = [_h(250, 750, 150), _h(300, 700, 300), _v(500, 150, 500),
        _h(200, 800, 650)]                                               # 青 (toy)


def _squeeze(strokes, x0, x1, y0=0, y1=1024):
    """Map a full-cell drawing into the box [x0, x1] x [y0, y1]."""
    return [[[x0 + x * (x1 - x0) // 1024, y0 + y * (y1 - y0) // 1024] for x, y in s]
            for s in strokes]


def toy_data() -> HanziData:
    chars = {
        "工": {"d": "", "m": [None] * 3, "s": WORK},
        "氵": {"d": "", "m": [None] * 3, "s": WATER},
        "青": {"d": "", "m": [None] * 4, "s": BLUE},
        "清": {"d": "⿰氵青", "m": [[0]] * 3 + [[1]] * 4,
               "s": WATER + _squeeze(BLUE, 360, 1000)},
        "江": {"d": "⿰氵工", "m": [[0]] * 3 + [[1]] * 3,
               "s": WATER + _squeeze(WORK, 380, 1000)},
    }
    return HanziData.from_json({"version": 1, "chars": chars})


def written(data, ch, jitter=0.0, seed=0, width=0.05):
    rng = np.random.default_rng(seed)
    out = []
    for med in data.get(ch).medians:
        pts = [[float(u + rng.normal(0, jitter)), float(v + rng.normal(0, jitter)), 0.7]
               for u, v in med]
        out.append({"points": pts, "width": width})
    return out


def capture_of(data, chars, sets=1, **kw):
    return {"version": 1, "kind": "hanzi", "width": 0.05,
            "chars": [{"char": ch, "set": k, "strokes": written(data, ch, seed=k, **kw)}
                      for ch in chars for k in range(sets)]}


@pytest.fixture
def toy():
    return toy_data()


def test_gb2312_level1_and_recommended():
    chars = gb2312_level1()
    assert len(chars) == 3755 and chars[0] == "啊" and len(set(chars)) == 3755
    assert len(set(RECOMMENDED)) == len(RECOMMENDED) == 100
    assert set(RECOMMENDED) <= set(chars)


def test_stroke_check(toy):
    strokes = normalize_hanzi(capture_of(toy, "江"))[0]["江"]
    assert match_strokes(strokes, "江", toy).ok
    short = match_strokes(strokes[:5], "江", toy)
    assert not short.ok and "写了 5 笔，标准 6 笔" in short.reason
    swapped = [strokes[3], *strokes[1:3], strokes[0], *strokes[4:]]
    bad = match_strokes(swapped, "江", toy)
    assert not bad.ok and bad.stroke in (1, 4)
    flipped = [(list(reversed(strokes[3][0])), strokes[3][1]) if i == 3 else s
               for i, s in enumerate(strokes)]
    assert match_strokes(flipped, "江", toy).stroke == 4
    assert not match_strokes(strokes, "龘", toy).ok


def test_capture_validation():
    with pytest.raises(CaptureError):
        normalize_hanzi({"chars": []})
    with pytest.raises(CaptureError):
        normalize_hanzi({"chars": [{"char": "江江", "strokes": [[[0, 0]]]}]})
    with pytest.raises(CaptureError):
        normalize_hanzi({"chars": [{"char": "江", "set": 9, "strokes": [[[0, 0]]]}]})


def test_build_font_has_only_captured_chars(toy, tmp_path):
    cap = capture_of(toy, "清工", sets=2, jitter=0.01)
    cap["chars"].append({"char": "江", "set": 0, "strokes": written(toy, "江")})
    raw = tmp_path / "hz.ttf"
    report = build_hanzi_font(cap, raw, size=128)
    assert report["glyphs"] == 3 and report["chars"] == "工江清"
    assert report["alternates"] == 2
    font = TTFont(str(raw))
    cmap = font.getBestCmap()
    assert set(map(chr, cmap)) == {"清", "江", "工"}
    assert font["hmtx"][cmap[ord("江")]][0] == font["head"].unitsPerEm
    alts = read_alternates(font)
    assert cmap[ord("清")] in alts and cmap[ord("江")] not in alts
    # Same capture, same font, whatever the order in the JSON.
    again = tmp_path / "again.ttf"
    build_hanzi_font({**cap, "chars": cap["chars"][::-1]}, again, size=128)
    assert raw.read_bytes() == again.read_bytes()


def test_merge_replaces_only_captured_chars(toy, tmp_path, font_path):
    raw = tmp_path / "hz.ttf"
    build_hanzi_font(capture_of(toy, "清江工"), raw, size=128)
    base = TTFont(str(font_path))
    base_cmap = base.getBestCmap()
    merged = merge_fonts(font_path, raw, tmp_path / "merged.ttf", replace=True)
    assert merged["replaced_chars"] == "工江清"
    out = TTFont(str(tmp_path / "merged.ttf"))
    out_cmap = out.getBestCmap()
    # Merged fonts regenerate glyph names, so compare glyph IDs.
    old_count = len(base.getGlyphOrder())
    gid = out.getGlyphID
    assert gid(out_cmap[ord("汁")]) == base.getGlyphID(base_cmap[ord("汁")])  # untouched
    assert gid(out_cmap[ord("江")]) >= old_count                             # handwritten
    assert out["hmtx"][out_cmap[ord("江")]][0] == base["hmtx"][base_cmap[ord("江")]][0]


@pytest.mark.skipif(not hanzi_data.available(), reason="hanzi data not downloaded")
def test_real_data_checks_reference_writing():
    data = hanzi_data.load()
    for ch in "清江永":
        strokes = [([(float(u), float(v), 0.5) for u, v in med], 0.05)
                   for med in data.get(ch).medians]
        assert match_strokes(strokes, ch, data).ok, ch
