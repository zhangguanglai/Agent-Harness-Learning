"""检查本机 DSH 与 npm 上的版本，判断是否需要更新。

用法：python scripts/check_dsh_version.py
退出码：0 已是最新；1 有更新可升；2 无法确定（网络/环境）。

只读：不安装、不改 profile。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

REGISTRY = "https://registry.npmjs.org/-/package/@deepseek-ai/dsh/dist-tags"


def installed_cli() -> str | None:
    exe = shutil.which("dsh")
    if not exe:
        return None
    cmd = ["cmd", "/c", exe, "--version"] if exe.lower().endswith((".cmd", ".bat")) else [exe, "--version"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60).stdout
        return out.strip().splitlines()[0].strip() or None
    except Exception:  # noqa: BLE001
        return None


def desktop_version() -> str | None:
    anchor = Path(__file__).resolve().parents[1]
    candidates = []
    import os

    install = os.environ.get("DSH_INSTALL_DIR")
    if install:
        candidates.append(Path(install))
    candidates.append(anchor)
    for start in candidates:
        path = start
        for _ in range(8):
            manifest = path / "package.json"
            if manifest.is_file():
                try:
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    payload = {}
                if payload.get("name") == "@deepseek-ai/dsh-desktop":
                    return payload.get("version")
            path = path.parent
    return None


def dist_tags() -> dict[str, str]:
    request = urllib.request.Request(REGISTRY, headers={"accept": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def version_key(version: str) -> tuple:
    """粗略 semver 排序：0.2.2-rc.5 < 0.2.2 < 0.2.3-alpha.2 < 0.2.3。"""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$", version.strip())
    if not match:
        return (0, 0, 0, 0)
    major, minor, patch, pre = int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4)
    if pre is None:
        return (major, minor, patch, 1)
    return (major, minor, patch, 0, pre)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    cli = installed_cli()
    desktop = desktop_version()
    try:
        tags = dist_tags()
    except Exception as exc:  # noqa: BLE001
        print(f"无法查询 npm registry：{exc}")
        return 2

    latest, alpha = tags.get("latest"), tags.get("alpha")
    print(f"本机 CLI      : {cli or '未找到'}")
    print(f"本机桌面版    : {desktop or '未检测（Desktop 版本见应用「关于」）'}")
    print(f"npm latest    : {latest}")
    print(f"npm alpha     : {alpha}")

    if not cli:
        print("\n找不到 dsh CLI：请确认安装方式（Desktop 内置 / npm -g）。")
        return 2

    if latest and version_key(latest) > version_key(cli):
        print(f"\n→ 有更新：{cli} → {latest}")
        print("  Desktop：应用内「检查更新」，或从 https://harness.deepseek.com 下载覆盖安装")
        print(f"  独立 CLI：npm i -g @deepseek-ai/dsh@{latest}")
        if alpha and version_key(alpha) > version_key(latest):
            print(f"  想试预览版（不建议生产）：npm i -g @deepseek-ai/dsh@{alpha}")
        print("  升级前读安装目录 docs/upgrade-guide/README.md，升级后跑 python scripts/ci.py")
        return 1

    print(f"\n→ 已是最新：{cli} 与 npm latest 一致")
    if alpha and version_key(alpha) > version_key(cli):
        print(f"  （另有预览版 {alpha}，仅用于评估，不建议生产）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
