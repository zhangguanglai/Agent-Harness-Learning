# Hermes Agent · 第四部分 进阶（安全、插件、架构）

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
>
> - 安全、性能与最佳实践 —— https://agent.jeffjade.com/hermes-agent/security-performance-best-practices/
> - 插件系统（Plugins） —— https://agent.jeffjade.com/hermes-agent/plugins-system/
> - 架构拆解 —— https://agent.jeffjade.com/hermes-agent/architecture-deep-dive/

## 本册要点

- 安全是七层纵深而非单开关；最常见的事故是授权层配好了、执行层仍是 `terminal.backend: local`，身份正确但爆炸半径是整台宿主机。
- 审批与 hardline 是两套机制：`approvals.mode` 可调、YOLO 只跳过提示，`UNRECOVERABLE_BLOCKLIST` 在无人值守下依然拒执行；而容器 backend 反而跳过危险命令检查，因为隔离边界被下移。
- 成本失控多来自隐性乘数：把 compression/vision/approval 分流到 `auxiliary` 便宜模型，但压缩模型窗口不得小于主模型，否则长会话「失忆」。
- 扩展走插件不改核心 `tools/`：通用插件默认「只发现不执行」，须写进 `plugins.enabled`；子目录（memory/context_engine/platforms）另有独立激活开关。
- 所有入口（CLI、Gateway、ACP、batch、API Server、Cron）都收敛到同一个 `AIAgent`，平台差异只体现为会话路由键与 85% 会话卫生压缩。

## 逐章笔记

### 安全、性能与最佳实践

- **一句话**：把「能干活」的代价摊开——哪些命令会被拦、隔离边界画在哪、token 烧在哪，以及 `hermes doctor` 如何把「感觉不对」变成可核对清单。
- **机制/要点**：
  - 七层是纵深防御而非并列开关：第 1 层管「谁能跟 Agent 说话」，第 2 层管「这句话里的命令能不能跑」，第 3 层管「跑坏之后的爆炸半径」。单层失效不应拖垮全部，但组合错配会——allowlist 开着而 backend 是 `local` 就属这类。
  - Checkpoints 在破坏性写之前快照工作目录，落在 `~/.hermes/checkpoints/store/` 的共享 shadow git 仓库里，刻意不碰项目自身 `.git`，所以回滚不污染提交历史；默认关闭（v2 opt-in），代价是影子库随时间膨胀。触发点绑「破坏性写」而非时间：`write_file`/`patch`，以及 `rm`、`mv`、`sed -i`、重定向 `>`、`git reset|clean|checkout`，按目录每轮最多一次。
  - 审批三档差在「谁判风险」：`manual` 命中模式就问你，`smart` 交辅助模型打分（低风险放行、高风险拒、不确定转人工），`off` 只留 hardline 兜底；超时按 deny 处理（fail-closed），这是安全默认值不是缺陷。
  - hardline 实现在 `tools/approval.py::UNRECOVERABLE_BLOCKLIST`，YOLO、`off`、Cron 无人值守一律拒——它的定位是「不可恢复」而非「高风险」。容器 backend（docker/singularity/modal/daytona/vercel_sandbox）会**跳过**危险命令检查，因为隔离边界已在容器内；但挂载卷仍在射程内，`docker_forward_env` 还会把列出的变量送进容器供恶意代码读走。
  - 双层压缩的阈值刻意错开：Gateway 约 85% 做会话卫生，`ContextCompressor` 默认 50% 做循环内主压缩。85% 更高是为避免长会话每轮都被压一遍——压太勤本身就在烧钱。
  - Prompt Caching 吃的是前缀稳定：`SOUL.md`/`MEMORY.md` 在会话开始时冻结注入，会话内用 `memory` 工具改盘不动已缓存前缀。这既是缓存优化，也正是「改了记忆、当前会话却不生效」的根因。
- **命令 / 配置 / 代码**：

```yaml
# ~/.hermes/config.yaml
approvals: {mode: smart, timeout: 60}       # manual|smart|off，超时 deny
compression: {threshold: 0.50, protect_last_n: 20}
auxiliary: {compression: {model: null, provider: auto}}
prompt_caching: {cache_ttl: "5m"}           # 或 "1h"
security: {allow_private_urls: false, allow_lazy_installs: false}
terminal: {backend: docker, container_cpu: 1, container_memory: 5120, docker_forward_env: []}
```

```bash
hermes chat --checkpoints                   # 单次；全局用 checkpoints.enabled
hermes doctor                               # 体检；--ack <advisory-id> 忽略供应链告警
hermes pairing approve telegram ABC12DEF    # 配对码 1h 过期、每平台最多 3 个待处理
hermes dashboard                            # 默认 127.0.0.1:9119，仅回环
```

