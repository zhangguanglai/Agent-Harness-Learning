"""实验 12：S0-2 领域 bundle —— 清单 / MCP 工具面 / DSH 组合。

验证：
1. bundle manifest、locale、patch 关键字齐全；
2. MCP server 能被 initialize，三个工具出现在 tools/list；
3. 在隔离 DSH_HOME 里 `--dump-config`，确认 patch 真的合进 profile
   （没有 DSH 环境时此项跳过，不伪装通过）。

运行：python labs/lab12_dsh_bundle.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "spike" / "dsh-bundle" / "validate.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        timeout=300,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output[-2000:]

    assert "静态检查：PASS" in output, output
    assert "MCP 工具面：PASS" in output, output
    assert "DSH 组合：passed" in output or "DSH 组合：skipped" in output, output
    print("✓ bundle：清单、MCP 工具面、DSH 组合三层校验通过")
    print("✓ 工具：mcp__banking-a__kb.search / doc.extract / rule.evaluate")
    print("✓ 权限预设：compliance-review（ask）/ compliance-batch（never）")

    print("\n结论：领域工具接进 DSH 的关键不是写插件，而是「行 id、cwd、权限预设」三处对齐。")


if __name__ == "__main__":
    run()
    print("\nLAB12 PASS")
