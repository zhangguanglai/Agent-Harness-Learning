"""模型适配层：协议 + 三个可替换后端。

循环体只依赖 `LLM.complete(messages, tools) -> LLMResponse`，
所以「离线脚本模型」和「真实 API」对 Agent 完全等价 —— 这正是能写
确定性实验的前提。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Protocol, Sequence

from .types import LLMResponse, ToolCall, Usage
from .util import estimate_messages_tokens, estimate_tokens


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:  # pragma: no cover
        ...


# --------------------------------------------------------------------------- #
# 辅助构造器：让实验脚本读起来像在描述模型行为
# --------------------------------------------------------------------------- #
def reply(text: str) -> LLMResponse:
    """模型只回文字：这通常意味着循环即将结束。"""
    return LLMResponse(text=text)


def call(tool_name: str, **arguments) -> LLMResponse:
    """模型发起一次工具调用。"""
    return LLMResponse(tool_calls=[ToolCall(name=tool_name, arguments=arguments, id=f"call_{tool_name}")])


def calls(*items: tuple[str, dict]) -> LLMResponse:
    """模型在同一轮里发起多次工具调用（可用来观察并行/串行策略）。"""
    return LLMResponse(
        tool_calls=[ToolCall(name=n, arguments=a, id=f"call_{i}_{n}") for i, (n, a) in enumerate(items)]
    )


# --------------------------------------------------------------------------- #
# 后端一：脚本模型（离线、确定性）
# --------------------------------------------------------------------------- #
class ScriptedLLM:
    """按剧本逐轮返回预设响应。

    剧本元素可以是 `LLMResponse`，也可以是 `fn(messages, tools) -> LLMResponse`，
    后者让实验能在「看到上一轮工具结果之后」再决定下一步，用于验证
    「工具结果真的回到了上下文」。
    """

    def __init__(self, script: Sequence[LLMResponse | Callable[[list[dict], list[dict]], LLMResponse]]):
        self.script = list(script)
        self.cursor = 0
        self.seen_tool_names: list[str] = []

    def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        if self.cursor >= len(self.script):
            raise LLMError("剧本已用完：模型没有说话，检查 max_turns 或剧本长度")
        step = self.script[self.cursor]
        self.cursor += 1
        resp = step(messages, tools) if callable(step) else step
        if not isinstance(resp, LLMResponse):
            raise LLMError(f"剧本第 {self.cursor} 步返回了 {type(resp)!r}，应为 LLMResponse")
        if resp.usage.total == 0:
            resp.usage = Usage(
                input_tokens=estimate_messages_tokens(messages),
                output_tokens=estimate_tokens(resp.text)
                + sum(estimate_tokens(json.dumps(c.arguments, ensure_ascii=False)) for c in resp.tool_calls),
            )
        return resp


# --------------------------------------------------------------------------- #
# 后端二：演示模型（离线，但会根据工具结果「继续做事」）
# --------------------------------------------------------------------------- #
class DemoLLM:
    """一个极简的「假模型」，用来演示一次完整的多轮任务。

    它的决策逻辑是可读的 if-else，不是真模型；存在的意义是让 `python -m
    mini_harness demo` 在没有任何 API Key 的情况下也能走完
    探索 → 读取 → 写入 → 收尾 的完整循环。
    """

    def __init__(self, target_file: str = "NOTES.md"):
        self.target_file = target_file

    def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        used = [m.get("name") for m in messages if m.get("role") == "tool"]
        last_tool = next((m for m in reversed(messages) if m.get("role") == "tool"), None)
        task = next((m["content"] for m in messages if m.get("role") == "user"), "")
        resp: LLMResponse

        if "list_dir" not in used:
            resp = call("list_dir", path=".")
        elif "read_file" not in used and any(n == "list_dir" for n in used):
            resp = call("read_file", path="README.md")
        elif "write_file" not in used:
            resp = call(
                "write_file",
                path=self.target_file,
                content=(
                    "# 由 mini-harness 生成\n\n"
                    f"任务：{task.strip()}\n\n"
                    "本文件由 DemoLLM 驱动的多轮工具循环写入，"
                    "用于验证 读 → 写 → 收尾 的完整回路。\n"
                ),
            )
        else:
            resp = reply(
                "已完成：先列出工作区，读取 README.md，再生成 "
                f"{self.target_file}。工作区里的文件已更新，可用 read_file 复核。"
            )
        resp.usage = Usage(
            input_tokens=estimate_messages_tokens(messages),
            output_tokens=max(8, estimate_tokens(resp.text) + 24),
        )
        return resp


# --------------------------------------------------------------------------- #
# 后端三：OpenAI 兼容 API（真实模型）
# --------------------------------------------------------------------------- #
class OpenAICompatLLM:
    """最小 OpenAI 兼容客户端，只用标准库。

    适配 OpenRouter / DeepSeek / vLLM / Ollama 等任何暴露
    `/chat/completions` 的服务。不依赖 SDK，便于看清线上真实报文长什么样。
    """

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.2,
        timeout: float = 120.0,
        max_retries: int = 2,
    ):
        self.model = model
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        if not self.api_key:
            raise LLMError("缺少 API Key：设置 OPENAI_API_KEY 或传 api_key=")

    def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        payload: dict = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                return self._parse(body)
            except urllib.error.HTTPError as exc:  # 4xx 不重试，5xx/429 重试
                detail = exc.read().decode("utf-8", "replace")[:500]
                last_err = LLMError(f"HTTP {exc.code}: {detail}")
                if exc.code < 500 and exc.code != 429:
                    raise last_err from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_err = LLMError(f"网络或解析失败: {exc}")
            time.sleep(0.5 * (attempt + 1))
        raise last_err or LLMError("未知错误")

    @staticmethod
    def _parse(body: dict) -> LLMResponse:
        choice = (body.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        raw_calls = msg.get("tool_calls") or []
        usage_raw = body.get("usage") or {}
        return LLMResponse(
            text=msg.get("content") or "",
            tool_calls=[ToolCall.from_openai(c) for c in raw_calls],
            usage=Usage(
                input_tokens=int(usage_raw.get("prompt_tokens") or 0),
                output_tokens=int(usage_raw.get("completion_tokens") or 0),
            ),
            raw=body,
        )
