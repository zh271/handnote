"""Chinese characters composed from handwritten components (toy reference data)."""

import json

import numpy as np
import pytest
from fontTools.ttLib import TTFont

from handnote import hanzi_data
from handnote.capture import CaptureError
from handnote.hanzi import (
    Composer,
    Library,
    _reference_capture,
    build_hanzi_font,
    coverage,
    harvest,
    match_strokes,
    normalize_hanzi,
    recommend,
)
from handnote.hanzi_data import HanziData, gb2312_level1, node_key, parse_ids
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
MOUTH = [_v(300, 300, 700), _h(300, 700, 300), _h(300, 700, 700)]         # 口 (toy)


def _squeeze(strokes, x0, x1, y0=0, y1=1024):
    """Map a full-cell drawing into the box [x0, x1] x [y0, y1]."""
    return [[[x0 + x * (x1 - x0) // 1024, y0 + y * (y1 - y0) // 1024] for x, y in s]
            for s in strokes]


def _toy() -> HanziData:
    left = _squeeze(WATER, 0, 1024)
    chars = {
        "工": {"d": "", "m": [None] * 3, "s": WORK},
        "氵": {"d": "", "m": [None] * 3, "s": WATER},
        "青": {"d": "", "m": [None] * 4, "s": BLUE},
        "口": {"d": "", "m": [None] * 3, "s": MOUTH},
        "清": {"d": "⿰氵青", "m": [[0]] * 3 + [[1]] * 4,
               "s": left + _squeeze(BLUE, 360, 1000)},
        "江": {"d": "⿰氵工", "m": [[0]] * 3 + [[1]] * 3,
               "s": left + _squeeze(WORK, 380, 1000)},
        # ⿱江口: 江 itself is never written, so it must be split again.
        "沪": {"d": "⿱江口", "m": [[0]] * 6 + [[1]] * 3,
               "s": _squeeze(left + _squeeze(WORK, 380, 1000), 0, 1024, 0, 600)
               + _squeeze(MOUTH, 200, 820, 560, 1024)},
        # 氵 squeezed into a sliver: scaling a full instance down that far fails.
        "汁": {"d": "⿰氵工", "m": [[0]] * 3 + [[1]] * 3,
               "s": _squeeze(WATER, 0, 1024, 0, 40) + _squeeze(WORK, 380, 1000)},
        # The reference leaves one stroke unassigned.
        "汀": {"d": "⿰氵工", "m": [[0]] * 3 + [[1]] * 2 + [None],
               "s": left + _squeeze(WORK, 380, 1000)},
    }
    return HanziData.from_json({"version": 1, "chars": chars})


def _written(data, ch, jitter=0.0, seed=0, width=0.05):
    rng = np.random.default_rng(seed)
    out = []
    for med in data.get(ch).medians:
        pts = [[float(u + rng.normal(0, jitter)), float(v + rng.normal(0, jitter)), 0.7]
               for u, v in med]
        out.append({"points": pts, "width": width})
    return out


def _capture(data, chars, sets=1, **kw):
    return {"version": 1, "kind": "hanzi", "width": 0.05,
            "chars": [{"char": ch, "set": k, "strokes": _written(data, ch, seed=k, **kw)}
                      for ch in chars for k in range(sets)]}


@pytest.fixture
def toy():
    return _toy()


def test_ids_parsing():
    assert parse_ids("⿰氵青") == ("⿰", ["氵", "青"])
    assert parse_ids("⿲辛刂辛") == ("⿲", ["辛", "刂", "辛"])
    assert parse_ids("⿰角⿱刀牛") == ("⿰", ["角", ("⿱", ["刀", "牛"])])
    assert parse_ids("？") is None and parse_ids("口") is None and parse_ids("⿰氵") is None
    assert node_key(parse_ids("⿰角⿱刀牛")) == "⿰角⿱刀牛"


def test_gb2312_level1():
    chars = gb2312_level1()
    assert len(chars) == 3755 and chars[0] == "啊" and len(set(chars)) == 3755


def test_component_strokes_and_boxes(toy):
    assert toy.strokes_of("清", (0,)) == [0, 1, 2]
    assert toy.strokes_of("清", (1,)) == [3, 4, 5, 6]
    x0, _, x1, _ = toy.ref_box("清", [0, 1, 2])
    assert x1 < 0.3 and toy.ref_box("清")[2] > 0.8
    assert HanziData.from_json(toy.to_json()).strokes_of("沪", (1,)) == [6, 7, 8]


def test_stroke_check(toy):
    strokes = normalize_hanzi(_capture(toy, "江"))[0]["江"]
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


def test_harvest_cuts_components(toy):
    lib = harvest(_capture(toy, "清工"), toy)
    assert {"清", "氵", "青", "工"} <= lib.keys()
    water = lib.instances["氵"][0]
    assert water.source == "清" and water.slot == ("⿰", 0) and len(water.strokes) == 3


def test_badly_written_char_is_kept_whole_but_not_split(toy):
    cap = _capture(toy, "清")
    cap["chars"][0]["strokes"].pop()
    lib = harvest(cap, toy)
    assert "清" in lib.written and "氵" not in lib.keys()
    assert not lib.checks["清"][0].ok


def test_compose_places_component_in_target_slot(toy):
    lib = harvest(_capture(toy, "清工", width=0.07), toy)
    result = Composer(lib, toy).compose("江")
    assert result is not None and not result.written
    assert ("氵", "清", 0) in result.parts and ("工", "工", 0) in result.parts
    water = np.array([(u, v) for pts, _ in result.strokes[:3] for u, v, _ in pts])
    x0, y0, x1, y1 = toy.ref_box("江", [0, 1, 2])
    assert water[:, 0].min() >= x0 - 0.02 and water[:, 0].max() <= x1 + 0.02
    assert water[:, 1].min() >= y0 - 0.02 and water[:, 1].max() <= y1 + 0.02
    assert all(width == 0.07 for _, width in result.strokes)  # pen width is not scaled


def test_compose_splits_unwritten_components(toy):
    lib = harvest(_capture(toy, "清工口"), toy)
    result = Composer(lib, toy).compose("沪")
    assert result is not None and len(result.strokes) == 9
    assert {part[0] for part in result.parts} == {"氵", "工", "口"}


def test_quality_gate_and_unassigned_strokes(toy):
    lib = harvest(_capture(toy, "清工"), toy)
    composer = Composer(lib, toy)
    assert composer.compose("汁") is None   # 氵 would shrink to a sliver
    assert composer.compose("汀") is None   # one stroke belongs to no component


def test_sets_rotate_instances_and_stay_deterministic(toy):
    cap = _capture(toy, "清工", sets=2, jitter=0.01)
    a = Composer(harvest(cap, toy), toy)
    b = Composer(harvest(json.loads(json.dumps(cap)), toy), toy)
    first, second = a.compose("江", 0), a.compose("江", 1)
    assert first.parts != second.parts
    assert first.strokes == b.compose("江", 0).strokes


def test_recommend_raises_coverage(toy):
    targets = "清江沪口工"
    empty = Library()
    picks = recommend(empty, toy, targets=targets, n=5)
    assert picks and set(picks) <= set(targets)
    before = coverage(empty, toy, targets)["available"]
    after = coverage(harvest(_reference_capture(picks, toy), toy), toy, targets)["available"]
    assert before == 0 and after > before


def test_capture_validation():
    with pytest.raises(CaptureError):
        normalize_hanzi({"chars": []})
    with pytest.raises(CaptureError):
        normalize_hanzi({"chars": [{"char": "江江", "strokes": [[[0, 0]]]}]})
    with pytest.raises(CaptureError):
        normalize_hanzi({"chars": [{"char": "江", "set": 9, "strokes": [[[0, 0]]]}]})


def test_build_font_and_merge(toy, tmp_path, font_path):
    cap = _capture(toy, "清工", sets=2, jitter=0.01)
    raw = tmp_path / "hz.ttf"
    report = build_hanzi_font(cap, raw, data=toy, targets="清江工汁", sets=2, size=128)
    assert report["written"] == 2 and report["composed"] == 1
    font = TTFont(str(raw))
    cmap = font.getBestCmap()
    assert set(map(chr, cmap)) == {"清", "江", "工"}
    assert font["hmtx"][cmap[ord("江")]][0] == font["head"].unitsPerEm
    assert cmap[ord("江")] in read_alternates(font)

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
def test_real_data_small_sample_covers_many():
    data = hanzi_data.load()
    picks = recommend(Library(), data, n=60)
    lib = harvest(_reference_capture(picks, data), data)
    assert coverage(lib, data)["available"] > 1500
