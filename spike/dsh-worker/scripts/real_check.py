"""真实模型链路预检 / 冒烟（S0-3 收尾）。

分两步：
1. **预检**（不需要 Key）：Node 24、DSH CLI、MCP 工具面、bundle 组合；
2. **冒烟**（需要 DEEPSEEK_API_KEY）：用 SDK 后端起 worker，让真实模型调用
   `mcp__banking-a__*` 工具，并从事件流里确认「模型真的调了工具」。

用法（在仓库根目录）：
    python spike/dsh-worker/scripts/real_check.py            # 只预检
    DEEPSEEK_API_KEY=sk-... python spike/dsh-worker/scripts/real_check.py --run

退出码：0 通过；2 缺少凭据（预检通过但未跑冒烟）；1 其它失败。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKER = ROOT / "spike" / "dsh-worker"
BUNDLE_PATCH = ROOT / "spike" / "dsh-bundle" / "cordis.patch.yml"
MIN_NODE = (24, 9)


def node_version() -> tuple[int, int, int] | None:
    exe = os.environ.get("DSH_NODE_EXECUTABLE")
    try:
        if exe:
            env = {**os.environ, "ELECTRON_RUN_AS_NODE": "1"}
            out = subprocess.run([exe, "-e", "console.log(process.versions.node)"], env=env, capture_output=True, text=True, timeout=60).stdout
        else:
            out = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=60).stdout
    except Exception:  # noqa: BLE001
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in match.groups()) if match else None


def dsh_version() -> str | None:
    exe = shutil.which("dsh")
    if not exe:
        return None
    cmd = ["cmd", "/c", exe, "--version"] if exe.lower().endswith((".cmd", ".bat")) else [exe, "--version"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()
        return out or None
    except Exception:  # noqa: BLE001
        return None


def mcp_tools() -> list[str]:
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    payload = "\n".join(json.dumps(item) for item in requests) + "\n"
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
    for line in proc.stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") == 2:
            return sorted(tool["name"] for tool in message["result"]["tools"])
    return []


def bundle_ok() -> bool:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "spike" / "dsh-bundle" / "validate.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        timeout=300,
    )
    return proc.returncode == 0


def run_smoke(port: int, timeout_s: float = 180) -> tuple[bool, str]:
    install = os.environ.get("DSH_INSTALL_DIR")
    electron = os.environ.get("DSH_NODE_EXECUTABLE")
    if not install or not electron:
        return False, "缺少 DSH_INSTALL_DIR / DSH_NODE_EXECUTABLE"
    cli_js = Path(install).parent / "dsh-desktop-host" / "lib" / "cli.js"
    env = {
        **os.environ,
        "ELECTRON_RUN_AS_NODE": "1",
        "DSH_BACKEND": "sdk",
        "DSH_BIN": electron,
        "DSH_ARGS_JSON": json.dumps(
            ["--expose-internals", str(cli_js), "--profile", "sdk", "--patch", str(BUNDLE_PATCH)],
            ensure_ascii=False,
        ),
        "BANKING_A_HOME": str(ROOT),
        "APPROVAL_MODE": "auto",
        "PORT": str(port),
        "MAX_WALL_MS": str(int(timeout_s * 1000)),
    }
    worker = subprocess.Popen(
        ["node", "src/server.js"],
        cwd=str(WORKER),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=2) as response:
                    if json.loads(response.read())["ok"]:
                        break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        else:
            return False, "worker 未能启动"

        body = json.dumps(
            {
                "run_id": "real-smoke-1",
                "tenant_id": "demo",
                "task": "请用 kb.search 工具检索「数据出境 安全评估」，把命中的条款 id 告诉我，不要编造。",
            }
        ).encode()
        request = urllib.request.Request(f"{base}/v1/runs", data=body, headers={"content-type": "application/json"}, method="POST")
        urllib.request.urlopen(request, timeout=20).read()

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            with urllib.request.urlopen(f"{base}/v1/runs/real-smoke-1", timeout=5) as response:
                status = json.loads(response.read())["status"]
            if status in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.5)
        events = json.loads(urllib.request.urlopen(f"{base}/v1/runs/real-smoke-1/events", timeout=10).read())["events"]
        tools = {
            str(event["payload"].get("tool"))
            for event in events
            if event["type"] == "tool_result"
        }
        used_mcp = any(name.startswith("mcp__banking-a__") for name in tools)
        if not used_mcp:
            return False, f"模型没有调用 banking-a 工具，实际调用：{sorted(tools)}"
        return True, f"模型调用了 {sorted(name for name in tools if name.startswith('mcp__banking-a__'))}"
    finally:
        worker.terminate()
        worker.wait(timeout=15)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="预检通过后跑真实模型冒烟")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--dsh-home", default=None, help="把 DSH profile 建在工作区（避免写用户目录）")
    args = parser.parse_args()

    ok = True
    version = node_version()
    if version and version >= MIN_NODE:
        print(f"✓ Node: {'.'.join(map(str, version))}（要求 ≥ {'.'.join(map(str, MIN_NODE))}）")
    else:
        print(f"✗ Node: {version}（要求 ≥ {'.'.join(map(str, MIN_NODE))}）")
        ok = False

    version = dsh_version()
    print(f"{'✓' if version else '✗'} DSH CLI: {version or '不可用'}")
    ok = ok and bool(version)

    tools = mcp_tools()
    expected = {"kb.search", "doc.extract", "rule.evaluate"}
    print(f"{'✓' if set(tools) == expected else '✗'} MCP 工具面: {tools}")
    ok = ok and set(tools) == expected

    composed = bundle_ok()
    print(f"{'✓' if composed else '✗'} bundle 组合: {'patch 可合成' if composed else '校验失败'}")
    ok = ok and composed

    if not ok:
        return 1

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("… 缺少 DEEPSEEK_API_KEY：预检通过，跳过真实模型冒烟（设置后加 --run）")
        return 2

    if args.dsh_home:
        os.environ["DSH_HOME"] = str(Path(args.dsh_home).resolve())
        Path(args.dsh_home).mkdir(parents=True, exist_ok=True)
    print("… 开始真实模型冒烟（会消耗少量额度）")
    passed, detail = run_smoke(args.port)
    print(f"{'✓' if passed else '✗'} 真实模型: {detail}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
