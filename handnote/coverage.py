"""Glyph coverage: which characters in a document fall back to another font."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from fontTools.ttLib import TTFont

from .markdown import Block

#: LaTeX command -> Unicode character used by mathtext.
LATEX_SYMBOLS: dict[str, str] = {
    # greek
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ",
    "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "ϕ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    # operators and relations
    "pm": "±", "mp": "∓", "times": "×", "div": "÷", "cdot": "⋅", "ast": "∗",
    "cup": "∪", "cap": "∩", "setminus": "∖", "oplus": "⊕", "otimes": "⊗",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠",
    "approx": "≈", "equiv": "≡", "sim": "∼", "simeq": "≃", "cong": "≅",
    "propto": "∝", "ll": "≪", "gg": "≫",
    "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "supset": "⊃",
    "subseteq": "⊆", "supseteq": "⊇", "subsetneq": "⊊", "supsetneq": "⊋",
    "perp": "⊥", "parallel": "∥", "mid": "∣",
    # logic
    "forall": "∀", "exists": "∃", "nexists": "∄", "neg": "¬", "lnot": "¬",
    "wedge": "∧", "land": "∧", "vee": "∨", "lor": "∨", "therefore": "∴",
    "because": "∵",
    # arrows
    "to": "→", "rightarrow": "→", "leftarrow": "←", "leftrightarrow": "↔",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "iff": "⇔",
    "mapsto": "↦", "uparrow": "↑", "downarrow": "↓",
    # big operators and misc
    "sum": "∑", "prod": "∏", "int": "∫", "iint": "∬", "oint": "∮",
    "partial": "∂", "nabla": "∇", "infty": "∞", "emptyset": "∅",
    "varnothing": "∅", "sqrt": "√", "angle": "∠", "triangle": "△",
    "degree": "°", "prime": "′", "ldots": "…", "cdots": "⋯", "dots": "…",
    "aleph": "ℵ", "hbar": "ℏ", "ell": "ℓ",
    # delimiters
    "langle": "⟨", "rangle": "⟩", "lceil": "⌈", "rceil": "⌉",
    "lfloor": "⌊", "rfloor": "⌋", "lbrace": "{", "rbrace": "}",
    "vert": "|", "Vert": "‖",
}

_BLACKBOARD = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "P": "ℙ", "H": "ℍ"}

#: Commands that only structure the formula and add no glyph of their own.
STRUCTURAL_COMMANDS = {
    "frac", "dfrac", "tfrac", "binom", "sqrt", "left", "right", "middle",
    "big", "Big", "bigg", "Bigg", "mathbf", "mathit", "mathrm", "mathsf",
    "mathtt", "boldsymbol", "operatorname", "text", "textrm", "textnormal",
    "begin", "end", "bmatrix", "pmatrix", "vmatrix", "matrix", "cases",
    "overline", "underline", "vec", "hat", "bar", "dot", "ddot", "tilde",
    "quad", "qquad", "limits", "nolimits", "displaystyle", "textstyle",
    "scriptstyle", "hline", "hspace", "phantom", "color", "underbrace",
    "overbrace", "substack", "not",
}

_CMD_RE = re.compile(r"\\mathbb\{([A-Z])\}|\\text\{([^}]*)\}|\\mathrm\{([^}]*)\}|\\([a-zA-Z]+)")
_STRUCTURAL = set("{}^_&$")


def tex_chars(tex: str, unknown: set[str] | None = None) -> set[str]:
    """Return the Unicode characters a LaTeX snippet needs from the font."""
    chars: set[str] = set()

    def repl(m: re.Match[str]) -> str:
        if m.group(1):
            return _BLACKBOARD.get(m.group(1), m.group(1))
        if m.group(2) is not None or m.group(3) is not None:
            return m.group(2) or m.group(3) or ""
        name = m.group(4)
        if name in LATEX_SYMBOLS:
            return LATEX_SYMBOLS[name]
        if name in STRUCTURAL_COMMANDS:
            return ""
        if unknown is not None:
            unknown.add(name)
        return ""

    flat = _CMD_RE.sub(repl, tex)
    flat = flat.replace("\\\\", "").replace("\\", "")
    for ch in flat:
        if ch in _STRUCTURAL or ch.isspace():
            continue
        chars.add(ch)
    return chars


def document_chars(blocks: list[Block]) -> tuple[set[str], set[str], set[str]]:
    """Return ``(text_chars, math_chars, unknown_commands)`` for a document."""
    text: set[str] = set()
    math: set[str] = set()
    unknown: set[str] = set()
    for block in blocks:
        if block.kind == "matrix":
            if block.payload.get("prefix"):
                math |= tex_chars(block.payload["prefix"], unknown)
            for row in block.payload.get("rows", []):
                for cell in row:
                    math |= tex_chars(cell, unknown)
            continue
        for kind, value in block.segments:
            if kind == "m":
                math |= tex_chars(value, unknown)
            else:
                text |= {ch for ch in value if not ch.isspace()}
    return text, math, unknown


@dataclass
class CoverageReport:
    """What a document needs versus what the font provides."""

    font_path: str
    missing: list[dict[str, str]] = field(default_factory=list)
    coverage: float = 1.0
    total: int = 0
    unknown_commands: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "font_path": self.font_path,
            "missing": self.missing,
            "coverage": round(self.coverage, 4),
            "total": self.total,
            "unknown_commands": self.unknown_commands,
        }


def font_codepoints(path: str | Path) -> set[int]:
    """Set of Unicode code points covered by a font's best cmap."""
    font = TTFont(str(path), fontNumber=0, lazy=True)
    try:
        cmap = font.getBestCmap() or {}
        return set(cmap.keys())
    finally:
        font.close()


def analyze(font_path: str | Path, blocks: list[Block]) -> CoverageReport:
    """Compare document glyph needs against the font cmap."""
    text, math, unknown = document_chars(blocks)
    needed = sorted(text | math)
    cmap = font_codepoints(font_path)
    missing_chars = [ch for ch in needed if ord(ch) not in cmap]
    report = CoverageReport(
        font_path=str(font_path),
        total=len(needed),
        coverage=1.0 if not needed else 1 - len(missing_chars) / len(needed),
        unknown_commands=sorted(unknown),
    )
    for ch in missing_chars:
        try:
            name = unicodedata.name(ch)
        except ValueError:
            name = "UNNAMED"
        report.missing.append({"char": ch, "code": f"U+{ord(ch):04X}", "name": name})
    return report
