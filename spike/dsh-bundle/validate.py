"""S0-2 校验：bundle 清单、patch 结构、MCP 工具面、DSH 组合结果。

三层验证，逐层加码：
1. 静态：manifest / locale / 引用文件是否齐全；
2. 协议：MCP server 能否 initialize + tools/list（三个工具在不在）；
3. 组合：用隔离的 DSH_HOME 跑 `dsh --dump-config`，确认 patch 真的合进了 profile。

第 3 层需要当前进程能拿到 DSH 安装环境（DSH_NODE_EXECUTABLE / DSH_INSTALL_DIR），
拿不到时跳过并说明原因，不伪装成通过。

用法：python spike/dsh-bundle/validate.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = Path(__file__).resolve().parent
REQUIRED_FILES = ["cordis.patch.yml", "locale/en.json", "locale/zh.json", "docs/DETAIL.md", "icon.svg"]


def check_static() -> list[str]:
    errors: list[str] = []
    manifest = json.loads((BUNDLE / "package.json").read_text(encoding="utf-8"))
    if manifest.get("dsh", {}).get("bundle", {}).get("patch") != "./cordis.patch.yml":
        errors.append("package.json 缺少 dsh.bundle.patch")
    for rel in REQUIRED_FILES:
        if not (BUNDLE / rel).exists():
            errors.append(f"缺少文件：{rel}")
    for locale in ("en.json", "zh.json"):
        payload = json.loads((BUNDLE / "locale" / locale).read_text(encoding="utf-8"))
        meta = payload.get("meta", {})
        if not meta.get("title") or not meta.get("description"):
            errors.append(f"{locale} 缺少 meta.title/description")

    patch = (BUNDLE / "cordis.patch.yml").read_text(encoding="utf-8")
    for needle in ("mcp-banking-a", "serverName: banking-a", "compliance-review", "compliance-batch", "defaultPreset: compliance-review"):
        if needle not in patch:
            errors.append(f"patch 缺少：{needle}")
    return errors


def check_mcp_surface() -> list[str]:
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in requests) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "domain.banking_a.mcp_server"],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        timeout=60,
    )
    try:
        lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        return [f"MCP 输出不是 JSON：{exc}"]
    by_id = {item.get("id"): item for item in lines}
    if 1 not in by_id or by_id[1].get("result", {}).get("serverInfo", {}).get("name") != "banking-a-tools":
        return ["MCP initialize 失败"]
    tools = {tool["name"] for tool in by_id.get(2, {}).get("result", {}).get("tools", [])}
    expected = {"kb.search", "doc.extract", "rule.evaluate"}
    if tools != expected:
        return [f"MCP 工具面不符：{sorted(tools)}"]
    return []


def check_composition() -> tuple[list[str], str]:
    node = os.environ.get("DSH_NODE_EXECUTABLE")
    install = os.environ.get("DSH_INSTALL_DIR")
    if not node or not install:
        return [], "skipped（未检测到 DSH 运行环境）"

    home = Path(tempfile.mkdtemp(prefix="dsh-home-"))
    env = {**os.environ, "ELECTRON_RUN_AS_NODE": "1", "DSH_HOME": str(home), "BANKING_A_HOME": str(ROOT)}
    argv = [
        node,
        str(Path(install) / "lib" / "bin.js"),
        "--profile",
        "spike",
        "--from-default-profile",
        "web",
        "--patch",
        str(BUNDLE / "cordis.patch.yml"),
        "--dump-config",
    ]
    try:
        proc = subprocess.run(argv, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
    except subprocess.TimeoutExpired:
        return ["dump-config 超时"], "failed"
    finally:
        shutil.rmtree(home, ignore_errors=True)

    if proc.returncode != 0:
        return [f"dump-config 返回 {proc.returncode}: {(proc.stderr or '')[:300]}"], "failed"
    out = proc.stdout
    errors = []
    for needle in ("id: mcp-banking-a", "serverName: banking-a", "compliance-review", "defaultPreset: compliance-review"):
        if needle not in out:
            errors.append(f"组合结果缺少：{needle}")
    return errors, "passed"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    static_errors = check_static()
    mcp_errors = check_mcp_surface()
    comp_errors, status = check_composition()
    errors = static_errors + mcp_errors + comp_errors

    print(f"静态检查：{'PASS' if not static_errors else 'FAIL'}")
    print(f"MCP 工具面：{'PASS' if not mcp_errors else 'FAIL'}")
    print(f"DSH 组合：{status}")
    if errors:
        for error in errors:
            print(f"  - {error}")
        return 1
    print("\nS0-2 校验通过：bundle 清单、MCP 工具面、profile 组合三层一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
