"""Read and write handwritten glyph sets as OpenType alternates.

A character with several handwritten shapes keeps its first shape as the
cmap glyph; the others become unencoded alternates. Two GSUB features expose
them:

* ``rand`` - one AlternateSubst lookup, ``base -> [base, alt1, alt2, ...]``.
  HarfBuzz applies it by default and picks a random member.
* ``calt`` - cycling for apps without ``rand`` (Word): after a base glyph the
  next variable glyph becomes ``.s1``, after an ``.s1`` glyph it becomes
  ``.s2`` and so on, wrapping to the base when a character has no further set.

Lookups are appended to an existing GSUB; existing lookups, features and
scripts are left untouched apart from feature indices being re-sorted.
"""

from __future__ import annotations

from fontTools.otlLib.builder import (
    buildAlternateSubstSubtable,
    buildCoverage,
    buildLookup,
    buildSingleSubstSubtable,
)
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables import otTables as ot

NO_FEATURE = 0xFFFF


def read_alternates(font: TTFont) -> dict[str, list[str]]:
    """Return ``{base glyph: [alternate, ...]}`` from the font's ``rand`` feature.

    The base glyph itself is dropped from its alternate list.
    """
    if "GSUB" not in font:
        return {}
    table = font["GSUB"].table
    if not table.FeatureList or not table.LookupList:
        return {}
    indices: list[int] = []
    for record in table.FeatureList.FeatureRecord:
        if record.FeatureTag == "rand":
            indices.extend(record.Feature.LookupListIndex)
    out: dict[str, list[str]] = {}
    lookups = table.LookupList.Lookup
    for index in dict.fromkeys(indices):
        if index >= len(lookups):
            continue
        for sub in lookups[index].SubTable:
            if getattr(sub, "LookupType", None) == 7:
                sub = sub.ExtSubTable
            alternates = getattr(sub, "alternates", None)
            if not alternates:
                continue
            for base, alts in alternates.items():
                extra = [name for name in alts if name != base]
                if extra and base not in out:
                    out[base] = extra
    return out


def add_alternates(font: TTFont, alternates: dict[str, list[str]]) -> int:
    """Append ``rand`` and ``calt`` lookups for ``alternates``; returns lookups added."""
    alternates = {base: list(alts) for base, alts in alternates.items() if alts}
    if not alternates:
        return 0
    glyph_map = font.getReverseGlyphMap()
    missing = [n for base, alts in alternates.items() for n in [base, *alts]
               if n not in glyph_map]
    if missing:
        raise ValueError(f"glyphs not in font: {missing[:5]}")
    table = _gsub(font)
    lookups = table.LookupList.Lookup
    start = len(lookups)
    depth = max(len(alts) for alts in alternates.values())

    lookups.append(buildLookup([buildAlternateSubstSubtable(
        {base: [base, *alts] for base, alts in alternates.items()})]))
    for t in range(1, depth + 1):
        mapping = {base: alts[t - 1] for base, alts in alternates.items() if len(alts) >= t}
        lookups.append(buildLookup([buildSingleSubstSubtable(mapping)]))
    bases = list(alternates)
    chain = []
    for j in range(depth):
        previous = bases if j == 0 else [alts[j - 1] for alts in alternates.values()
                                         if len(alts) >= j]
        chain.append(_chain_rule(previous, bases, start + 1 + j, glyph_map))
    lookups.append(buildLookup(chain))
    table.LookupList.LookupCount = len(lookups)

    rand_index = start
    calt_index = start + depth + 1
    _add_features(table, {"rand": [rand_index], "calt": [calt_index]})
    return depth + 2