- **坑与边界**：
  - 装了 Docker 却没改 `terminal.backend` 是最常见的假隔离，必须显式 `hermes config set terminal.backend docker`。
  - 带 `terminal` 的 bot 不能开 `GATEWAY_ALLOW_ALL_USERS=true`；未配 allowlist 又未显式放行时默认拒绝所有人，现象是「Gateway 不回复」。
  - 压缩模型窗口小于主模型 → 摘要静默失败、长会话「失忆」；checkpoint 也救不了已 `git push`、已发外部消息、已发支付、已跑 migration 这类不可逆操作。
  - 公网开 `security.allow_private_urls` 等于放 Agent 扫内网与云元数据；`hermes dashboard` 的权限等价于能登录该主机，远程直连必须反代 + TLS + 强认证。
- **原文**：https://agent.jeffjade.com/hermes-agent/security-performance-best-practices/

### 插件系统（Plugins）

- **一句话**：插件是官方指定的扩展面——团队定制工具、Slash、CLI 子命令、hooks、平台适配器乃至记忆与上下文后端，都不该塞进核心 `tools/`。
- **机制/要点**：
  - 能力面统一收在一个 `ctx` 上：`register_tool` / `register_hook` / `register_command`（CLI 与 Gateway 共用）/ `register_cli_command` / `register_skill`（落到 `plugin:skill` 命名空间）/ `inject_message` / `register_platform` / `register_context_engine`，Provider 走 `register_provider`。因此插件天生跨入口生效，不必为终端和消息网关各写一份。
  - 发现四级、后者覆盖前者：bundled `plugins/` → `~/.hermes/plugins/` → `./.hermes/plugins/`（需 `HERMES_ENABLE_PROJECT_PLUGINS=true`）→ pip entry point `hermes_agent.plugins`；同名时用户目录能盖掉 bundled 版本。但子目录决定激活开关，这是最易误解处：`plugins/platforms/` 看 `gateway.platforms.*.enabled`，`plugins/memory/` 看 `memory.provider`，`plugins/context_engine/` 看 `context.engine`，`plugins/model-providers/` 在 `hermes model` 里选——`plugins.enabled` 只管根目录下的通用插件。
  - 通用插件默认「只发现、不执行」，须显式 enable（`disabled` 是 deny-list 且优先于 `enabled`）；项目级目录默认关闭、`hermes plugins install` 收尾的 `Enable now?` 默认 N，两处默认值都在防供应链静默执行。
  - 记忆插件走**独立发现系统**：内置 `MEMORY.md`/`USER.md` 始终保留，外部 provider 只在其上叠加注入、预取、同步，且同时只能激活一个；官方要求新后端独立发布，不再往核心 `plugins/memory/` 提 PR。上下文引擎插件同理，替换默认 `ContextCompressor` 需显式 `context.engine` 才接管。
  - 分工按「交付物」切：插件工具=稳定 schema + 代码逻辑 + 可分发；plugin hook=横切策略与审计，不新增工具名；Skill=教模型流程；MCP=外部进程与第三方生态。
- **命令 / 配置 / 代码**：

```yaml
# ~/.hermes/config.yaml
plugins: {enabled: [hello-world], disabled: [noisy-plugin]}   # disabled 优先
memory: {provider: honcho}                                    # 单选
context: {engine: my-engine-name}                             # 单选，需显式写
```

```python
# ~/.hermes/plugins/hello-world/__init__.py（同目录还需 plugin.yaml）
import json
def register(ctx):
    def handle(params, **kw):
        return json.dumps({"success": True, "greeting": f"Hello, {params['name']}!"})
    ctx.register_tool(name="hello_world", toolset="hello_world", handler=handle, description="问好",
        schema={"name": "hello_world", "description": "向名字问好",
            "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}})
```

```bash
hermes plugins [enable|disable] <name>    # 交互开关：hermes plugins；装包：install owner/repo
hermes memory setup | status | off        # 切换 provider 后需重启
```

- **坑与边界**：
  - 「工具不出现」九成是没 enable 而非代码写错，项目插件不可见则先查 `HERMES_ENABLE_PROJECT_PLUGINS`；已有 bundled 工具能覆盖的需求，优先用 Skill 组合现有 terminal/web，别用插件重造。
  - 未审阅就把第三方仓库写进 `plugins.enabled` 等同于运行任意 Python；插件与内置工具同名还会互相覆盖，症状是「调到的不是你写的那个」，需改名或 disable 其一。
  - 同时配两个 `memory.provider` 不被支持（单选），切换后未重启会出现双写异常，先 `hermes memory off` 再 `setup`。
