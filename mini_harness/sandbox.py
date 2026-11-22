"""进程沙箱：所有 shell 执行都必须走这里。

为什么单列一个模块：`bash` 是 Harness 里唯一能执行任意代码的工具，它需要一个
**进程级**的边界（进程组、超时、取消、输出上限、环境白名单），而不是字符串规则。

本模块给的是「本地沙箱」——单机可用、可测、跨平台；生产环境把 `Sandbox` 换成
容器/微虚机实现即可，调用方（`bash` 工具、运行时）不需要改。
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol

#: 默认不透传给子进程的环境变量模式（密钥、令牌、云凭证）。
SECRET_PATTERNS = ("*_KEY", "*_TOKEN", "*_SECRET", "*_PASSWORD", "AWS_*", "GITHUB_*", "OPENAI_*", "ANTHROPIC_*")


@dataclass
class SandboxLimits:
    timeout_s: float = 120.0
    max_output_chars: int = 200_000
    #: 传给子进程的环境变量；None 表示继承（会按 `SECRET_PATTERNS` 过滤）。
    env_allow_prefixes: tuple[str, ...] | None = None


@dataclass
class ExecResult:
    command: str
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled


class Sandbox(Protocol):
    def exec(self, command: str, *, cwd: Path, limits: SandboxLimits, cancel_event: threading.Event | None = None) -> ExecResult:
        ...


def sanitize_env(base: Mapping[str, str] | None = None, allow_prefixes: tuple[str, ...] | None = None) -> dict[str, str]:
    """默认继承，但剔除疑似密钥；`allow_prefixes` 给出白名单时只保留命中的变量。"""
    import fnmatch

    env = dict(base if base is not None else os.environ)
    if allow_prefixes:
        return {k: v for k, v in env.items() if any(k.startswith(p) for p in allow_prefixes)}
    return {k: v for k, v in env.items() if not any(fnmatch.fnmatchcase(k, p) for p in SECRET_PATTERNS)}


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _WinJob:
    """Windows Job Object：`KILL_ON_JOB_CLOSE` + `TerminateJobObject` 回收整棵进程树。

    为什么不用 taskkill：受限环境里 `taskkill` 可能返回 Access denied；
    Job Object 属于用户态 API，不依赖外部可执行文件。
    """

    def __init__(self, proc: subprocess.Popen):
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.handle = self._k32.CreateJobObjectW(None, None)
        self.active = False
        if not self.handle:
            return
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            return
        if not self._k32.AssignProcessToJobObject(self.handle, ctypes.c_void_p(int(proc._handle))):
            self.close()
            return
        self.active = True

    def kill(self) -> bool:
        if not self.active or not self.handle:
            return False
        return bool(self._k32.TerminateJobObject(self.handle, 1))

    def close(self) -> None:
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


class LocalSandbox:
    """本机进程沙箱。

    能力：独立进程组（可整组回收）、墙钟超时、协作式取消、输出上限、环境过滤。
    不提供：文件系统隔离、网络隔离、资源（CPU/内存）限额 —— 那些需要容器/Job Object，
    属于生产实现，接口保持一致。

    Windows 上 `taskkill` 在受限环境可能被拒绝（Access denied），因此优先用
    **Job Object** 回收整棵树：把子进程放进 job，`TerminateJobObject` 一枪打掉所有后代。
    """

    def __init__(self, *, env_allow_prefixes: tuple[str, ...] | None = None):
        self.env_allow_prefixes = env_allow_prefixes
        self.is_windows = sys.platform.startswith("win")

    # -- 对外入口 ---------------------------------------------------------- #
    def exec(
        self,
        command: str,
        *,
        cwd: Path,
        limits: SandboxLimits,
        cancel_event: threading.Event | None = None,
    ) -> ExecResult:
        started = time.perf_counter()
        env = sanitize_env(allow_prefixes=self.env_allow_prefixes or limits.env_allow_prefixes)
        # 用 shell=True 直接把命令交给 cmd.exe / /bin/sh：命令本身是模型给的字符串，
        # 自己拼 argv 会引入 Windows 的引号转义问题（cmd 会吞掉一层引号）。
        kwargs: dict = {
            "shell": True,
            "cwd": str(cwd),
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if self.is_windows:
            kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True  # 独立进程组，便于整组 kill

        proc = subprocess.Popen(command, **kwargs)
        job = _WinJob(proc) if self.is_windows else None
        timed_out = cancelled = False
        deadline = started + max(0.1, limits.timeout_s)
        try:
            while True:
                try:
                    out, err = proc.communicate(timeout=0.05)
                    break
                except subprocess.TimeoutExpired:
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                    elif time.perf_counter() >= deadline:
                        timed_out = True
                    else:
                        continue
                    self._stop(proc, job)
                    try:
                        out, err = proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        out, err = b"", b""
                    break
        finally:
            if job is not None:
                job.close()

        stdout = (out or b"").decode("utf-8", "replace")
        stderr = (err or b"").decode("utf-8", "replace")
        truncated = False
        if len(stdout) > limits.max_output_chars:
            stdout = stdout[-limits.max_output_chars :]
            truncated = True
        if len(stderr) > limits.max_output_chars:
            stderr = stderr[-limits.max_output_chars :]
            truncated = True

        return ExecResult(
            command=command,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            timed_out=timed_out,
            cancelled=cancelled,
            truncated=truncated,
        )

    # -- 回收 -------------------------------------------------------------- #
    @staticmethod
    def _stop(proc: subprocess.Popen, job: "_WinJob | None") -> None:
        if proc.poll() is not None:
            return
        if sys.platform.startswith("win"):
            if job is not None and job.kill():
                return
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
