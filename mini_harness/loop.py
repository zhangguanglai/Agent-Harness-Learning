"""代理循环：把「模型 → 工具 → 结果 → 模型」串成一条可停止、可观测的流水线。

一轮（turn）= 一次模型调用 + 它请求的所有工具执行完成、结果写回上下文。
整个 `run()` 就是不断重复这一轮，直到出现明确的停止条件之一：

- 模型不再请求工具（自然结束）；
- 触达 `max_turns` / token 预算（预算护栏）；
- 连续多轮全部被权限拒绝（无法推进）；
- 外部中断。

所有「什么时候停」都必须可观测：`RunResult.stopped` 会写进轨迹，
实验里据此断言，而不是靠感觉。
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Sequence

from .context import ContextManager
from .hooks import Hooks
from .llm import LLM
from .memory import MemoryStore
from .policy import Decision, Mode, PermissionPolicy
from .skills import SkillManager
from .tools import ToolContext, ToolRegistry, build_core_registry
from .trace import Tracer
from .types import LLMResponse, ToolCall, ToolResult, Usage, assistant_message, system_message, user_message
from .util import estimate_tokens, truncate_middle

Approver = Callable[[ToolCall, Decision], bool]


@dataclass
class AgentConfig:
    workspace: Path = field(default_factory=Path.cwd)
    mode: Mode = Mode.DEFAULT
    allow: Sequence[str] = ()
    deny: Sequence[str] = ()
    ask: Sequence[str] = ()
    tool_groups: Sequence[str] = ("core", "shell", "web", "memory", "skills", "delegate")
    max_turns: int = 16
    token_budget: int = 120_000
    max_context_tokens: int = 8_000
    compact_at: float = 0.6
    keep_tail: int = 8
    max_tool_chars: int = 4_000
    memory_home: Path | None = None
    skills_root: Path | None = None
    system_extra: str = ""
    auto_deny_unapproved: bool = False


@dataclass
class RunResult:
    text: str
    stopped: str
    turns: int
    usage: Usage
    messages: list[dict]
    denials: list[str] = field(default_factory=list)
    compactions: int = 0
    trace_summary: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.stopped == "final"


class Agent:
    def __init__(
        self,
        llm: LLM,
        config: AgentConfig | None = None,
        *,
        tracer: Tracer | None = None,
        hooks: Hooks | None = None,
        approver: Approver | None = None,
        name: str = "lead",
        depth: int = 0,
        on_event: Callable[[dict], None] | None = None,
    ):
        self.llm = llm
        self.config = config or AgentConfig()
        self.name = name
        self.depth = depth
        self.tracer = tracer or Tracer()
        self.hooks = hooks or Hooks()
        self.approver = approver
        self.on_event = on_event
        self.usage = Usage()
        self.turns = 0
        self.denials: list[str] = []
        self.compactions = 0
        self._consecutive_denied_turns = 0
        self._interrupted = False

        self.registry: ToolRegistry = build_core_registry()
        self.memory = MemoryStore(self.config.memory_home) if self.config.memory_home else None
        self.skills = SkillManager(self.config.skills_root) if self.config.skills_root else None
        if self.memory:
            from .memory import register_memory_tools

            register_memory_tools(self.registry, self.memory)
        if self.skills:
            from .skills import register_skill_tools

            register_skill_tools(self.registry, self.skills)
        if "delegate" in self.config.tool_groups:
            from .subagent import register_delegate

            register_delegate(self.registry, self.delegate_run)

        self.ctx = ToolContext(workspace=Path(self.config.workspace), memory=self.memory, skills=self.skills, agent=self)
        self.ctx.extra["registry"] = self.registry
        self.policy = PermissionPolicy(
            mode=self.config.mode,
            allow=self.config.allow,
            deny=self.config.deny,
            ask=self.config.ask,
            sandbox_root=Path(self.config.workspace),
        )
        self.context = ContextManager(
            max_context_tokens=self.config.max_context_tokens,
            compact_at=self.config.compact_at,
            keep_tail=self.config.keep_tail,
            max_tool_chars=self.config.max_tool_chars,
        )
        self.system_prompt: str = ""
        self.messages: list[dict] = []

    # ------------------------------------------------------------------ #
    # 主循环
    # ------------------------------------------------------------------ #
    def run(self, task: str, history: list[dict] | None = None) -> RunResult:
        self.turns = 0
        self.usage = Usage()
        self.denials = []
        self.compactions = 0
        self._consecutive_denied_turns = 0
        self._interrupted = False

        self.system_prompt = self._build_system(task)
        self.tracer.emit(
            "session_start",
            agent=self.name,
            depth=self.depth,
            mode=self.policy.mode.value,
            workspace=str(self.config.workspace),
            system_chars=len(self.system_prompt),
            system_hash=hashlib.sha1(self.system_prompt.encode("utf-8")).hexdigest()[:12],
            tools=self.registry.names(self._enabled_groups()),
        )
        self._emit({"type": "session_start", "agent": self.name, "system_prompt": self.system_prompt})

        self.messages = [system_message(self.system_prompt)] + list(history or []) + [user_message(task)]
        stopped = "max_turns"
        final_text = ""

        while True:
            if self._interrupted:
                stopped, final_text = "interrupted", "已中断。"
                break
            if self.turns >= self.config.max_turns:
                stopped = "max_turns"
                final_text = final_text or f"达到最大轮数 {self.config.max_turns}，任务未确认完成。"
                break
            if self.usage.total >= self.config.token_budget:
                stopped = "budget"
                final_text = final_text or f"达到 token 预算 {self.config.token_budget}，已停止。"
                break

            self._maybe_compact()
            self.turns += 1
            self.tracer.emit("turn", agent=self.name, turn=self.turns, messages=len(self.messages))
            self._emit({"type": "turn", "turn": self.turns})

            response = self.llm.complete(self.messages, self._schemas())
            self.usage = self.usage + response.usage
            self.tracer.emit(
                "llm_response",
                agent=self.name,
                turn=self.turns,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                text_chars=len(response.text),
                tool_calls=[c.name for c in response.tool_calls],
            )
            self._emit({"type": "assistant", "text": response.text, "tool_calls": [c.summary() for c in response.tool_calls]})

            self.messages.append(assistant_message(response.text, response.tool_calls))

            if not response.wants_tools:
                final_text = response.text
                stopped = "final"
                break

            results = self._execute(response.tool_calls)
            for result in results:
                self.messages.append(result.to_message())

            if results and all(r.denied for r in results):
                self._consecutive_denied_turns += 1
                if self._consecutive_denied_turns >= 2:
                    stopped = "denied"
                    final_text = "连续两轮的工具调用全部被权限层拒绝，循环停止。请放宽规则或换用更窄的任务。"
                    break
            else:
                self._consecutive_denied_turns = 0
            final_text = response.text

        self.tracer.emit(
            "session_end",
            agent=self.name,
            stopped=stopped,
            turns=self.turns,
            input_tokens=self.usage.input_tokens,
            output_tokens=self.usage.output_tokens,
            denials=len(self.denials),
        )
        self._emit({"type": "session_end", "stopped": stopped, "text": final_text})
        return RunResult(
            text=final_text,
            stopped=stopped,
            turns=self.turns,
            usage=self.usage,
            messages=list(self.messages),
            denials=list(self.denials),
            compactions=self.compactions,
            trace_summary=self.tracer.summary(),
        )

    def interrupt(self) -> None:
        self._interrupted = True

    # ------------------------------------------------------------------ #
    # 工具执行
    # ------------------------------------------------------------------ #
    def _execute(self, calls: list[ToolCall]) -> list[ToolResult]:
        prepared: list[tuple[Decision, ToolCall]] = []
        for call in calls:
            veto = self.hooks.run_before(call, self.ctx)
            if veto:
                decision = Decision("deny", f"Hook 否决：{veto}")
            else:
                decision = self.policy.decide(call, self.ctx)
                if decision.action == "ask":
                    decision = self._resolve_ask(call, decision)
            prepared.append((decision, call))
            self.tracer.emit(
                "permission",
                agent=self.name,
                turn=self.turns,
                tool=call.name,
                action=decision.action,
                reason=decision.reason,
            )
            self._emit({"type": "permission", "tool": call.name, "decision": str(decision)})

        results: list[ToolResult | None] = [None] * len(calls)
        read_only_idx = [
            i
            for i, (decision, call) in enumerate(prepared)
            if decision.allowed and self.registry.is_read_only(call.name)
        ]
        serial_idx = [i for i in range(len(calls)) if i not in set(read_only_idx)]

        # 只读调用可以并行：这是站点里「同一轮多个工具怎么跑」的实现层答案
        if read_only_idx:
            with ThreadPoolExecutor(max_workers=min(4, len(read_only_idx))) as pool:
                futures = {pool.submit(self._run_one, prepared[i][1], prepared[i][0]): i for i in read_only_idx}
                for future in as_completed(futures):
                    results[futures[future]] = future.result()
        for i in serial_idx:
            results[i] = self._run_one(prepared[i][1], prepared[i][0])

        return [r or ToolResult(name=c.name, call_id=c.id, ok=False, error="未执行") for c, r in zip(calls, results)]

    def _run_one(self, call: ToolCall, decision: Decision) -> ToolResult:
        self.tracer.emit("tool_call", agent=self.name, turn=self.turns, tool=call.name, args=call.arguments)
        if not decision.allowed:
            self.denials.append(f"{call.name}: {decision.reason}")
            result = ToolResult(name=call.name, call_id=call.id, ok=False, denied=True, error=decision.reason)
        else:
            result = self.registry.call(call, self.ctx)
            result.output = truncate_middle(result.output, self.config.max_tool_chars)
        self.tracer.emit(
            "tool_result",
            agent=self.name,
            turn=self.turns,
            tool=call.name,
            ok=result.ok,
            denied=result.denied,
            chars=len(result.output),
            error=result.error[:300],
            duration_ms=round(result.duration_ms, 1),
        )
        self.hooks.run_after(call, result, self.ctx)
        self._emit({"type": "tool_result", "tool": call.name, "ok": result.ok, "denied": result.denied, "output": result.output[:500]})
        return result

    def _resolve_ask(self, call: ToolCall, decision: Decision) -> Decision:
        if self.config.auto_deny_unapproved or self.approver is None:
            return Decision("deny", f"需要人工确认但当前无审批人：{decision.reason}")
        approved = bool(self.approver(call, decision))
        self.tracer.emit("approval", agent=self.name, tool=call.name, approved=approved)
        return Decision("allow" if approved else "deny", "人工批准" if approved else "人工拒绝", decision.rule)

    # ------------------------------------------------------------------ #
    # 上下文与提示
    # ------------------------------------------------------------------ #
    def _maybe_compact(self) -> None:
        if not self.context.needs_compaction(self.messages):
            return
        compacted, report = self.context.compact(self.messages)
        if report is None:
            return
        self.messages = compacted
        self.compactions += 1
        self.tracer.emit("compaction", agent=self.name, turn=self.turns, **report.to_dict())
        self._emit({"type": "compaction", "report": report.to_dict()})

    def _enabled_groups(self) -> tuple[str, ...]:
        return tuple(self.config.tool_groups)

    def _schemas(self) -> list[dict]:
        names = self.registry.names(self._enabled_groups())
        if self.policy.mode is Mode.PLAN:
            names = [n for n in names if self.registry.is_read_only(n)]
        return [self.registry.get(n).schema() for n in names if self.registry.get(n)]

    def _build_system(self, task: str) -> str:
        cfg = self.config
        if self.depth > 0:
            return self._build_subagent_system(task)

        parts = [
            "你是 mini-harness 驱动的工程型 Agent。你的价值在于把任务拆成可验证的步骤并真正执行，"
            "而不是给出建议。",
            "## 工作方式\n"
            "1. 先观察：用 list_dir / read_file 建立事实，不要凭猜测改文件。\n"
            "2. 修改前先读取目标文件；edit_file 只做精确替换，整文件重写用 write_file。\n"
            "3. 同一轮里互不依赖的只读调用可以一起发出，运行时并行执行；写操作串行。\n"
            "4. 用可运行的命令验证结果，再给出结论；失败就修，不要假装成功。\n"
            "5. 目标达成即停：最后一轮只回复文字，概括做了什么、验证了什么、还差什么。",
            "## 工具纪律\n"
            "- 工具报错是正常反馈，读错误信息再决定下一步，不要重复同样的失败调用。\n"
            "- 被权限层拒绝时，先判断是任务需要放宽范围，还是应该停下来向用户说明。\n"
            "- 不要在回复里粘贴整个文件；引用路径和关键片段即可。",
        ]
        if self.memory:
            parts.append("## 长期记忆（会话启动时的冻结快照）\n" + self.memory.as_prompt())
        if self.skills:
            parts.append("## 可用 Skill（L0 索引）\n" + self.skills.index() + "\n（任务相关时用 skill_view 读取全文，再按其步骤执行。）")
        project = self._project_context()
        if project:
            parts.append("## 项目上下文\n" + project)
        if cfg.system_extra:
            parts.append(cfg.system_extra)
        parts.append(
            "## 环境\n"
            f"- 工作区：{Path(cfg.workspace).resolve()}\n"
            f"- 权限模式：{self.policy.mode.value}\n"
            f"- 工具组：{', '.join(self._enabled_groups())}\n"
            f"- 轮次上限：{cfg.max_turns}，token 预算：{cfg.token_budget}\n\n"
            "## 权限规则\n" + self.policy.describe()
        )
        return "\n\n".join(parts)

    def _build_subagent_system(self, task: str) -> str:
        return (
            f"你是父 Agent（{self.name}）派出的子代理，只负责这一个子任务：{task}\n"
            "你拥有独立的上下文，父会话看不到你的中间步骤，只会收到你的最终文字结论。\n"
            "因此：直接干活，不要寒暄；结论里写清关键文件路径、验证命令与结果、未解决的风险。\n"
            "你只有核心工具（列目录、读写文件），没有 shell、网络、记忆与技能工具；"
            "不要试图扩大权限，也不要再派生子代理。"
        )

    def _project_context(self) -> str:
        for name in ("AGENTS.md", "CLAUDE.md", ".hermes.md"):
            path = Path(self.config.workspace) / name
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                return f"（{name}，注入前已截断）\n" + truncate_middle(text, 4_000)
        return ""

    # ------------------------------------------------------------------ #
    # 子代理
    # ------------------------------------------------------------------ #
    def delegate_run(self, goal: str, tools: Sequence[str] | None = None) -> str:
        if self.depth >= 1:
            return "子代理不能再派生子代理（depth 上限 1）。"
        child_config = replace(
            self.config,
            mode=self.policy.mode,
            tool_groups=("core",),
            max_turns=min(8, self.config.max_turns),
            token_budget=min(40_000, self.config.token_budget),
            system_extra="",
        )
        child = Agent(
            self.llm,
            child_config,
            tracer=Tracer(path=self.tracer.path),
            hooks=self.hooks,
            approver=self.approver,
            name=f"{self.name}/sub",
            depth=self.depth + 1,
        )
        if tools:
            child.ctx.extra["allowed"] = list(tools)
        self.tracer.emit("subagent_start", agent=self.name, goal=goal[:200])
        result = child.run(goal)
        summary = result.text.strip()
        self.tracer.emit(
            "subagent_end",
            agent=self.name,
            stopped=result.stopped,
            turns=result.turns,
            chars=len(summary),
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        )
        return f"[子代理结论 · {result.stopped} · {result.turns} 轮]\n" + truncate_middle(summary, 2_000)

    def _emit(self, event: dict) -> None:
        if self.on_event:
            try:
                self.on_event({**event, "agent": self.name})
            except Exception:  # noqa: BLE001 —— 观察者异常不能影响主循环
                pass