- **原文**：https://agent.jeffjade.com/hermes-agent/plugins-system/

### 架构拆解

- **一句话**：所有入口都在 `AIAgent` 处收敛，平台差异被压成「会话路由键 + 会话卫生压缩」，读懂这条主路径就能排查大部分「CLI 正常、Gateway 不对」的问题。
- **机制/要点**：
  - 入口层是 `cli.py`、`gateway/run.py`、`acp_adapter`、batch 与 API Server，全部落到 `run_agent.py` 的 `AIAgent`；它内部串起 prompt builder、Provider 解析、`model_tools` 分发、`ContextCompressor`、prompt caching 与中断回调。设计原则可归为：提示稳定、执行可观测、可中断、平台无关、松耦合（注册表 + `check_fn`）、Profile 隔离。
  - 内部消息统一为 OpenAI 风格 dict（`system`/`user`/`assistant`/`tool`），只在边界转成三种 `api_mode`：`chat_completions`、`codex_responses`、`anthropic_messages`；解析优先级是构造参数 > Provider 检测 > base URL 启发 > 默认 `chat_completions`。交替有硬约束：不能连续两条 `user` 或 `assistant`（工具阶段可连续 `tool`），违反会被 Provider 直接拒收。
  - 工具管线分三层：单个 tool call 主线程直跑、多个走 `ThreadPoolExecutor` 并行但结果按原顺序写回（`clarify` 等交互工具强制串行），而 `todo`/`memory`/`session_search`/`delegate_task` 四个工具在 `run_agent.py` 里**先于 registry** 就被拦截；`tools/registry.py` 靠各 `tools/*.py` 在 import 时 `registry.register()` 自注册（所以新工具 = 新文件 + 顶层 `register()`，无需改中央 import 列表），`toolsets.py` 按平台分组决定暴露哪些，`hermes_cli/commands.py` 的 `COMMAND_REGISTRY` 则是 Slash 命令单一来源、CLI 与 Gateway 共用。迭代预算默认约 90 轮（`agent.max_turns`），子 Agent 走 `delegation.max_iterations`（默认 50）。
  - Prompt 被拆成「会话级缓存系统提示」与「API 调用瞬时层」（`ephemeral_system_prompt`、Gateway 覆盖不写入持久前缀）。组装顺序：身份（`SOUL.md`，否则 `DEFAULT_AGENT_IDENTITY`）→ 工具使用指引 → 可选 Honcho 静态块 → config/API `system_message` → 冻结 `MEMORY.md` → 冻结 `USER.md` → Skills 索引（L0 名称+描述）→ 项目上下文 → 时间戳/session id → 平台提示（CLI 纯文本 vs Gateway Markdown）。
  - 项目上下文**只加载一种**，按 `.hermes.md`/`HERMES.md`（CWD 向上到 git 根）> `AGENTS.md`（仅 CWD）> `CLAUDE.md` > `.cursorrules`/`.cursor/rules/*.mdc` 先匹配先生效；注入前做安全扫描并按约 20k 字符截断（保留头尾）。子目录 `AGENTS.md` 由 `subdirectory_hints.py` 在工具结果里渐进注入，而非首轮全塞进 system。
  - 三条数据流差异只在「历史从哪来、回哪儿去」：CLI 是 `process_input → run_conversation → SessionDB`；Gateway 多一步 `_is_user_authorized()` 与 session key 解析、可选 85% 卫生压缩、再 delivery 回平台；Cron 从 `jobs.json` 取到期任务后**新建**一个通常无历史的 `AIAgent`，注入 Skill 上下文执行并更新 `next_run`——它是 Agent 任务而非裸 shell。
- **命令 / 配置 / 代码**：

```bash
# ~/.hermes/.env：OpenAI 兼容 API Server
API_SERVER_ENABLED=true
API_SERVER_KEY=change-me-local-dev
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8642
```

```bash
curl http://127.0.0.1:8642/v1/chat/completions \
  -H "Authorization: Bearer change-me-local-dev" -H "Content-Type: application/json" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"Hello"}]}'
# 端点：/v1/chat/completions 无状态、/v1/responses 服务端存链、/v1/runs 长跑+SSE、/v1/models、/health
```

```python
agent.chat("修复 main.py 的 bug")            # 只要最终字符串
agent.run_conversation(user_message="...")   # 要 messages / usage / 元数据
```

