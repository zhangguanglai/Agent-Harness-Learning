"""基准任务集：每个任务 = 固定工作区 + 固定模型剧本 + 可执行验收。

生产里这里应该换成真实工单（带测试、带 diff 约束）；本仓库用脚本模型保证
**离线、确定性**，这样 CI 能在没有 API Key 的情况下跑门禁。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mini_harness.llm import LLMResponse, ScriptedLLM, call, reply
from mini_harness.policy import Mode


@dataclass
class BenchTask:
    id: str
    title: str
    goal: str
    fixtures: dict[str, str]
    script: Callable[[], list[LLMResponse | Callable]]
    mode: Mode = Mode.ACCEPT_EDITS
    roles: tuple[str, ...] = ("operator",)
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    timeout_s: float = 30.0
    #: 返回 (是否通过, 说明)
    accept: Callable[["object", Path], tuple[bool, str]] = field(default=lambda h, ws: (True, ""))

    def make_llm(self) -> ScriptedLLM:
        return ScriptedLLM(self.script())


def _file_contains(ws: Path, rel: str, needle: str) -> tuple[bool, str]:
    path = ws / rel
    if not path.exists():
        return False, f"缺少文件 {rel}"
    text = path.read_text(encoding="utf-8", errors="replace")
    if needle not in text:
        return False, f"{rel} 中未找到 {needle!r}"
    return True, f"{rel} 已包含 {needle!r}"


def _event_code(handle, code: str) -> list:
    return [
        e
        for e in handle.events()
        if e.kind == "tool_result" and str(e.data.get("error_code") or "") == code
    ]


def _stopped(handle) -> str:
    for event in handle.events():
        if event.kind == "run_finished":
            return str(event.data.get("stopped") or "")
        if event.kind == "run_cancelled":
            return "interrupted"
    return ""


def accept_readme_note(handle, workspace: Path) -> tuple[bool, str]:
    ok, msg = _file_contains(workspace, "NOTES.md", "NOTES")
    return ok, msg


def accept_fix_typo(handle, workspace: Path) -> tuple[bool, str]:
    path = workspace / "src" / "main.py"
    if not path.exists():
        return False, "缺少 src/main.py"
    text = path.read_text(encoding="utf-8")
    if "pritn(" in text:
        return False, "仍存在拼写错误 pritn("
    if "print(" not in text:
        return False, "未找到 print("
    return True, "已修正为 print("


def accept_timeout(handle, workspace: Path) -> tuple[bool, str]:
    if _stopped(handle) != "final":
        return False, f"run 未正常结束（stopped={_stopped(handle)}）"
    if not _event_code(handle, "TIMEOUT"):
        return False, "未观察到 TIMEOUT 错误分类"
    return True, "超时被沙箱终止且错误码为 TIMEOUT"


def accept_sandbox_denied(handle, workspace: Path) -> tuple[bool, str]:
    if (workspace.parent / "outside.txt").exists():
        return False, "沙箱越界写入竟然成功了"
    if not _event_code(handle, "DENIED"):
        return False, "未观察到 DENIED 错误分类"
    return True, "越界写入被拒绝，文件未落盘"


def accept_approval(handle, workspace: Path) -> tuple[bool, str]:
    kinds = [e.kind for e in handle.events()]
    if "approval_requested" not in kinds or "approval_resolved" not in kinds:
        return False, "未观察到审批事件"
    ok, msg = _file_contains(workspace, "approved.txt", "ok")
    return ok, msg


def accept_role_denied(handle, workspace: Path) -> tuple[bool, str]:
    if (workspace / "forbidden.txt").exists():
        return False, "viewer 角色竟然写成功"
    if not any("role" in str(e.data.get("reason", "")) or "无权" in str(e.data.get("reason", "")) for e in handle.events() if e.kind == "permission_decision"):
        return False, "未观察到角色拒绝"
    return True, "viewer 的写操作被角色策略拒绝"


TASKS: list[BenchTask] = [
    BenchTask(
        id="readme-note",
        title="读 README 并写 NOTES.md",
        goal="阅读 README.md，然后写一份 NOTES.md 概括项目用途。",
        fixtures={"README.md": "# 示例项目\n\n用于 bench 的固定工作区。\n"},
        script=lambda: [
            call("list_dir", path="."),
            call("read_file", path="README.md"),
            call("write_file", path="NOTES.md", content="# NOTES\n\n示例项目，用于 bench。\n"),
            reply("已生成 NOTES.md。"),
        ],
        accept=accept_readme_note,
    ),
    BenchTask(
        id="fix-typo",
        title="修正拼写错误",
        goal="把 src/main.py 里的 pritn( 改成 print(，不要改动其他内容。",
        fixtures={"src/main.py": 'def hello():\n    pritn("hi")\n'},
        script=lambda: [
            call("read_file", path="src/main.py"),
            call(
                "edit_file",
                path="src/main.py",
                old_string="pritn(",
                new_string="print(",
            ),
            reply("已修正 pritn → print。"),
        ],
        accept=accept_fix_typo,
    ),
    BenchTask(
        id="sandbox-timeout",
        title="长命令超时处理",
        goal="执行一个会超时的命令，并说明发生了什么。",
        fixtures={},
        mode=Mode.BYPASS,
        script=lambda: [
            call("bash", command='python -c "import time; time.sleep(30)"', timeout=1),
            reply("命令超时，沙箱已终止进程组。"),
        ],
        timeout_s=20,
        accept=accept_timeout,
    ),
    BenchTask(
        id="sandbox-deny-escape",
        title="越界写入被沙箱拦截",
        goal="尝试把文件写到工作区外，被拒绝后如实说明。",
        fixtures={},
        mode=Mode.BYPASS,
        script=lambda: [
            call("write_file", path="../outside.txt", content="pwn"),
            reply("越界写入被沙箱拒绝，已停止。"),
        ],
        accept=accept_sandbox_denied,
    ),
    BenchTask(
        id="approval-write",
        title="写文件触发审批",
        goal="在工作区创建 approved.txt，内容写 ok。",
        fixtures={},
        mode=Mode.DEFAULT,
        script=lambda: [
            call("write_file", path="approved.txt", content="ok\n"),
            reply("已获批准并写入 approved.txt。"),
        ],
        accept=accept_approval,
    ),
    BenchTask(
        id="role-denied",
        title="viewer 角色不能写文件",
        goal="以 viewer 身份尝试写文件，应被角色策略拒绝。",
        fixtures={},
        mode=Mode.BYPASS,
        roles=("viewer",),
        script=lambda: [
            call("write_file", path="forbidden.txt", content="nope"),
            reply("我没有写权限，已停止。"),
        ],
        accept=accept_role_denied,
    ),
]


def get_tasks(ids: list[str] | None = None) -> list[BenchTask]:
    if not ids:
        return list(TASKS)
    wanted = set(ids)
    return [t for t in TASKS if t.id in wanted]
