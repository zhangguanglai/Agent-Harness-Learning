"""实验 5：Skill 的渐进披露与自沉淀。

要看到的结论：
- system 里只常驻 L0 索引（名称 + 一句话），不占上下文；
- 模型判断相关后调 `skill_view` 才拿到全文（L1）；
- `skill_manage` 可以新建技能，索引从下一个会话生效 —— 这就是闭环的起点。

运行：python labs/lab05_skills.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import shutil
from pathlib import Path

from mini_harness import Agent, AgentConfig, Mode, ScriptedLLM, SkillManager, call, reply

TMP = _bootstrap.TMP_ROOT / "lab05"
MARKER = "MARKER_BODY_XYZ"


def prepare() -> tuple[Path, Path]:
    shutil.rmtree(TMP, ignore_errors=True)
    ws = TMP / "ws"
    ws.mkdir(parents=True)
    skills = TMP / "skills"
    skill_dir = skills / "code-review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: code-review\n"
        "description: 按清单做一次结构化代码审查\n"
        "when: 用户要求 review、检查 diff 或合并前自查时\n"
        "---\n\n"
        "## 步骤\n"
        "1. 用 git status / git diff 列出改动范围\n"
        "2. 逐个文件检查：错误处理、边界条件、命名、测试覆盖\n"
        "3. 输出「必须改 / 建议改 / 可以忽略」三档，并给出文件:行号\n\n"
        f"（内部检查清单标记：{MARKER}）\n",
        encoding="utf-8",
    )
    return ws, skills


def run() -> None:
    ws, skills = prepare()

    # ---- L0 索引 vs L1 全文 ----------------------------------------------- #
    manager = SkillManager(skills)
    index = manager.index()
    assert "code-review" in index and "结构化代码审查" in index
    assert MARKER not in index, "索引不应包含正文"
    assert MARKER in manager.view("code-review")
    print("✓ 索引只含名称+描述；全文需要 skill_view 才加载")

    # ---- 注入 system 的是索引，不是正文 ----------------------------------- #
    agent = Agent(
        ScriptedLLM([call("skill_view", name="code-review"), reply("按技能清单完成审查。")]),
        AgentConfig(workspace=ws, mode=Mode.BYPASS, skills_root=skills),
    )
    result = agent.run("帮我 review 一下这个仓库")
    system_prompt = result.messages[0]["content"]
    assert "code-review" in system_prompt and MARKER not in system_prompt
    tool_msg = next(m for m in result.messages if m.get("role") == "tool")
    assert tool_msg["name"] == "skill_view" and MARKER in tool_msg["content"]
    print("✓ system 只带索引；skill_view 之后正文才进入上下文（token 省在这里）")

    # ---- 自沉淀：模型自己创建 Skill --------------------------------------- #
    writer = Agent(
        ScriptedLLM(
            [
                call(
                    "skill_manage",
                    action="create",
                    name="write-changelog",
                    description="从 git 提交历史生成分类变更日志",
                    when="发版前需要整理 CHANGELOG 时",
                    body="## 步骤\n1. git log --oneline <上一个 tag>..HEAD\n2. 按 feat/fix/chore 分类\n3. 写入 CHANGELOG.md",
                ),
                reply("已把这次的流程沉淀成 Skill。"),
            ]
        ),
        AgentConfig(workspace=ws, mode=Mode.BYPASS, skills_root=skills),
    )
    result2 = writer.run("把刚才整理变更日志的流程记下来")
    assert result2.stopped == "final", result2.stopped
    assert (skills / "write-changelog" / "SKILL.md").exists()
    assert "write-changelog" not in result2.messages[0]["content"], "新技能在下一个会话才进索引"
    assert "write-changelog" in SkillManager(skills).index()
    print("✓ 自沉淀：新技能落盘，索引从下一个会话生效（与记忆同一条冻结语义）")

    print("\n结论：Skill 让 Agent 的「程序性记忆」可读、可审、可版本化。")


if __name__ == "__main__":
    run()
    print("\nLAB05 PASS")
