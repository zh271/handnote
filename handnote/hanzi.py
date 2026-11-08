"""Handwritten Chinese characters from a small sample, composed by component.

The writer covers a few hundred characters in standard stroke order. Each
written character is checked against its reference strokes, then cut into
components with makemeahanzi's per-stroke component paths (``清`` gives
``氵`` and ``青``). An unwritten character is composed by placing such
component instances where its own components sit in the reference glyph:
the instance moves from its reference box in the source character to the
reference box in the target, so the writer's own offsets and proportions
travel with it.

Capture payload::

    {"version": 1, "kind": "hanzi", "width": 0.05,
     "chars": [{"char": "清", "set": 0, "strokes": [[[u, v, p], ...]]}]}

Coordinates are the same cell-normalised ``(u, v)`` as symbol captures; the
cell is makemeahanzi's 1024 box.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .capture import DEFAULT_WIDTH, MAX_POINTS_PER_STROKE, CaptureError, Stroke, _parse_stroke
from .hanzi_data import Box, HanziData, Node, gb2312_level1, node_key
from .symbols import MAX_SETS

MAX_CHARS = 8192
MAX_STROKES = 64
#: Deepest recursion when a component has no instance and must itself be split.
MAX_DEPTH = 3
#: Largest change of aspect ratio applied to an instance; the rest is uniform.
MAX_ASPECT = 1.4
#: Instances scaled outside this range are not used.
SCALE_RANGE = (0.35, 2.5)
#: Mean endpoint error (fraction of the character box) above which a written
#: character is taken as out of stroke order.
MATCH_MEAN = 0.22
MATCH_ONE = 0.5


# -- capture -----------------------------------------------------------------
def normalize_hanzi(data: dict[str, Any]) -> dict[int, dict[str, list[Stroke]]]:
    """Validate a hanzi capture; returns ``{set: {char: strokes}}``."""
    if not isinstance(data, dict):
        raise CaptureError("capture must be an object")
    chars = data.get("chars")
    if not isinstance(chars, list) or not chars:
        raise CaptureError("capture needs a non-empty 'chars' list")
    if len(chars) > MAX_CHARS:
        raise CaptureError(f"too many characters (max {MAX_CHARS})")
    default_width = min(max(float(data.get("width") or DEFAULT_WIDTH), 0.01), 0.2)
    out: dict[int, dict[str, list[Stroke]]] = {}
    for entry in chars:
        if not isinstance(entry, dict):
            raise CaptureError("each character must be an object")
        ch = entry.get("char")
        if not isinstance(ch, str) or len(ch) != 1:
            raise CaptureError(f"bad char: {ch!r}")
        set_no = entry.get("set", 0)
        if (not isinstance(set_no, int) or isinstance(set_no, bool)
                or not 0 <= set_no < MAX_SETS):
            raise CaptureError(f"{ch}: bad set {set_no!r} (0..{MAX_SETS - 1})")
        strokes = entry.get("strokes") or []
        if not isinstance(strokes, list) or len(strokes) > MAX_STROKES:
            raise CaptureError(f"{ch}: 'strokes' must be a list of at most {MAX_STROKES}")
        parsed = []
        for stroke in strokes:
            if isinstance(stroke, list) and len(stroke) > MAX_POINTS_PER_STROKE:
                raise CaptureError(f"{ch}: stroke has too many points")
            points, width = _parse_stroke(stroke, 0, default_width)
            if points:
                parsed.append((points, width))
        if parsed:
            out.setdefault(set_no, {})[ch] = parsed
    if not out:
        raise CaptureError("capture contains no strokes")
    return out


# -- stroke check ------------------------------------------------------------
@dataclass
class MatchResult:
    """Outcome of checking one written character against its reference."""

    ok: bool
    expected: int | None
    got: int
    reason: str = ""
    stroke: int | None = None  # 1-based stroke that failed, if any

    def to_dict(self) -> dict:
        return {"ok": self.ok, "expected": self.expected, "got": self.got,
                "reason": self.reason, "stroke": self.stroke}


def _norm(points: np.ndarray, box: Box) -> np.ndarray:
    x0, y0, x1, y1 = box
    span = max(x1 - x0, y1 - y0, 1e-6)
    w = max(x1 - x0, 0.3 * span)
    h = max(y1 - y0, 0.3 * span)
    return (points - np.array([(x0 + x1) / 2, (y0 + y1) / 2])) / np.array([w, h])


def match_strokes(strokes: Sequence[Stroke], ch: str, data: HanziData) -> MatchResult:
    """Check stroke count and order of a written character.

    Both the writing and the reference medians are normalised to their own
    boxes; stroke ``i`` must start and end near reference stroke ``i``.
    """
    entry = data.get(ch)
    got = len(strokes)
    if entry is None:
        return MatchResult(False, None, got, "没有这个字的笔顺数据")
    if got != entry.strokes:
        return MatchResult(False, entry.strokes, got,
                           f"写了 {got} 笔，标准 {entry.strokes} 笔")
    user = [np.array([(u, v) for u, v, _ in pts]) for pts, _ in strokes]
    ubox = _box_of(user)
    rbox = data.ref_box(ch)
    errors = []
    reversed_at = None
    for i, (mine, ref) in enumerate(zip(user, entry.medians, strict=True)):
        a = _norm(mine[[0, -1]], ubox)
        b = _norm(ref[[0, -1]], rbox)
        err = float(np.linalg.norm(a - b, axis=1).mean())
        flipped = float(np.linalg.norm(a - b[::-1], axis=1).mean())
        if flipped < err * 0.5 and err > MATCH_ONE * 0.6 and reversed_at is None:
            reversed_at = i
        errors.append(err)
    worst = int(np.argmax(errors))
    if reversed_at is not None:
        return MatchResult(False, entry.strokes, got, f"第 {reversed_at + 1} 笔写反了",
                           reversed_at + 1)
    if errors[worst] > MATCH_ONE or float(np.mean(errors)) > MATCH_MEAN:
        return MatchResult(False, entry.strokes, got,
                           f"第 {worst + 1} 笔的位置或笔顺和标准不一致", worst + 1)
    return MatchResult(True, entry.strokes, got)


def _box_of(arrays: Iterable[np.ndarray]) -> Box:
    allp = np.vstack(list(arrays))
    x0, y0 = allp.min(axis=0)
    x1, y1 = allp.max(axis=0)
    return float(x0), float(y0), float(x1), float(y1)


# -- components ----------------------------------------------------------------
@dataclass
class Instance:
    """One handwritten occurrence of a component."""

    key: str
    strokes: list[Stroke]
    box: Box  # reference box of these strokes in the source character
    slot: tuple[str, int] | None  # (parent IDC, child index); None = whole char
    source: str
    set: int


@dataclass
class Library:
    """Everything harvested from a capture."""

    written: dict[str, dict[int, list[Stroke]]] = field(default_factory=dict)
    instances: dict[str, list[Instance]] = field(default_factory=dict)
    checks: dict[str, dict[int, MatchResult]] = field(default_factory=dict)

    def keys(self) -> set[str]:
        return set(self.instances)


def harvest(capture: dict[str, Any] | dict[int, dict[str, list[Stroke]]],
            data: HanziData) -> Library:
    """Check every written character and cut the good ones into components."""
    sets = capture if _is_grouped(capture) else normalize_hanzi(capture)
    lib = Library()
    for set_no in sorted(sets):
        for ch in sorted(sets[set_no]):
            strokes = sets[set_no][ch]
            lib.written.setdefault(ch, {})[set_no] = strokes
            result = match_strokes(strokes, ch, data)
            lib.checks.setdefault(ch, {})[set_no] = result
            if not result.ok:
                continue
            entry = data.get(ch)
            _add(lib, Instance(ch, strokes, data.ref_box(ch), None, ch, set_no))
            if entry.tree is not None:
                _harvest_node(lib, data, ch, entry.tree, (), strokes, set_no)
    return lib


def _is_grouped(capture: Any) -> bool:
    return isinstance(capture, dict) and bool(capture) and all(
        isinstance(k, int) for k in capture)


def _harvest_node(lib: Library, data: HanziData, ch: str, node: Node,
                  path: tuple[int, ...], strokes: list[Stroke], set_no: int) -> None:
    if isinstance(node, str):
        return
    idc, kids = node
    for i, kid in enumerate(kids):
        sub = path + (i,)
        idx = data.strokes_of(ch, sub)
        if not idx:
            continue
        _add(lib, Instance(node_key(kid), [strokes[j] for j in idx],
                           data.ref_box(ch, idx), (idc, i), ch, set_no))
        _harvest_node(lib, data, ch, kid, sub, strokes, set_no)


def _add(lib: Library, inst: Instance) -> None:
    lib.instances.setdefault(inst.key, []).append(inst)


# -- composition -----------------------------------------------------------------
@dataclass
class Composed:
    """Strokes of one composed (or written) character and where parts came from."""

    char: str
    strokes: list[Stroke]
    parts: list[tuple[str, str, int]]  # (component, source character, set)
    written: bool = False


def _fit(src: Box, dst: Box) -> tuple[float, float] | None:
    sw, sh = src[2] - src[0], src[3] - src[1]
    dw, dh = dst[2] - dst[0], dst[3] - dst[1]
    if min(sw, sh, dw, dh) <= 0:
        return None
    sx, sy = dw / sw, dh / sh
    ratio = sx / sy
    if ratio > MAX_ASPECT or ratio < 1 / MAX_ASPECT:
        g = math.sqrt(sx * sy)
        lim = math.sqrt(MAX_ASPECT)
        sx, sy = (g * lim, g / lim) if ratio > 1 else (g / lim, g * lim)
    lo, hi = SCALE_RANGE
    if not (lo <= sx <= hi and lo <= sy <= hi):
        return None
    return sx, sy


def _place(strokes: Sequence[Stroke], src: Box, dst: Box, scale: tuple[float, float]
           ) -> list[Stroke]:
    sx, sy = scale
    scx, scy = (src[0] + src[2]) / 2, (src[1] + src[3]) / 2
    dcx, dcy = (dst[0] + dst[2]) / 2, (dst[1] + dst[3]) / 2
    out = []
    for points, width in strokes:
        moved = [(min(max((u - scx) * sx + dcx, 0.0), 1.0),
                  min(max((v - scy) * sy + dcy, 0.0), 1.0), p) for u, v, p in points]
        out.append((moved, width))
    return out


def _score(inst: Instance, slot: tuple[str, int] | None, dst: Box) -> float:
    sw, sh = inst.box[2] - inst.box[0], inst.box[3] - inst.box[1]
    dw, dh = dst[2] - dst[0], dst[3] - dst[1]
    aspect = abs(math.log((sw / sh) / (dw / dh)))
    size = abs(math.log((sw * sh) / (dw * dh)))
    same_slot = 0.0 if inst.slot == slot else (0.35 if inst.slot is None else 0.6)
    return same_slot + aspect + 0.15 * size


class Composer:
    """Compose characters from a :class:`Library` against reference data."""

    def __init__(self, lib: Library, data: HanziData):
        self.lib = lib
        self.data = data

    def variants(self, ch: str) -> int:
        """How many distinct sets ``ch`` can have (written sets win)."""
        if ch in self.lib.written:
            return len(self.lib.written[ch])
        return 1 if self.compose(ch, 0) is not None else 0

    def compose(self, ch: str, k: int = 0) -> Composed | None:
        """Set ``k`` of ``ch``: the written strokes, or a composition."""
        written = self.lib.written.get(ch)
        if written:
            sets = sorted(written)
            pick = sets[k % len(sets)]
            return Composed(ch, written[pick], [(ch, ch, pick)], written=True)
        entry = self.data.get(ch)
        if entry is None or entry.tree is None:
            return None
        parts: list[tuple[str, str, int]] = []
        strokes = self._node(ch, entry.tree, (), k, 0, parts)
        if strokes is None:
            return None
        return Composed(ch, strokes, parts)

    def _node(self, ch: str, node: Node, path: tuple[int, ...], k: int, depth: int,
              parts: list[tuple[str, str, int]]) -> list[Stroke] | None:
        idc, kids = node
        groups = [self.data.strokes_of(ch, path + (i,)) for i in range(len(kids))]
        # Every stroke of the node must belong to a child, otherwise a stroke
        # the reference leaves unassigned (the dot of 求) would go missing.
        whole = self.data.stroke_count(ch) if not path else len(self.data.strokes_of(ch, path))
        if any(not g for g in groups) or sum(map(len, groups)) != whole:
            return None
        out: list[Stroke] = []
        for i, kid in enumerate(kids):
            sub = path + (i,)
            idx = groups[i]
            dst = self.data.ref_box(ch, idx)
            placed = self._component(ch, kid, sub, (idc, i), dst, len(idx), k, depth, parts)
            if placed is None:
                return None
            out += placed
        return out

    def _component(self, ch: str, kid: Node, path: tuple[int, ...], slot: tuple[str, int],
                   dst: Box, n_strokes: int, k: int, depth: int,
                   parts: list[tuple[str, str, int]]) -> list[Stroke] | None:
        key = node_key(kid)
        cands = [c for c in self.lib.instances.get(key, []) if c.source != ch]
        ranked = sorted(cands, key=lambda c: (_score(c, slot, dst), c.source, c.set))
        good = []
        for cand in ranked:
            scale = _fit(cand.box, dst)
            if scale is not None:
                good.append((cand, scale))
        if good:
            best = _score(good[0][0], slot, dst)
            near = [g for g in good if _score(g[0], slot, dst) <= best + 0.5]
            cand, scale = near[k % len(near)]
            parts.append((key, cand.source, cand.set))
            return _place(cand.strokes, cand.box, dst, scale)
        if depth >= MAX_DEPTH:
            return None
        if isinstance(kid, tuple):
            return self._node(ch, kid, path, k, depth + 1, parts)
        own = self.data.get(kid)
        if own is None or own.tree is None or own.strokes != n_strokes:
            return None
        inner = self._node(kid, own.tree, (), k, depth + 1, parts)
        if inner is None:
            return None
        src = self.data.ref_box(kid)
        scale = _fit(src, dst)
        if scale is None:
            return None
        return _place(inner, src, dst, scale)


# -- planning --------------------------------------------------------------------
def coverage(lib: Library, data: HanziData, targets: str | None = None) -> dict:
    """Which targets can be produced right now (written or composed)."""
    targets = targets or gb2312_level1()
    composer = Composer(lib, data)
    ok, missing = [], []
    for ch in targets:
        (ok if composer.compose(ch, 0) is not None else missing).append(ch)
    written = [ch for ch in targets if ch in lib.written]
    return {"total": len(targets), "available": len(ok), "written": len(written),
            "composed": len(ok) - len(written), "missing": "".join(missing)}


def _structural(ch: str, have: set[str], data: HanziData, depth: int = 0) -> bool:
    """Cheap composability test that ignores geometry (used for ranking)."""
    if ch in have:
        return True
    entry = data.get(ch)
    if (entry is None or entry.tree is None or depth > MAX_DEPTH
            or any(m is None for m in entry.matches)):
        return False
    return _structural_node(entry.tree, have, data, depth)


def _structural_node(node: Node, have: set[str], data: HanziData, depth: int) -> bool:
    for kid in node[1]:
        key = node_key(kid)
        if key in have:
            continue
        if depth >= MAX_DEPTH:
            return False
        if isinstance(kid, tuple):
            if not _structural_node(kid, have, data, depth + 1):
                return False
        elif not _structural(kid, have, data, depth + 1):
            return False
    return True


def _provides(ch: str, data: HanziData) -> set[str]:
    entry = data.get(ch)
    out = {ch}
    if entry is None or entry.tree is None:
        return out
    stack: list[Node] = [entry.tree]
    while stack:
        node = stack.pop()
        if isinstance(node, tuple):
            for kid in node[1]:
                out.add(node_key(kid))
                stack.append(kid)
    return out


def recommend(lib: Library, data: HanziData, targets: str | None = None, n: int = 100,
              prefer: str = "", progress: Callable[[int], None] | None = None) -> list[str]:
    """Next ``n`` characters to write, greedily by how many targets they unlock.

    Gain is divided by stroke count (to the 0.7th power), so simple characters
    that carry common components come first; characters in ``prefer`` (the
    writer's notes) get a bonus. Ranking is structural (it ignores the
    geometric quality gate), so the real gain is measured by :func:`coverage`.
    """
    targets = targets or gb2312_level1()
    prefer = set(prefer)
    pool = [ch for ch in targets if ch in data and ch not in lib.written]
    have = lib.keys()
    open_targets = [ch for ch in targets if not _structural(ch, have, data)]
    provides = {ch: _provides(ch, data) for ch in pool}
    picked: list[str] = []
    while len(picked) < n and pool and open_targets:
        need: dict[str, int] = {}
        for ch in open_targets:
            for key in _leaf_needs(ch, have, data):
                need[key] = need.get(key, 0) + 1
        best, best_gain = None, 0.0
        open_set = set(open_targets)
        for ch in pool:
            gain = sum(need.get(key, 0) for key in provides[ch] if key not in have)
            if ch in open_set:
                gain += 1
            if not gain:
                continue
            # Unlocked targets per unit of writing effort; characters from the
            # writer's own text are worth more.
            score = gain / data.stroke_count(ch) ** 0.7 * (1.5 if ch in prefer else 1.0)
            if score > best_gain:
                best, best_gain = ch, score
        if best is None:
            break
        picked.append(best)
        pool.remove(best)
        new = provides[best] - have
        have |= new
        # Only targets whose decomposition mentions a new key can change.
        open_targets = [ch for ch in open_targets if ch != best and not (
            new & _all_keys(ch, data) and _structural(ch, have, data))]
        if progress:
            progress(len(picked))
    if len(picked) < n:
        # Structure says the rest is composable, but some fail the quality
        # gate today; those have to be written, simplest first.
        # Stand in for the picks with their reference strokes to see what the
        # geometry actually allows once they are written.
        chosen = set(picked)
        future = _reference_capture([*lib.written, *picked], data)
        composer = Composer(harvest(future, data), data)
        rest = [ch for ch in targets if ch in data and ch not in lib.written
                and ch not in chosen and composer.compose(ch, 0) is None]
        rest.sort(key=lambda ch: (ch not in prefer, data.stroke_count(ch)))
        picked += rest[:n - len(picked)]
    return picked


def _reference_capture(chars: Iterable[str], data: HanziData
                       ) -> dict[int, dict[str, list[Stroke]]]:
    """A capture that writes ``chars`` exactly as their reference medians."""
    out: dict[str, list[Stroke]] = {}
    for ch in chars:
        entry = data.get(ch)
        if entry is not None:
            out[ch] = [([(float(u), float(v), 1.0) for u, v in med], DEFAULT_WIDTH)
                       for med in entry.medians]
    return {0: out}


_ALL_KEYS: dict[tuple[int, str], frozenset[str]] = {}


def _all_keys(ch: str, data: HanziData, depth: int = 0) -> frozenset[str]:
    """Every component key reachable from ``ch`` within :data:`MAX_DEPTH`."""
    memo_key = (id(data), ch)
    if depth == 0 and memo_key in _ALL_KEYS:
        return _ALL_KEYS[memo_key]
    out = {ch}
    entry = data.get(ch)
    if entry is not None and entry.tree is not None and depth <= MAX_DEPTH:
        stack: list[tuple[Node, int]] = [(entry.tree, depth)]
        while stack:
            node, d = stack.pop()
            for kid in node[1]:
                out.add(node_key(kid))
                if d >= MAX_DEPTH:
                    continue
                if isinstance(kid, tuple):
                    stack.append((kid, d + 1))
                else:
                    out |= _all_keys(kid, data, d + 1)
    result = frozenset(out)
    if depth == 0:
        _ALL_KEYS[memo_key] = result
    return result


def _leaf_needs(ch: str, have: set[str], data: HanziData) -> set[str]:
    """Top-level components of ``ch`` that are still missing."""
    entry = data.get(ch)
    if entry is None or entry.tree is None:
        return {ch}
    return {node_key(kid) for kid in entry.tree[1] if node_key(kid) not in have}


# -- font ------------------------------------------------------------------------
def build_hanzi_font(
    capture: dict[str, Any],
    out_path: str | Path,
    *,
    data: HanziData | None = None,
    targets: str | None = None,
    sets: int = 3,
    size: int = 256,
    upem: int = 1000,
    family: str = "HandNote Hanzi",
    pressure: float = 1.0,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Write every written or composable target as a glyph; returns a report.

    Written characters keep their own sets; composed ones get up to ``sets``
    variants, each a different choice of component instances.
    """
    from .capture import rasterize_strokes
    from .hanzi_data import load
    from .symbols import _cell_glyph, write_font

    data = data or load()
    lib = harvest(capture, data)
    composer = Composer(lib, data)
    chars = list(dict.fromkeys((targets or gb2312_level1()) + "".join(sorted(lib.written))))
    sets = min(max(int(sets), 1), MAX_SETS)
    glyphs: dict[str, object] = {}
    metrics: dict[str, tuple[int, int]] = {}
    cmap: dict[int, str] = {}
    alternates: dict[str, list[str]] = {}
    written, composed = [], []
    for done, ch in enumerate(chars, 1):
        if progress and done % 50 == 0:
            progress(done, len(chars))
        count = len(lib.written[ch]) if ch in lib.written else sets
        seen: list[tuple] = []
        shapes = []
        for k in range(count):
            result = composer.compose(ch, k)
            if result is None:
                break
            sig = tuple(result.parts) if not result.written else (k,)
            if sig in seen:
                continue
            seen.append(sig)
            crop = rasterize_strokes(result.strokes, size, pressure=pressure)
            traced = _cell_glyph(crop, upem, 128, cjk=True)
            if traced is not None:
                shapes.append(traced)
        if not shapes:
            continue
        name = f"uni{ord(ch):04X}"
        for n, (pen, advance, lsb) in enumerate(shapes):
            glyph_name = name if n == 0 else f"{name}.s{n}"
            glyphs[glyph_name] = pen.glyph()
            metrics[glyph_name] = (advance, lsb)
        if len(shapes) > 1:
            alternates[name] = [f"{name}.s{n}" for n in range(1, len(shapes))]
        cmap[ord(ch)] = name
        (written if ch in lib.written else composed).append(ch)
    if progress:
        progress(len(chars), len(chars))
    write_font(out_path, glyphs, metrics, cmap, alternates, upem=upem, family=family)
    bad = {ch: res.reason for ch, by_set in lib.checks.items()
           for res in by_set.values() if not res.ok}
    return {
        "font": str(out_path),
        "glyphs": len(cmap),
        "written": len(written),
        "composed": len(composed),
        "total": len(targets or gb2312_level1()),
        "alternates": sum(len(a) for a in alternates.values()),
        "unsplit": bad,
        "chars": "".join(written + composed),
    }
