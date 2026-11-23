"""S0-1 验证：容器静态检查 + （可选）真实构建/运行。

三层：
1. 静态：Dockerfile 与 entrypoint 的关键约束；
2. 环境：docker CLI / daemon 是否可用；
3. 运行：构建镜像，跑 headless 任务，断言工作区产出 HELLO.md。

没有 Docker daemon 时返回 2（跳过），不伪装成通过。

用法：
    python deploy/s0-container/verify.py --static-only
    python deploy/s0-container/verify.py --build
    python deploy/s0-container/verify.py --no-build --image banking-a/s0:local
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIR = Path(__file__).resolve().parent
IMAGE = "banking-a/s0:local"


def check_static() -> list[str]:
    errors: list[str] = []
    dockerfile = (DIR / "Dockerfile").read_text(encoding="utf-8")
    for needle in ("FROM node:24", "USER harness", "ENTRYPOINT", "COPY --chown=harness:harness spike/dsh-bundle", "COPY --chown=harness:harness domain"):
        if needle not in dockerfile:
            errors.append(f"Dockerfile 缺少：{needle}")

    entry = (DIR / "entrypoint.sh").read_text(encoding="utf-8")
    for needle in ("set -euo pipefail", "dsh headless --patch", "exec node", "worker"):
        if needle not in entry:
            errors.append(f"entrypoint.sh 缺少：{needle}")

    bash = next(
        (candidate for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe") if Path(candidate).exists()),
        None,
    )
    if bash:
        proc = subprocess.run([bash, "-n", str(DIR / "entrypoint.sh")], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            if "signal pipe" in (proc.stderr or ""):
                print("… 沙箱禁止 cygwin bash 启动，跳过 entrypoint 语法检查（Linux 容器内不受影响）")
            else:
                errors.append(f"entrypoint.sh 语法错误：{proc.stderr.strip()[:300]}")
    else:
        print("… 未找到 Git Bash，跳过 entrypoint 语法检查")
    return errors


def docker_ready() -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "未安装 docker CLI"
    proc = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if proc.returncode != 0:
        return False, "docker daemon 不可用"
    return True, proc.stdout.strip()


def build_image(image: str) -> tuple[bool, str]:
    proc = subprocess.run(
        ["docker", "build", "-f", str(DIR / "Dockerfile"), "-t", image, str(ROOT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout)[-1500:]
    return True, "镜像构建完成"


def run_headless(image: str, timeout_s: float = 600) -> tuple[bool, str]:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return False, "缺少 DEEPSEEK_API_KEY"
    workspace = Path(tempfile.mkdtemp(prefix="s0-ws-"))
    started = time.time()
    try:
        proc = subprocess.run(
            [
                "docker", "run", "--rm",
                "--read-only", "--tmpfs", "/tmp:rw,exec,size=256m",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--pids-limit", "256", "--memory", "2g", "--cpus", "2",
                "-v", f"{workspace}:/workspace",
                "-e", "DEEPSEEK_API_KEY",
                "-e", "TASK=请列出当前目录的文件，然后写一个 HELLO.md，内容只有一行：ok",
                image, "headless",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env={**os.environ, "DEEPSEEK_API_KEY": key},
        )
        hello = workspace / "HELLO.md"
        if not hello.exists():
            return False, f"未生成 HELLO.md；exit={proc.returncode}；stderr={proc.stderr[-800:]}"
        content = hello.read_text(encoding="utf-8", errors="replace").strip()
        if "ok" not in content:
            return False, f"HELLO.md 内容不符：{content[:100]}"
        return True, f"容器内完成任务（{time.time() - started:.0f}s），HELLO.md={content[:40]!r}"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    parser = argparse.ArgumentParser(description="S0-1 容器验证")
    parser.add_argument("--static-only", action="store_true")
    parser.add_argument("--build", action="store_true", help="构建镜像")
    parser.add_argument("--no-build", action="store_true", help="跳过构建，直接用 --image")
    parser.add_argument("--image", default=IMAGE)
    args = parser.parse_args()

    errors = check_static()
    print(f"静态检查：{'PASS' if not errors else 'FAIL'}（Dockerfile / entrypoint）")
    if errors:
        for error in errors:
            print(f"  - {error}")
        return 1
    if args.static_only:
        return 0

    ready, detail = docker_ready()
    print(f"Docker：{'可用 ' + detail if ready else '不可用（' + detail + '）'}")
    if not ready:
        print("… 跳过构建与运行：请在装有 Docker 的环境执行 `python deploy/s0-container/verify.py --build`")
        return 2

    image = args.image
    if args.build:
        ok, message = build_image(image)
        print(f"构建：{'PASS' if ok else 'FAIL'} {message}")
        if not ok:
            return 1

    ok, message = run_headless(image)
    print(f"运行：{'PASS' if ok else 'FAIL'} {message}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
