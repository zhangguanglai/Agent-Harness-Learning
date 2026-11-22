"""一键验收：依次运行全部实验 + 离线 demo，输出结果表。

用法：
    python labs/verify.py           # 全部
    python labs/verify.py 03 05     # 只跑编号含 03 / 05 的实验

退出码 0 表示全部通过；任何一项失败返回 1。
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABS = sorted((ROOT / "labs").glob("lab[0-9]*.py"))


def run_cmd(args: list[str]) -> tuple[bool, str, str]:
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    ok = proc.returncode == 0
    return ok, proc.stdout, proc.stderr


def run_one(script: Path) -> tuple[bool, str, str]:
    ok, stdout, stderr = run_cmd([str(script)])
    return ok and "PASS" in stdout, stdout, stderr


def main(argv: list[str]) -> int:
    selected = [p for p in LABS if not argv or any(tag in p.name for tag in argv)]
    if not selected:
        print("没有匹配的实验。")
        return 1

    print(f"mini-harness 验收：共 {len(selected)} 项\n" + "=" * 64)
    failures: list[tuple[str, str]] = []
    for script in selected:
        ok, stdout, stderr = run_one(script)
        mark = "✅ PASS" if ok else "❌ FAIL"
        checks = [ln for ln in stdout.splitlines() if ln.startswith("✓")]
        print(f"{mark}  {script.name:<24} {len(checks)} 项检查")
        for line in checks:
            print("        " + line)
        if not ok:
            failures.append((script.name, (stderr or stdout).strip()[-1500:]))

    # 端到端 demo：不联网、不依赖 API Key
    ok, stdout, stderr = run_cmd(["-m", "mini_harness", "demo"])
    demo_ok = ok and "停止原因：final" in stdout
    print()
    if demo_ok:
        print("✅ PASS  demo（python -m mini_harness demo）")
    else:
        print("❌ FAIL  demo（python -m mini_harness demo）")
        failures.append(("demo", (stderr or stdout).strip()[-1500:]))

    print("=" * 64)
    if failures:
        for name, detail in failures:
            print(f"\n--- {name} 失败详情 ---\n{detail}")
        print(f"\n验收失败：{len(failures)} 项")
        return 1
    print(f"全部通过：{len(selected)} 个实验 + 1 个 demo")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
