"""Font lookup and export: ``find_font``, ``export_name``, ``handnote fonts export``."""

import json
import os
import re
import time
from pathlib import Path

import pytest

from handnote import fonts
from handnote.fonts import SOURCES, export_name, find_font, list_fonts, upload_dir

STAMP = re.compile(r"^handnote-handwriting-\d{8}-\d{4}\.ttf$")


def _font(folder: Path, name: str, age: float = 0.0, data: bytes | None = None) -> Path:
    """A placeholder font file ``age`` seconds old (lookups never parse it)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data or (b"\0\1\0\0" + name.encode() * 8))
    stamp = time.time() - 900 - age
    os.utime(path, (stamp, stamp))
    return path


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """An isolated font cache holding only the zhimang file."""
    folder = tmp_path / "cache"
    _font(folder, SOURCES["zhimang"][0])
    monkeypatch.setattr(fonts, "CACHE_DIR", folder)
    return folder


def test_upload_dir_follows_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert upload_dir() == tmp_path / "var" / "uploads"


def test_default_is_the_newest_merged_font(tmp_path, cache):
    up = tmp_path / "up"
    _font(up, "merged-old.ttf", age=50)
    newest_merged = _font(up, "merged-new.ttf", age=20)
    _font(up, "upload.ttf", age=0)  # newer, but not handwritten
    _font(up, "capture-x.ttf", age=-10)  # raw symbol fonts are never picked
    assert find_font(None, [up]) == newest_merged
    assert find_font("", [up]) == newest_merged


def test_default_falls_back_to_the_newest_upload(tmp_path, cache):
    up = tmp_path / "up"
    _font(up, "a.ttf", age=30)
    newest = _font(up, "b.otf", age=10)
    _font(up, "symbols-x.ttf", age=0)
    assert find_font(None, [up]) == newest


def test_default_without_uploads_is_an_error(tmp_path, cache):
    with pytest.raises(FileNotFoundError):
        find_font(None, [tmp_path / "missing"])
    with pytest.raises(FileNotFoundError):
        find_font(None)


def test_font_keys_must_be_cached(cache):
    assert find_font("zhimang") == cache / SOURCES["zhimang"][0]
    with pytest.raises(FileNotFoundError, match="fetch"):
        find_font("caveat")


def test_names_stems_labels_and_paths(tmp_path, cache):
    up = tmp_path / "up"
    merged = _font(up, "merged-abc.ttf")
    other = _font(up, "mine.otf")
    label = next(f["name"] for f in list_fonts([up]) if f["path"] == str(merged))
    assert label != merged.name
    assert find_font("merged-abc.ttf", [up]) == merged
    assert find_font("merged-abc", [up]) == merged
    assert find_font(label, [up]) == merged
    assert find_font("mine", [up]) == other
    assert find_font("ZhiMangXing-Regular", [up]) == cache / SOURCES["zhimang"][0]
    outside = _font(tmp_path / "elsewhere", "loose.ttf")
    assert find_font(str(outside)) == outside
    with pytest.raises(FileNotFoundError):
        find_font("no-such-font", [up])
    with pytest.raises(FileNotFoundError):
        find_font(str(tmp_path / "nope.ttf"), [up])


def test_export_name(tmp_path):
    merged = _font(tmp_path, "merged-1234abcd.ttf")
    name = export_name(merged)
    assert STAMP.match(name), name
    expected = time.strftime("%Y%m%d-%H%M", time.localtime(merged.stat().st_mtime))
    assert expected in name
    assert export_name(_font(tmp_path, "mine.otf")) == "mine.otf"
    assert export_name(_font(tmp_path, "ZhiMangXing-Regular.ttf")) == "ZhiMangXing-Regular.ttf"


# -- CLI ------------------------------------------------------------------------
def _cli(args, capsys):
    from handnote.cli import main

    assert main(["fonts", *args]) == 0
    return json.loads(capsys.readouterr().out)


def test_cli_export_defaults_to_newest_handwritten_font(tmp_path, cache, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    up = tmp_path / "var" / "uploads"
    _font(up, "merged-old.ttf", age=50)
    merged = _font(up, "merged-new.ttf", age=10)
    report = _cli(["export"], capsys)
    out = Path(report["out"])
    assert Path(report["from"]) == merged
    assert out.parent == Path("out") and STAMP.match(out.name)
    assert (tmp_path / out).read_bytes() == merged.read_bytes()


def test_cli_export_named_font_to_file_or_folder(tmp_path, cache, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    report = _cli(["export", "zhimang", "-o", str(tmp_path / "copy" / "font.ttf")], capsys)
    target = tmp_path / "copy" / "font.ttf"
    assert Path(report["out"]) == target
    assert target.read_bytes() == (cache / SOURCES["zhimang"][0]).read_bytes()
    report = _cli(["export", "zhimang", "-o", str(tmp_path / "folder")], capsys)
    assert Path(report["out"]) == tmp_path / "folder" / SOURCES["zhimang"][0]
    assert Path(report["out"]).is_file()


def test_cli_export_errors(tmp_path, cache, monkeypatch):
    from handnote.cli import main

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        main(["fonts", "export"])  # nothing built or uploaded yet
    with pytest.raises(SystemExit):
        main(["fonts", "export", "no-such-font"])
    with pytest.raises(SystemExit):
        main(["fonts", "export", "zhimang", "caveat"])
    assert not (tmp_path / "out").exists()


def test_cli_list_includes_uploads(tmp_path, cache, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    merged = _font(tmp_path / "var" / "uploads", "merged-abc.ttf")
    listed = _cli(["list"], capsys)
    sources = {f["path"]: f["source"] for f in listed}
    assert sources[str(merged)] == "upload"
    assert sources[str(cache / SOURCES["zhimang"][0])] == "cache"
