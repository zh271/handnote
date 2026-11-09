"""Windows launcher: line endings and the git self-update it runs."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from handnote import update
from handnote.update import check_and_update

ROOT = Path(__file__).resolve().parent.parent
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
    GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
)


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, env=_ENV, check=True,
                         capture_output=True, text=True)
    return out.stdout.strip()


def _commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)


@pytest.fixture
def repos(tmp_path):
    """An upstream repo with one commit and a clone tracking it."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _commit(origin, "a.txt", "one\n")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    return origin, clone


def test_start_bat_is_crlf():
    """cmd.exe misreads LF-only batch files with UTF-8 text; .gitattributes must force CRLF."""
    assert "*.bat text eol=crlf" in (ROOT / ".gitattributes").read_text(encoding="utf-8")
    data = (ROOT / "start.bat").read_bytes()
    if (ROOT / ".git").exists():
        assert data.count(b"\n") == data.count(b"\r\n")


@needs_git
def test_up_to_date(repos):
    _, clone = repos
    assert check_and_update(clone) == (False, "已是最新版本")


@needs_git
def test_fast_forwards_when_behind(repos):
    origin, clone = repos
    _commit(origin, "b.txt", "two\n")
    updated, message = check_and_update(clone)
    assert updated, message
    assert (clone / "b.txt").read_text(encoding="utf-8") == "two\n"
    assert _git(clone, "rev-parse", "HEAD") == _git(origin, "rev-parse", "HEAD")


@needs_git
def test_keeps_unrelated_local_edits(repos):
    origin, clone = repos
    (clone / "a.txt").write_text("mine\n", encoding="utf-8")
    _commit(origin, "b.txt", "two\n")
    updated, _ = check_and_update(clone)
    assert updated
    assert (clone / "a.txt").read_text(encoding="utf-8") == "mine\n"


@needs_git
def test_skips_conflicting_local_edits(repos):
    origin, clone = repos
    (clone / "a.txt").write_text("mine\n", encoding="utf-8")
    _commit(origin, "a.txt", "theirs\n")
    updated, _ = check_and_update(clone)
    assert not updated
    assert (clone / "a.txt").read_text(encoding="utf-8") == "mine\n"


@needs_git
def test_skips_when_local_commits(repos):
    origin, clone = repos
    _commit(clone, "c.txt", "local\n")
    _commit(origin, "b.txt", "two\n")
    updated, _ = check_and_update(clone)
    assert not updated
    assert (clone / "c.txt").exists() and not (clone / "b.txt").exists()


@needs_git
def test_skips_when_remote_unreachable(repos, tmp_path):
    _, clone = repos
    _git(clone, "remote", "set-url", "origin", str(tmp_path / "missing"))
    assert check_and_update(clone, fetch_timeout=10)[0] is False


@needs_git
def test_skips_without_upstream(repos):
    _, clone = repos
    _git(clone, "checkout", "-q", "-b", "feature")
    assert check_and_update(clone)[0] is False


def test_skips_outside_git(tmp_path):
    assert check_and_update(tmp_path)[0] is False


@pytest.fixture
def system_proxy(monkeypatch):
    """No proxy env vars or git config; the OS reports a system proxy."""
    for name in update._PROXY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(update.urllib.request, "getproxies",
                        lambda: {"https": "http://127.0.0.1:3067"})
    monkeypatch.setattr(update.urllib.request, "proxy_bypass", lambda host: False)


@needs_git
def test_system_proxy_passed_to_git(repos, system_proxy):
    _, clone = repos
    _git(clone, "remote", "set-url", "origin", "https://github.com/zh271/handnote")
    assert update.proxy_args(clone, "origin") == ["-c", "http.proxy=http://127.0.0.1:3067"]


@needs_git
def test_explicit_proxy_settings_win(repos, system_proxy, monkeypatch):
    _, clone = repos
    _git(clone, "remote", "set-url", "origin", "https://github.com/zh271/handnote")
    _git(clone, "config", "http.proxy", "http://10.0.0.1:8080")
    assert update.proxy_args(clone, "origin") == []
    _git(clone, "config", "--unset", "http.proxy")
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:8080")
    assert update.proxy_args(clone, "origin") == []


@needs_git
def test_no_proxy_for_local_or_bypassed_remote(repos, system_proxy, monkeypatch):
    _, clone = repos
    assert update.proxy_args(clone, "origin") == []  # local path remote
    _git(clone, "remote", "set-url", "origin", "https://git.corp.example/handnote")
    monkeypatch.setattr(update.urllib.request, "proxy_bypass", lambda host: True)
    assert update.proxy_args(clone, "origin") == []


@needs_git
def test_fetch_uses_proxy_args(repos, monkeypatch):
    origin, clone = repos
    _commit(origin, "b.txt", "two\n")
    calls = []
    real_git = update._git

    def recording_git(root, *args, **kwargs):
        calls.append(args)
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(update, "_git", recording_git)
    monkeypatch.setattr(update, "proxy_args", lambda root, remote: ["-c", "http.proxy=http://p:1"])
    assert check_and_update(clone)[0]
    fetch = next(args for args in calls if "fetch" in args)
    assert fetch[:2] == ("-c", "http.proxy=http://p:1")
