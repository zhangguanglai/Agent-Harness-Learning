"""mini-harness：一个用来学习 Agent Harness 的极简实现。

对外只暴露四样东西：Agent、AgentConfig、三种 LLM 后端、以及工具注册表。
其余模块（context / policy / memory / skills / trace）可以单独 import，
方便在实验里只测某一层。
"""

from .context import CompactionReport, ContextManager
from .hooks import Hooks, secret_guard
from .llm import DemoLLM, LLMError, OpenAICompatLLM, ScriptedLLM, call, calls, reply
from .loop import Agent, AgentConfig, RunResult
from .memory import MemoryStore
from .policy import Decision, Mode, PermissionPolicy
from .skills import Skill, SkillManager
from .tools import Tool, ToolContext, ToolRegistry, build_core_registry
from .trace import Tracer, load_trace

__all__ = [
    "Agent",
    "AgentConfig",
    "RunResult",
    "ScriptedLLM",
    "DemoLLM",
    "OpenAICompatLLM",
    "LLMError",
    "call",
    "calls",
    "reply",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "build_core_registry",
    "Mode",
    "Decision",
    "PermissionPolicy",
    "ContextManager",
    "CompactionReport",
    "MemoryStore",
    "Skill",
    "SkillManager",
    "Hooks",
    "secret_guard",
    "Tracer",
    "load_trace",
]

__version__ = "0.1.0"