def _chain_rule(backtrack: list[str], inputs: list[str], lookup_index: int,
                glyph_map: dict[str, int]) -> ot.ChainContextSubst:
    sub = ot.ChainContextSubst()
    sub.Format = 3
    sub.BacktrackGlyphCount = 1
    sub.BacktrackCoverage = [buildCoverage(backtrack, glyph_map)]
    sub.InputGlyphCount = 1
    sub.InputCoverage = [buildCoverage(inputs, glyph_map)]
    sub.LookAheadGlyphCount = 0
    sub.LookAheadCoverage = []
    record = ot.SubstLookupRecord()
    record.SequenceIndex = 0
    record.LookupListIndex = lookup_index
    sub.SubstCount = 1
    sub.SubstLookupRecord = [record]
    return sub


def _gsub(font: TTFont) -> ot.GSUB:
    """Return the GSUB table, creating it or its empty lists as needed."""
    if "GSUB" not in font:
        font["GSUB"] = newTable("GSUB")
        font["GSUB"].table = ot.GSUB()
        font["GSUB"].table.Version = 0x00010000
    table = font["GSUB"].table
    for attr in ("ScriptList", "FeatureList", "LookupList"):
        if not hasattr(table, attr):
            setattr(table, attr, None)
    if table.ScriptList is None:
        table.ScriptList = ot.ScriptList()
        table.ScriptList.ScriptRecord = []
        table.ScriptList.ScriptCount = 0
    if table.FeatureList is None:
        table.FeatureList = ot.FeatureList()
        table.FeatureList.FeatureRecord = []
        table.FeatureList.FeatureCount = 0
    if table.LookupList is None:
        table.LookupList = ot.LookupList()
        table.LookupList.Lookup = []
        table.LookupList.LookupCount = 0
    return table


def _lang_systems(table: ot.GSUB) -> list[ot.LangSys]:
    found = []
    for record in table.ScriptList.ScriptRecord:
        script = record.Script
        if script.DefaultLangSys is not None:
            found.append(script.DefaultLangSys)
        found.extend(lang.LangSys for lang in script.LangSysRecord)
    return found


def _add_features(table: ot.GSUB, features: dict[str, list[int]]) -> None:
    """Add features, keep FeatureList sorted by tag, attach them to every LangSys."""
    records = table.FeatureList.FeatureRecord
    old_count = len(records)
    for tag, lookup_indices in features.items():
        feature = ot.Feature()
        feature.FeatureParams = None
        feature.LookupListIndex = list(lookup_indices)
        feature.LookupCount = len(lookup_indices)
        record = ot.FeatureRecord()
        record.FeatureTag = tag
        record.Feature = feature
        records.append(record)
    order = sorted(range(len(records)), key=lambda i: records[i].FeatureTag)
    remap = {old: new for new, old in enumerate(order)}
    table.FeatureList.FeatureRecord = [records[i] for i in order]
    table.FeatureList.FeatureCount = len(order)

    new_indices = [remap[i] for i in range(old_count, len(records))]
    if not table.ScriptList.ScriptRecord:
        script = ot.Script()
        script.DefaultLangSys = _new_lang_sys()
        script.LangSysRecord = []
        script.LangSysCount = 0
        record = ot.ScriptRecord()
        record.ScriptTag = "DFLT"
        record.Script = script
        table.ScriptList.ScriptRecord = [record]
        table.ScriptList.ScriptCount = 1
    for lang in _lang_systems(table):
        kept = [remap[i] for i in lang.FeatureIndex]
        lang.FeatureIndex = sorted(set(kept + new_indices))
        lang.FeatureCount = len(lang.FeatureIndex)
        if lang.ReqFeatureIndex != NO_FEATURE:
            lang.ReqFeatureIndex = remap[lang.ReqFeatureIndex]
    variations = getattr(table, "FeatureVariations", None)
    if variations is not None:
        for rec in variations.FeatureVariationRecord:
            for sub in rec.FeatureTableSubstitution.SubstitutionRecord:
                sub.FeatureIndex = remap[sub.FeatureIndex]


def _new_lang_sys() -> ot.LangSys:
    lang = ot.LangSys()
    lang.LookupOrder = None
    lang.ReqFeatureIndex = NO_FEATURE
    lang.FeatureIndex = []
    lang.FeatureCount = 0
    return lang
