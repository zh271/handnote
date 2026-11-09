"""Self-update for git checkouts, used by ``start.bat`` before launching.

Fetches the tracked branch and fast-forwards when the checkout is behind. Anything
unusual (no git, no upstream, offline, local commits, conflicting edits) is
reported and skipped, so a failed update never blocks startup. Stdlib only: it
runs before dependencies are installed.

Exit codes: 0 = nothing changed, 3 = updated (caller should restart itself).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

UPDATED = 3
FETCH_TIMEOUT = 20.0
_PROXY_ENV = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


def _git(root: Path, *args: str, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=env,
    )


def proxy_args(root: Path, remote: str) -> list[str]:
    """Return ``-c http.proxy=...`` for ``git fetch`` when only the system proxy is set.

    pip and urllib read the Windows system proxy from the registry, git does not; with
    a VPN in "system proxy" mode (no TUN) git would fail to reach GitHub. Explicit
    proxy environment variables and git's own ``http.proxy`` config take precedence.

    Args:
        root: Repository root.
        remote: Remote name whose URL is fetched.

    Returns:
        Extra git arguments, possibly empty.
    """
    if any(os.environ.get(name) for name in _PROXY_ENV):
        return []
    url = _git(root, "remote", "get-url", remote).stdout.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return []
    if _git(root, "config", "--get-urlmatch", "http.proxy", url).returncode == 0:
        return []
    proxies = urllib.request.getproxies()
    proxy = proxies.get(parsed.scheme) or proxies.get("all")
    if not proxy or urllib.request.proxy_bypass(parsed.hostname):
        return []
    return ["-c", f"http.proxy={proxy}"]


def check_and_update(root: Path, fetch_timeout: float = FETCH_TIMEOUT) -> tuple[bool, str]:
    """Fast-forward ``root`` to its upstream branch if it is behind.

    Args:
        root: Repository root.
        fetch_timeout: Seconds to wait for ``git fetch`` before giving up.

    Returns:
        ``(updated, message)``; ``updated`` is True only when new commits were applied.
    """
    if not (root / ".git").exists():
        return False, "不是 git 克隆（例如下载的 ZIP），跳过自动更新；用 git clone 安装可自动更新"
    if shutil.which("git") is None:
        return False, "未找到 git，跳过自动更新"
    try:
        upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if upstream.returncode != 0:
            return False, "当前分支没有跟踪远程分支，跳过自动更新"
        upstream_name = upstream.stdout.strip()
        remote = upstream_name.split("/", 1)[0]
        fetch = _git(root, *proxy_args(root, remote), "fetch", "--quiet", remote,
                     timeout=fetch_timeout)
        if fetch.returncode != 0:
            return False, "检查更新失败（网络不通？），跳过"
        behind = _git(root, "rev-list", "--count", "HEAD..@{u}")
        ahead = _git(root, "rev-list", "--count", "@{u}..HEAD")
        n_behind, n_ahead = int(behind.stdout.strip() or 0), int(ahead.stdout.strip() or 0)
        if n_behind == 0:
            return False, "已是最新版本"
        if n_ahead:
            return False, (f"远程有 {n_behind} 个新提交，但本地有 {n_ahead} 个未推送提交，"
                           "跳过自动更新")
        merge = _git(root, "merge", "--ff-only", "--quiet", "@{u}")
        if merge.returncode != 0:
            return False, f"远程有 {n_behind} 个新提交，但与本地改动冲突，跳过自动更新"
    except subprocess.TimeoutExpired:
        return False, "检查更新超时，跳过"
    except OSError as exc:
        return False, f"检查更新出错（{exc}），跳过"
    return True, f"已更新 {n_behind} 个提交（{upstream_name}）"


def main() -> int:
    """Run the update check for the repository that contains this package."""
    root = Path(__file__).resolve().parent.parent
    updated, message = check_and_update(root)
    print(message, flush=True)
    return UPDATED if updated else 0


if __name__ == "__main__":
    sys.exit(main())
