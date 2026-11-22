"""Skill：渐进披露的程序性记忆。

三层加载，与 Claude Code / Hermes 的做法对齐：

- **L0 索引**（常驻 system）：只有 name + 一句话描述，约 20–40 token；
- **L1 全文**（按需）：模型判断相关后调 `skill_view`，把 SKILL.md 读进上下文；
- **L2 附件**（再按需）：SKILL.md 里引用的脚本、模板，用普通文件工具读。

这样技能库可以很大而不撑爆上下文 —— 这是「渐进披露」的全部意义。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .tools import ToolContext, ToolRegistry
from .util import ensure_dir, read_text, write_text

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    when: str = ""
    requires: str = ""

    @property
    def index_line(self) -> str:
        extra = f"｜何时用：{self.when}" if self.when else ""
        return f"- **{self.name}**：{self.description}{extra}"


def parse_skill(text: str, path: Path) -> Skill:
    meta: dict[str, str] = {}
    match = FRONTMATTER.match(text)
    body = text
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip().lower()] = value.strip()
        body = match.group(2)
    name = meta.get("name") or path.parent.name
    return Skill(
        name=name,
        description=meta.get("description", "（缺少 description）"),
        body=body.strip(),
        path=path,
        when=meta.get("when", ""),
        requires=meta.get("requires_tools", ""),
    )


class SkillManager:
    def __init__(self, root: Path):
        self.root = Path(root)

    # -- 读取 -------------------------------------------------------------- #
    def load(self) -> list[Skill]:
        if not self.root.is_dir():
            return []
        skills = []
        for path in sorted(self.root.glob("*/SKILL.md")):
            skills.append(parse_skill(read_text(path), path))
        return skills

    def get(self, name: str) -> Skill | None:
        for skill in self.load():
            if skill.name == name or skill.path.parent.name == name:
                return skill
        return None

    def index(self) -> str:
        skills = self.load()
        if not skills:
            return "（当前没有可用 Skill。完成非平凡任务后可考虑把步骤沉淀为一个 Skill。）"
        return "\n".join(s.index_line for s in skills)

    def view(self, name: str) -> str:
        skill = self.get(name)
        if skill is None:
            available = ", ".join(s.name for s in self.load()) or "无"
            raise KeyError(f"没有名为 {name!r} 的 Skill。可用：{available}")
        return (
            f"# Skill: {skill.name}\n"
            f"{skill.body}\n\n"
            f"（来源：{skill.path.as_posix()}；如引用其他文件，用 read_file 按需读取）"
        )

    # -- 写入 -------------------------------------------------------------- #
    def create(self, name: str, description: str, body: str, when: str = "") -> Skill:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "skill"
        folder = ensure_dir(self.root / slug)
        path = folder / "SKILL.md"
        if path.exists():
            raise FileExistsError(f"Skill {slug!r} 已存在；如需修改请用 action=patch")
        front = [f"name: {name}", f"description: {description}"]
        if when:
            front.append(f"when: {when}")
        write_text(path, "---\n" + "\n".join(front) + "\n---\n\n" + body.strip() + "\n")
        return parse_skill(read_text(path), path)

    def patch(self, name: str, old: str, new: str) -> str:
        skill = self.get(name)
        if skill is None:
            raise KeyError(f"没有名为 {name!r} 的 Skill")
        text = read_text(skill.path)
        if old not in text:
            raise ValueError("patch 的 old 片段未匹配，请先 skill_view 查看当前全文")
        write_text(skill.path, text.replace(old, new, 1))
        return f"已更新 {skill.path.name}"

    def delete(self, name: str) -> str:
        skill = self.get(name)
        if skill is None:
            raise KeyError(f"没有名为 {name!r} 的 Skill")
        shutil.rmtree(skill.path.parent)
        return f"已删除 Skill {skill.name}"


# --------------------------------------------------------------------------- #
# 给 Agent 用的 Skill 工具
# --------------------------------------------------------------------------- #
def register_skill_tools(registry: ToolRegistry, manager: SkillManager) -> None:
    @registry.tool(
        name="skill_view",
        description="读取某个 Skill 的完整正文（L1）。当 L0 索引中的某个技能与当前任务相关时调用。",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Skill 名称或目录名"}},
            "required": ["name"],
        },
        read_only=True,
        group="skills",
    )
    def skill_view(args: dict, ctx: ToolContext) -> str:
        return manager.view(str(args.get("name") or ""))

    @registry.tool(
        name="skill_manage",
        description=(
            "维护 Skill。action=create 新建（需 name/description/body），patch 局部修改（需 old/new），"
            "delete 删除。建议：一个任务用掉 5 次以上工具调用、且未来可能复用时，再沉淀为 Skill。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "patch", "delete"]},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "body": {"type": "string"},
                "when": {"type": "string", "description": "何时使用该技能"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["action", "name"],
        },
        group="skills",
    )
    def skill_manage(args: dict, ctx: ToolContext) -> str:
        action = str(args.get("action") or "")
        name = str(args.get("name") or "")
        if action == "create":
            skill = manager.create(
                name=name,
                description=str(args.get("description") or ""),
                body=str(args.get("body") or ""),
                when=str(args.get("when") or ""),
            )
            return f"已创建 Skill {skill.name}（{skill.path.as_posix()}）；索引将在下一个会话进入 system 提示"
        if action == "patch":
            return manager.patch(name, str(args.get("old") or ""), str(args.get("new") or ""))
        if action == "delete":
            return manager.delete(name)
        raise ValueError(f"未知 action: {action}")
