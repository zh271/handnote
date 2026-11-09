"""Reference data for Chinese characters: structure, stroke order, geometry.

Built from makemeahanzi (https://github.com/skishore/makemeahanzi):
``dictionary.txt`` gives each character's IDS decomposition and, per stroke,
the path of the component it belongs to (``matches``); ``graphics.txt`` gives
each stroke's median line. Both are downloaded on demand into the cache and
condensed into one JSON file; nothing from them ships with handnote.

Coordinates are stored in the writing cell's frame: ``(u, v)`` in ``[0, 1]``,
origin top-left, ``v`` down. makemeahanzi's 1024 box (``y`` up, top at 900)
maps onto the cell exactly, so a stroke written over the guide lands on the
reference stroke.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

BASE_URL = "https://raw.githubusercontent.com/skishore/makemeahanzi/master/"
FILES = ("dictionary.txt", "graphics.txt")
COMPACT = "hanzi-gb1.json"
#: Half the reference stroke width, in cell units, added around median boxes.
STROKE_PAD = 30 / 1024

#: Ideographic description characters and how many children each takes.
IDC_ARITY = {chr(c): 2 for c in range(0x2FF0, 0x2FFC)}
IDC_ARITY["⿲"] = IDC_ARITY["⿳"] = 3

Node = str | tuple  # leaf character, or (idc, [children])
Box = tuple[float, float, float, float]


def cache_dir() -> Path:
    root = Path(os.environ.get("HANDNOTE_CACHE") or Path.home() / ".cache" / "handnote")
    return root / "hanzi"


def gb2312_level1() -> str:
    """The 3755 level-1 GB2312 characters, in code order."""
    out = []
    for hi in range(0xB0, 0xD8):
        for lo in range(0xA1, 0xFF):
            try:
                out.append(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return "".join(out)


def parse_ids(text: str) -> Node | None:
    """Parse an IDS string into a tree; ``None`` when unknown or a single char."""
    if not text or len(text) < 2 or "？" in text:
        return None

    def walk(i: int) -> tuple[Node, int]:
        if i >= len(text):
            raise ValueError("truncated IDS")
        ch = text[i]
        arity = IDC_ARITY.get(ch)
        if arity is None:
            return ch, i + 1
        kids = []
        i += 1
        for _ in range(arity):
            node, i = walk(i)
            kids.append(node)
        return (ch, kids), i

    try:
        node, end = walk(0)
    except ValueError:
        return None
    return node if end == len(text) and isinstance(node, tuple) else None


def node_key(node: Node) -> str:
    """Stable key of a component: the character, or the IDS of a subtree."""
    if isinstance(node, str):
        return node
    return node[0] + "".join(node_key(k) for k in node[1])


@dataclass(frozen=True)
class Entry:
    """One character: IDS tree, per-stroke component paths, stroke medians."""

    char: str
    tree: Node | None
    matches: tuple[tuple[int, ...] | None, ...]
    medians: tuple[np.ndarray, ...]  # each (n, 2) in cell units

    @property
    def strokes(self) -> int:
        return len(self.medians)


class HanziData:
    """Lookup over condensed makemeahanzi entries."""

    def __init__(self, entries: dict[str, Entry]):
        self.entries = entries

    @classmethod
    def from_json(cls, data: dict) -> HanziData:
        entries = {}
        for ch, raw in data["chars"].items():
            entries[ch] = Entry(
                char=ch,
                tree=parse_ids(raw.get("d", "")),
                matches=tuple(tuple(m) if m is not None else None for m in raw["m"]),
                medians=tuple(np.asarray(s, dtype=np.float64) / 1024.0 for s in raw["s"]),
            )
        return cls(entries)

    def get(self, ch: str) -> Entry | None:
        return self.entries.get(ch)

    def __contains__(self, ch: str) -> bool:
        return ch in self.entries

    def stroke_count(self, ch: str) -> int | None:
        entry = self.entries.get(ch)
        return entry.strokes if entry else None

    def strokes_of(self, ch: str, path: Sequence[int]) -> list[int]:
        """Indices of the strokes of ``ch`` that belong to the node at ``path``."""
        entry = self.entries[ch]
        n = len(path)
        path = tuple(path)
        return [i for i, m in enumerate(entry.matches)
                if m is not None and len(m) >= n and m[:n] == path]

    def ref_box(self, ch: str, strokes: Iterable[int] | None = None) -> Box | None:
        """Box around the medians of ``strokes`` (all by default), padded by stroke width."""
        entry = self.entries[ch]
        idx = range(entry.strokes) if strokes is None else list(strokes)
        pts = [entry.medians[i] for i in idx]
        if not pts:
            return None
        allp = np.vstack(pts)
        x0, y0 = allp.min(axis=0) - STROKE_PAD
        x1, y1 = allp.max(axis=0) + STROKE_PAD
        return float(x0), float(y0), float(x1), float(y1)

    def to_json(self) -> dict:
        """Inverse of :meth:`from_json`, for tests and caching."""
        def ids(node: Node | None) -> str:
            return node_key(node) if node is not None else ""

        return {"version": 1, "chars": {
            ch: {"d": ids(e.tree),
                 "m": [list(m) if m is not None else None for m in e.matches],
                 "s": [np.rint(s * 1024).astype(int).tolist() for s in e.medians]}
            for ch, e in self.entries.items()}}


def fetch(timeout: float = 120.0, force: bool = False) -> Path:
    """Download makemeahanzi and write the condensed cache; returns its path."""
    folder = cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / COMPACT
    if out.is_file() and not force:
        return out
    raw: dict[str, str] = {}
    for name in FILES:
        path = folder / name
        if force or not path.is_file() or path.stat().st_size < 1024:
            tmp = path.with_suffix(".part")
            with urllib.request.urlopen(BASE_URL + name, timeout=timeout) as resp:
                tmp.write_bytes(resp.read())
            tmp.replace(path)
        raw[name] = path.read_text(encoding="utf-8")
    out.write_text(json.dumps(condense(raw["dictionary.txt"], raw["graphics.txt"]),
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return out


def condense(dictionary: str, graphics: str, targets: str | None = None) -> dict:
    """Keep ``targets`` (GB2312 level 1 by default) plus every component they use."""
    dic = {}
    for line in dictionary.splitlines():
        if line.strip():
            item = json.loads(line)
            dic[item["character"]] = item
    med = {}
    for line in graphics.splitlines():
        if line.strip():
            item = json.loads(line)
            med[item["character"]] = item["medians"]
    want: set[str] = set()
    stack = list(targets or gb2312_level1())
    while stack:
        ch = stack.pop()
        if ch in want or ch not in dic or ch not in med:
            continue
        want.add(ch)
        tree = parse_ids(dic[ch].get("decomposition", ""))
        if tree is not None:
            stack.extend(_leaves(tree))
    chars = {}
    for ch in sorted(want):
        medians = [[[x, 900 - y] for x, y in stroke] for stroke in med[ch]]
        matches = dic[ch].get("matches") or [None] * len(medians)
        if len(matches) != len(medians):
            matches = [None] * len(medians)
        chars[ch] = {"d": dic[ch].get("decomposition", ""), "m": matches, "s": medians}
    return {"version": 1, "chars": chars}


def _leaves(node: Node) -> list[str]:
    if isinstance(node, str):
        return [node]
    out: list[str] = []
    for kid in node[1]:
        out += _leaves(kid)
    return out


_LOADED: HanziData | None = None


def load(path: str | Path | None = None) -> HanziData:
    """Load the condensed cache (downloading it on first use)."""
    global _LOADED
    if path is None and _LOADED is not None:
        return _LOADED
    target = Path(path) if path else cache_dir() / COMPACT
    if not target.is_file():
        if path:
            raise FileNotFoundError(f"hanzi data not found: {target}")
        target = fetch()
    data = HanziData.from_json(json.loads(target.read_text(encoding="utf-8")))
    if path is None:
        _LOADED = data
    return data


def available() -> bool:
    """True when the condensed cache is present (no download)."""
    return (cache_dir() / COMPACT).is_file()


def set_default(data: HanziData | None) -> None:
    """Replace the process-wide dataset (tests and servers use this)."""
    global _LOADED
    _LOADED = data
