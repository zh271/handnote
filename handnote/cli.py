"""Command line interface for handnote."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .capture import build_capture_font
from .config import RenderConfig
from .fonts import SOURCES, ensure_font, list_fonts, resolve_font
from .markdown import parse
from .render import Renderer, flatten
from .symbols import (
    Template,
    build_font,
    build_template,
    merge_fonts,
    simulate_scan,
    write_template,
)


def _overrides(pairs: list[str]) -> dict:
    data: dict[str, object] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--set expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _source(path: str) -> str:
    """Markdown text of ``path``: a Markdown file, a Word .docx, or ``-`` for stdin."""
    if path == "-":
        return sys.stdin.read()
    if Path(path).suffix.lower() in (".docx", ".doc"):
        from .docx import docx_to_markdown

        result = docx_to_markdown(Path(path).read_bytes())
        for note in result.warnings:
            print(f"note: {note}", file=sys.stderr)
        return result.markdown
    return Path(path).read_text(encoding="utf-8")


def _config(args: argparse.Namespace) -> RenderConfig:
    data: dict[str, object] = {}
    if getattr(args, "paper", None):
        from .paper import preset

        data.update(preset(args.paper))
    data.update(_overrides(getattr(args, "set", []) or []))
    data.setdefault("font_path", str(resolve_font(getattr(args, "font", None))))
    return RenderConfig.from_mapping(data)


def cmd_render(args: argparse.Namespace) -> int:
    source = _source(args.input)
    cfg = _config(args)
    renderer = Renderer(cfg)
    blocks = parse(source)
    out = Path(args.out or "out/handnote.pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    suffix = out.suffix.lower()
    if suffix == ".pdf" and cfg.vector_pdf:
        # Vector pages are recorded directly; no page images are needed.
        pages: list = renderer.render_vector(blocks)
        out.write_bytes(renderer.to_vector_pdf(pages))
    else:
        pages = renderer.render(blocks)
        if suffix == ".pdf":
            out.write_bytes(renderer.to_pdf(pages))
        elif len(pages) == 1 and suffix in (".png", ".jpg", ".jpeg"):
            # PNG keeps the transparent background; JPEG has no alpha.
            (pages[0] if suffix == ".png" else flatten(pages[0])).save(out)
        else:
            if out.suffix:
                out = out.with_suffix("")
            out.mkdir(parents=True, exist_ok=True)
            for i, image in enumerate(pages, 1):
                image.save(out / f"page-{i}.png")
    report = renderer.coverage(blocks)
    summary = {
        "out": str(out),
        "pages": len(pages),
        "font": str(renderer.font_path),
        "coverage": report["coverage"],
        "missing": "".join(item["char"] for item in report["missing"]),
        "warnings": renderer.warnings,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    source = _source(args.input)
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
                              cols=args.cols, rows=args.rows, dpi=args.dpi,
                              sets=args.sets)
    print(json.dumps({
        "pdf": str(pdf),
        "spec": str(spec_path),
        "pages": template.pages * template.sets,
        "sets": template.sets,
        "cells": len(template.cells),
        "guide_missing": "".join(template.guide_missing),
    }, ensure_ascii=False, indent=1))
    return 0


def cmd_symbols_build(args: argparse.Namespace) -> int:
    spec = Template.from_dict(json.loads(Path(args.spec).read_text(encoding="utf-8")))
    out = Path(args.out)
    raw = out.with_name(f"{out.stem}-symbols.ttf") if args.merge else out
    report = build_font([Path(p) for p in args.scans], spec, raw,
                        threshold=args.threshold, keep=args.keep)
    if args.merge and report["glyphs"]:
        merged = merge_fonts(args.merge, raw, out, replace=args.replace)
        report["merged_into"] = args.merge
        report["merged_added"] = merged["added"]
        report["merged_replaced"] = merged["replaced_chars"]
        report["merged_alternates"] = merged["alternates"]
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


def cmd_symbols_capture(args: argparse.Namespace) -> int:
    spec = Template.from_dict(json.loads(Path(args.spec).read_text(encoding="utf-8")))
    raw_text = (sys.stdin.read() if args.capture == "-"
                else Path(args.capture).read_text(encoding="utf-8"))
    data = json.loads(raw_text)
    if isinstance(data, dict) and "capture" in data:
        data = data["capture"]
    out = Path(args.out)
    raw = out.with_name(f"{out.stem}-symbols.ttf") if args.merge else out
    report = build_capture_font(data, spec, raw, size=args.size, keep=args.keep,
                                pressure=args.pressure)
    if args.merge and report["glyphs"]:
        merged = merge_fonts(args.merge, raw, out, replace=args.replace)
        report["merged_into"] = args.merge
        report["merged_added"] = merged["added"]
        report["merged_replaced"] = merged["replaced_chars"]
        report["merged_alternates"] = merged["alternates"]
        report["font"] = str(out)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["glyphs"] else 1


def cmd_fonts(args: argparse.Namespace) -> int:
    if args.action == "fetch":
        for key in args.names or list(SOURCES):
            print(ensure_font(key))
        return 0
    print(json.dumps(list_fonts(), ensure_ascii=False, indent=1))
    return 0


def _read_json(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return json.loads(text)


def _targets(spec: str) -> str:
    from .hanzi_data import gb2312_level1

    if spec == "gb1":
        return "".join(gb2312_level1())
    return Path(spec).read_text(encoding="utf-8")


def cmd_hanzi(args: argparse.Namespace) -> int:
    from . import hanzi_data
    from .hanzi_font import build_hanzi_font

    command = args.hanzi_command
    if command == "fetch":
        print(hanzi_data.fetch(force=args.force))
        return 0
    if command == "build":
        out = Path(args.out)
        raw = out.with_name(out.stem + "-hanzi.ttf") if args.merge else out
        report = build_hanzi_font(_read_json(args.capture), raw, pressure=args.pressure)
        if args.merge and report["glyphs"]:
            merged = merge_fonts(args.merge, raw, out, replace=args.replace,
                                 family_suffix=" Hand")
            report.update(merged_into=args.merge, merged_added=merged["added"],
                          merged_replaced=merged["replaced"], font=str(out))
        report.pop("chars", None)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if report["glyphs"] else 1

    from .model import torch_available

    if not torch_available():
        raise SystemExit("this command needs the model extra: pip install -e \".[model]\" "
                         "--extra-index-url https://download.pytorch.org/whl/cpu")
    from .model import weights

    def show(info: dict) -> None:
        print(json.dumps(info, ensure_ascii=False), file=sys.stderr, flush=True)

    if command == "model-fetch":
        def fetched(what: str, done: int, total: int) -> None:
            if what == "content" or done == total or done % (32 << 20) < (1 << 20):
                show({"file": what, "done": done, "total": total})

        hanzi_data.fetch()
        print(json.dumps(weights.fetch(fetched), ensure_ascii=False, indent=1))
        return 0
    if command == "train":
        from .model.train import finetune

        def trained(info: dict) -> None:
            if info["iter"] % 25 == 0 or info["iter"] == info["iters"]:
                show({k: round(v, 3) if isinstance(v, float) else v for k, v in info.items()})

        report = finetune(_read_json(args.capture), args.out, iters=args.iters, progress=trained)
        report.pop("log", None)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    from .model.generate import generate

    out = Path(args.out)
    result = generate(_read_json(args.capture), args.model, _targets(args.targets),
                      sets=args.sets, cache_path=out.with_suffix(".partial.json"),
                      progress=lambda info: show(info) if info["done"] % 64 < 16 else None)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result["capture"], ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out": str(out), **result["report"]}, ensure_ascii=False, indent=1))
    return 0


def cmd_paper(args: argparse.Namespace) -> int:
    """Turn a photo of blank paper into a page background and measure its lines."""
    from PIL import Image

    from .paper import PRESETS, detect_page, detect_rules, flatten_scan

    if args.paper_command == "presets":
        for key, (label, fields) in PRESETS.items():
            print(f"{key:14s} {label}  {fields.get('paper_style', 'plain')}")
        return 0
    with Image.open(args.image) as img:
        img = img.convert("RGB")
        corners = (json.loads(args.corners) if args.corners else detect_page(img))
        sheet = flatten_scan(img, corners, (args.width, args.height),
                             evenness=args.evenness, whiten=args.whiten, gray=args.gray)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    rules = detect_rules(sheet)
    report = {"out": str(out), "corners": corners, "rules": rules,
              "set": ["paper_style=image", f"paper_image={out}"]}
    if rules:
        report["set"] += [f"rule_pitch={rules['pitch']}", f"rule_top={rules['top']}"]
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


def cmd_docx(args: argparse.Namespace) -> int:
    from .docx import docx_to_markdown

    result = docx_to_markdown(Path(args.input).read_bytes())
    for note in result.warnings:
        print(f"note: {note}", file=sys.stderr)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(result.markdown, encoding="utf-8")
    else:
        sys.stdout.write(result.markdown)
    return 0


def cmd_line(args: argparse.Namespace) -> int:
    """Experimental DiffBrush text lines (English only)."""
    from .linegen import available, weights

    def show(info: dict) -> None:
        print(json.dumps(info, ensure_ascii=False), file=sys.stderr, flush=True)

    command = args.line_command
    if command == "fetch":
        def fetched(what: str, done: int, total: int) -> None:
            if done == total or done % (64 << 20) < (1 << 20):
                show({"file": what, "done": done, "total": total})

        print(json.dumps(weights.fetch(fetched), ensure_ascii=False, indent=1))
        return 0
    if not available():
        raise SystemExit("this command needs the linegen extra: pip install -e \".[linegen]\" "
                         "(see README for the GPU builds of torch)")
    if command == "bench":
        from .linegen.bench import run

        print(json.dumps(run(args.device, steps=args.steps, repeats=args.repeats,
                             half=args.half), ensure_ascii=False, indent=1))
        return 0
    from .linegen.generate import generate_lines, stack
    from .linegen.style import check_text, wrap

    text = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")
    lines = wrap(text, args.width)
    if not lines:
        raise SystemExit("nothing to write")
    try:
        for line in lines:
            check_text(line)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    images = generate_lines(lines, args.style, device=args.device, steps=args.steps,
                            seed=args.seed, half=args.half, progress=show)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    stack(images).save(out)
    print(json.dumps({"out": str(out), "lines": len(images)}, ensure_ascii=False))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .webui.server import serve

    try:
        serve(host=args.host, port=args.port, open_browser=args.open)
    except OSError as exc:
        print(f"无法启动 Web UI：{exc}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="handnote", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--font", help="TTF/OTF path (default: cached handwriting font)")
        p.add_argument("--set", action="append", metavar="KEY=VALUE",
                       help="override any RenderConfig field")

    r = sub.add_parser("render", help="render Markdown to PNG or PDF")
    r.add_argument("input", help="Markdown or Word (.docx) file, or - for stdin")
    r.add_argument("-o", "--out", default="out/handnote.pdf",
                   help="output .pdf, single .png, or a directory")
    r.add_argument("--paper", metavar="PRESET",
                   help="paper preset (see `handnote paper presets`); --set overrides it")
    add_common(r)
    r.set_defaults(func=cmd_render)

    a = sub.add_parser("analyze", help="report glyphs missing from the font")
    a.add_argument("input", help="Markdown or Word (.docx) file, or - for stdin")
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
    st.add_argument("--sets", type=int, default=1,
                    help="print the grid this many times, one per handwritten set")
    st.set_defaults(func=cmd_symbols_template)

    sb = ssub.add_parser("build", help="trace scanned pages into a TTF")
    sb.add_argument("scans", nargs="+",
                    help="scan images; every template's worth of pages is one set")
    sb.add_argument("-m", "--spec", required=True, help="template JSON")
    sb.add_argument("-o", "--out", default="out/handnote-symbols.ttf")
    sb.add_argument("--threshold", type=int, default=128)
    sb.add_argument("--merge", metavar="BASE_FONT",
                    help="merge the traced symbols into a base font (glyf-based)")
    sb.add_argument("--keep", type=float, default=0.3,
                    help="share of size/position error kept after aligning to the guide "
                         "(0 = snap to guide, 1 = raw)")
    sb.add_argument("--replace", action="store_true",
                    help="with --merge: handwritten glyphs replace the base font's own "
                         "(digits, letters...); unwritten characters are kept")
    sb.set_defaults(func=cmd_symbols_build)

    sc = ssub.add_parser("capture", help="build a font from a browser capture JSON")
    sc.add_argument("capture", help="capture JSON, or - for stdin")
    sc.add_argument("-m", "--spec", required=True, help="template JSON")
    sc.add_argument("-o", "--out", default="out/handnote-symbols.ttf")
    sc.add_argument("--size", type=int, default=256, help="raster size per cell")
    sc.add_argument("--merge", metavar="BASE_FONT")
    sc.add_argument("--keep", type=float, default=0.3,
                    help="share of size/position error kept after aligning to the guide")
    sc.add_argument("--replace", action="store_true",
                    help="with --merge: handwritten glyphs replace the base font's own")
    sc.add_argument("--pressure", type=float, default=1.0,
                    help="pen pressure gain: 0 = uniform width, 1 = default, 2 = strong")
    sc.set_defaults(func=cmd_symbols_capture)

    ss = ssub.add_parser("simulate", help="fill a template with a font for testing")
    ss.add_argument("-m", "--spec", required=True)
    ss.add_argument("-o", "--out", default="out/scans")
    ss.add_argument("--font")
    ss.set_defaults(func=cmd_symbols_simulate)

    f = sub.add_parser("fonts", help="list or download bundled fonts")
    f.add_argument("action", choices=["list", "fetch"], nargs="?", default="list")
    f.add_argument("names", nargs="*", help=f"font keys: {', '.join(SOURCES)}")
    f.set_defaults(func=cmd_fonts)

    h = sub.add_parser("hanzi", help="handwritten Chinese characters (fine-tuned SDT model)")
    hsub = h.add_subparsers(dest="hanzi_command", required=True)
    hf = hsub.add_parser("fetch", help="download stroke data for the trace guide (makemeahanzi)")
    hf.add_argument("--force", action="store_true", help="download again")
    hm = hsub.add_parser("model-fetch", help="download the SDT weights and content glyphs")
    ht = hsub.add_parser("train", help="fine-tune SDT on a hanzi capture (CPU, ~20 min)")
    ht.add_argument("capture", help="hanzi capture JSON from /hanzi, or - for stdin")
    ht.add_argument("-o", "--out", default="out/hanzi-model.pth")
    ht.add_argument("--iters", type=int, default=300, help="training steps (~3.5 s each)")
    hg = hsub.add_parser("generate", help="write characters with a fine-tuned model")
    hg.add_argument("capture", help="the capture the model was trained on")
    hg.add_argument("--model", required=True, help="model from `hanzi train`")
    hg.add_argument("-o", "--out", default="out/hanzi-generated.json")
    hg.add_argument("--targets", default="gb1",
                    help="gb1 (GB2312 level 1, 3755 chars) or a text file whose characters "
                         "to write (e.g. your notes)")
    hg.add_argument("--sets", type=int, default=1, help="variants per character (1-8)")
    hb = hsub.add_parser("build", help="build a font from a hanzi capture or generated JSON")
    hb.add_argument("capture", help="hanzi JSON, or - for stdin")
    hb.add_argument("-o", "--out", default="out/handnote-hanzi.ttf")
    hb.add_argument("--merge", metavar="BASE_FONT",
                    help="merge into a base font (glyf-based), e.g. ZhiMangXing")
    hb.add_argument("--replace", action="store_true",
                    help="with --merge: replace the base font's own glyphs for these characters")
    hb.add_argument("--pressure", type=float, default=1.0,
                    help="pen pressure gain: 0 = uniform width, 1 = default, 2 = strong")
    for p in (hf, hm, ht, hg, hb):
        p.set_defaults(func=cmd_hanzi)

    pa = sub.add_parser("paper", help="paper presets and scanned paper backgrounds")
    pasub = pa.add_subparsers(dest="paper_command", required=True)
    pasub.add_parser("presets", help="list paper presets")
    ps = pasub.add_parser("scan", help="flatten a photo of blank paper into a background")
    ps.add_argument("image", help="photo or scan of the paper")
    ps.add_argument("-o", "--out", default="out/paper.png")
    ps.add_argument("--width", type=int, default=1240, help="page width in pixels")
    ps.add_argument("--height", type=int, default=1754, help="page height in pixels")
    ps.add_argument("--corners", help="JSON [[x,y]*4] clockwise from top-left (default: detect)")
    ps.add_argument("--evenness", type=float, default=0.8, help="0-1, remove uneven light")
    ps.add_argument("--whiten", type=float, default=0.6, help="0-1, brighten the paper")
    ps.add_argument("--gray", action="store_true", help="drop the colour")
    pa.set_defaults(func=cmd_paper)

    d = sub.add_parser("docx", help="convert a Word .docx into handnote Markdown")
    d.add_argument("input", help=".docx file")
    d.add_argument("-o", "--out", help="output .md (default: stdout)")
    d.set_defaults(func=cmd_docx)

    li = sub.add_parser("line", help="experimental: whole English lines with DiffBrush")
    lsub = li.add_subparsers(dest="line_command", required=True)
    lsub.add_parser("fetch", help="download the DiffBrush checkpoint, SD1.5 VAE and glyphs")
    lg = lsub.add_parser("generate", help="write text lines imitating a reference line")
    lg.add_argument("--style", required=True,
                    help="reference line image, or a folder (first image wider than 512 px)")
    src = lg.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", help="text to write (wrapped into lines)")
    src.add_argument("--file", help="UTF-8 text file to write")
    lg.add_argument("-o", "--out", default="out/lines.png")
    lg.add_argument("--width", type=int, default=60, help="characters per line (max 70)")
    lb = lsub.add_parser("bench", help="time one line here and estimate other GPUs")
    lb.add_argument("--repeats", type=int, default=2)
    for p in (lg, lb):
        p.add_argument("--device", default="auto", help="auto, cuda (also ROCm), dml or cpu")
        p.add_argument("--steps", type=int, default=50, help="DDIM steps (fewer = faster)")
        p.add_argument("--half", action="store_true", help="float16 (GPU only)")
    lg.add_argument("--seed", type=int, default=0)
    li.set_defaults(func=cmd_line)

    w = sub.add_parser("serve", help="start the local web UI")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8765,
                   help="preferred port; the next free one is used if it is taken")
    w.add_argument("--open", action="store_true", help="open the UI in the browser")
    w.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