- **坑与边界**：
  - 新工具不出现，先确认 `registry.register()` 在模块顶层（import 时才执行），而不是漏了某条 import；Gateway 与 CLI 工具集不同属正常，用 `hermes tools` 分平台核对。
  - 改 `MEMORY.md` 不见系统提示变化是设计而非 bug——快照在新会话才重建；要本会话可见只能开新 session。
  - API Server 等同开放完整 toolset（含 `terminal`），公网暴露就是远程 shell；非回环绑定必须设 `API_SERVER_KEY`，浏览器直连才需收窄 `API_SERVER_CORS_ORIGINS`，多用户应按 Profile 分端口与 Key。
  - 不要在 `run_agent.py` 写平台分支（如 `if Telegram`），差异应落 adapter 或 callback；也不要长期关压缩调试，Gateway 的 85% 阈值本身是防 API 超长失败的安全网。
- **原文**：https://agent.jeffjade.com/hermes-agent/architecture-deep-dive/

## 跨章节关联

- **与本站其他章节的关系**：
  - 安全章的容器 backend 与凭据剥离，延续「工具系统」里 terminal 的路由链路（`terminal_tool.py` → `tools/environments/`）；架构章又把这条链路点明为 registry 之外的分发路径。
  - 85% 会话卫生压缩、授权顺序、session key 路由必须与「消息网关」章对读：Gateway 的 `gateway/session.py` 与 CLI 共享 Agent 核心，差别只在路由键含平台 id / chat id / topic。
  - Prompt 组装中的冻结 `MEMORY.md`/`USER.md` 与 Skills 索引，是「记忆、学习与 Skill」章的上游依据；「技能系统实战」里的渐进加载正对应这里只注入名称+描述。
  - 插件的 `ctx.register_hook` 与「事件钩子（Event Hooks）」同源，插件章的价值在于说清何时该用插件而非 hook；成本表里的子 Agent 乘数与 `/goal` 默认 20 轮，呼应「高级特性」与「Kanban 多 Agent 看板」。
- **与通用 Harness 概念的对应**：
  - 代理循环 → `AIAgent.run_conversation` 的迭代：追加消息、构建/复用系统提示、超阈值预压缩、组装消息并施加 cache_control、可中断 API 调用、执行 tool_calls 回写、持久化并 flush memory。
  - 工具 → import 自注册 + `toolsets` 分组 + `check_fn` 依赖探测，唯一分发口是 `model_tools.handle_function_call`。
  - 权限 → 七层模型；审批层是「工具级」、容器层是「执行面」、allowlist/pairing 是「主体」，三者正交，缺一层就漏一类风险。
  - 上下文 → 双层压缩（Gateway 85% / Agent 50%）+ Prompt Caching 的 `system_and_3` 断点（系统提示 + 滚动 3 条非 system 消息，Anthropic 上限 4 个断点）。
  - 记忆 → 内置 `MEMORY.md`/`USER.md` 冻结快照 + 单一 Memory Provider 叠加；SessionDB（SQLite + FTS5）供 `session_search`，压缩生成子 session 谱系便于追溯。
  - Skill / 子代理 / MCP / 沙箱 → `register_skill(name, path)` 落 `plugin:skill`，与内置 Skill 共用 L0 注入位；`delegate_task` 在 registry 之前被拦截并受 `delegation.max_iterations` 约束、可 `skip_context_files`；MCP 子进程只继承 `PATH`/`HOME` 等安全变量、工具名带服务器前缀；沙箱即 `terminal.backend` 的六种取值，Docker 默认带 `--cap-drop ALL` 与 `no-new-privileges`。

## 自测（3 题）

1. **Gateway 已配 allowlist，为什么仍可能被一条 `rm -rf` 打穿？补哪一层、怎么验证？**
   提示：allowlist 只解决第 1 层「谁能说话」，执行面还看 `approvals.mode` 与 `terminal.backend`。补第 3 层换成 docker（注意容器下危险命令检查会被跳过，隔离全靠容器本身），再 `hermes config get terminal.backend` 与 `hermes doctor` 核对，并确认 `docker_forward_env: []` 没把密钥送进容器。

2. **为什么 `auxiliary.compression` 不能随手指定一个便宜小模型？**
   提示：官方要求压缩模型上下文窗口不得小于主模型。窗口不足会让中间段摘要失败并静默丢上下文，表现是长会话「健忘」而不报错；用 `/usage` 对比压缩前后 token 才看得出。省下的模型费抵不过丢掉的质量。

3. **加了一个工具插件却有三种「不生效」，根因分别在哪一层？**
   提示：①工具没进 schema → 通用插件默认只发现不执行，查 `plugins.enabled`；②插件在但被内置同名人顶掉 → 后发现的覆盖先发现的，改名或 disable 其一；③放 `./.hermes/plugins/` 下全无反应 → 项目插件默认关闭，需 `HERMES_ENABLE_PROJECT_PLUGINS=true`。若改的是核心 `tools/*.py`，则要确认 `registry.register()` 在模块顶层且已被 discovery 链 import。
