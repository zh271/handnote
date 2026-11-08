"""Command line interface for handnote."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import RenderConfig
from .fonts import SOURCES, ensure_font, list_fonts, resolve_font
from .markdown import parse
from .render import Renderer
from .symbols import (Template, build_font, build_template, merge_fonts,
                      simulate_scan, write_template)


def _overrides(pairs: list[str]) -> dict:
    data: dict[str, object] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--set expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _config(args: argparse.Namespace) -> RenderConfig:
    data = _overrides(getattr(args, "set", []) or [])
    data.setdefault("font_path", str(resolve_font(getattr(args, "font", None))))
    return RenderConfig.from_mapping(data)


def cmd_render(args: argparse.Namespace) -> int:
    source = Path(args.input).read_text(encoding="utf-8") if args.input != "-" else sys.stdin.read()
    cfg = _config(args)
    renderer = Renderer(cfg)
    blocks = parse(source)
    images = renderer.render(blocks)
    out = Path(args.out or "out/handnote.pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".pdf":
        out.write_bytes(renderer.to_pdf(images))
    elif len(images) == 1 and out.suffix.lower() in (".png", ".jpg", ".jpeg"):
        images[0].convert("RGB").save(out)
    else:
        if out.suffix:
            out = out.with_suffix("")
        out.mkdir(parents=True, exist_ok=True)
        for i, image in enumerate(images, 1):
            image.convert("RGB").save(out / f"page-{i}.png")
    report = renderer.coverage(blocks)
    summary = {
        "out": str(out),
        "pages": len(images),
        "font": str(renderer.font_path),
        "coverage": report["coverage"],
        "missing": "".join(item["char"] for item in report["missing"]),
        "warnings": renderer.warnings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    source = Path(args.input).read_text(encoding="utf-8") if args.input != "-" else sys.stdin.read()
    cfg = _config(args)
    renderer = Renderer(cfg)
    report = renderer.coverage(parse(source))
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if not report["missing"] else 2


def cmd_symbols_template(args: argparse.Namespace) -> int:
    chars = args.chars if args.chars else None
    pdf = Path(args.out)
    spec_path = Path(args.json) if args.json else pdf.with_suffix(".json")
    template = write_template(pdf, spec_path, chars=chars, cell_mm=args.cell_mm,
                              cols=args.cols, rows=args.rows, dpi=args.dpi)
    print(json.dumps({
        "pdf": str(pdf),
        "spec": str(spec_path),
        "pages": template.pages,
        "cells": len(template.cells),
        "guide_missing": "".join(template.guide_missing),
    }, ensure_ascii=False, indent=1))
    return 0


def cmd_symbols_build(args: argparse.Namespace) -> int:
    spec = Template.from_dict(json.loads(Path(args.spec).read_text(encoding="utf-8")))
    out = Path(args.out)
    raw = out.with_name(f"{out.stem}-symbols.ttf") if args.merge else out
    report = build_font([Path(p) for p in args.scans], spec, raw,
                        threshold=args.threshold)
    if args.merge and report["glyphs"]:
        merged = merge_fonts(args.merge, raw, out)
        report["merged_into"] = args.merge
        report["merged_added"] = merged["added"]
        report["font"] = str(out)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["glyphs"] else 1


def cmd_symbols_simulate(args: argparse.Namespace) -> int:
    spec = Template.from_dict(json.loads(Path(args.spec).read_text(encoding="utf-8")))
    chars = "".join(cell.char for cell in spec.cells)
    pages, template = build_template(
        chars,
        cell_mm=spec.cell_px / spec.dpi * 25.4,
        cols=spec.cols,
        rows=spec.rows,
        dpi=spec.dpi,
    )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = [out_dir / f"scan-{i + 1}.jpg" for i in range(len(pages))]
    font = resolve_font(args.font)
    written = simulate_scan(pages, template, font, targets)
    print(json.dumps({"scans": [str(p) for p in written]}, ensure_ascii=False, indent=1))
    return 0


def cmd_fonts(args: argparse.Namespace) -> int:
    if args.action == "fetch":
        for key in args.names or list(SOURCES):
            print(ensure_font(key))
        return 0
    print(json.dumps(list_fonts(), ensure_ascii=False, indent=1))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .webui.server import serve

    serve(host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="handnote", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--font", help="TTF/OTF path (default: cached handwriting font)")
        p.add_argument("--set", action="append", metavar="KEY=VALUE",
                       help="override any RenderConfig field")

    r = sub.add_parser("render", help="render Markdown to PNG or PDF")
    r.add_argument("input", help="Markdown file, or - for stdin")
    r.add_argument("-o", "--out", default="out/handnote.pdf",
                   help="output .pdf, single .png, or a directory")
    add_common(r)
    r.set_defaults(func=cmd_render)

    a = sub.add_parser("analyze", help="report glyphs missing from the font")
    a.add_argument("input", help="Markdown file, or - for stdin")
    add_common(a)
    a.set_defaults(func=cmd_analyze)

    s = sub.add_parser("symbols", help="symbol template and font building")
    ssub = s.add_subparsers(dest="symbol_command", required=True)

    st = ssub.add_parser("template", help="write a printable symbol grid")
    st.add_argument("-o", "--out", default="out/math-symbols.pdf")
    st.add_argument("--json", help="spec path (default: next to the PDF)")
    st.add_argument("--chars", help="explicit character set (default: built-in groups)")
    st.add_argument("--cell-mm", type=float, default=24.0)
    st.add_argument("--cols", type=int, default=7)
    st.add_argument("--rows", type=int, default=10)
    st.add_argument("--dpi", type=int, default=300)
    st.set_defaults(func=cmd_symbols_template)

    sb = ssub.add_parser("build", help="trace scanned pages into a TTF")
    sb.add_argument("scans", nargs="+", help="scan images, in page order")
    sb.add_argument("-m", "--spec", required=True, help="template JSON")
    sb.add_argument("-o", "--out", default="out/handnote-symbols.ttf")
    sb.add_argument("--threshold", type=int, default=128)
    sb.add_argument("--merge", metavar="BASE_FONT",
                    help="merge the traced symbols into a base font (glyf-based)")
    sb.set_defaults(func=cmd_symbols_build)

    ss = ssub.add_parser("simulate", help="fill a template with a font for testing")
    ss.add_argument("-m", "--spec", required=True)
    ss.add_argument("-o", "--out", default="out/scans")
    ss.add_argument("--font")
    ss.set_defaults(func=cmd_symbols_simulate)

    f = sub.add_parser("fonts", help="list or download bundled fonts")
    f.add_argument("action", choices=["list", "fetch"], nargs="?", default="list")
    f.add_argument("names", nargs="*", help=f"font keys: {', '.join(SOURCES)}")
    f.set_defaults(func=cmd_fonts)

    w = sub.add_parser("serve", help="start the local web UI")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8765)
    w.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
