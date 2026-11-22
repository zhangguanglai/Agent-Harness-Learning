---
name: write-changelog
description: 从 git 提交历史生成分类变更日志，追加到 CHANGELOG.md
when: 发版前需要整理变更、或用户要求「写个 changelog / 更新版本记录」时
requires_tools: bash, read_file, write_file
---

## 步骤

1. 确认范围：`git describe --tags --abbrev=0` 找到上一个 tag；没有 tag 时用最近 50 条提交。
2. 取提交：`git log --oneline <tag>..HEAD`，或 `git log --oneline -50`。
3. 分类（按 Conventional Commits 前缀）：
   - `feat` → 新功能
   - `fix` → 修复
   - `perf` / `refactor` → 优化
   - `docs` / `test` / `chore` → 其他
4. 合并同类项，去掉「update」「fix typo」这类无信息量的条目。
5. 读取现有 `CHANGELOG.md`，在顶部插入新版本小节（保持倒序）：
   ```markdown
   ## vX.Y.Z - YYYY-MM-DD

   ### 新功能
   - ...
   ```
6. 用 `read_file` 复核写入结果，确认没有重复版本号。

## 注意

- 不要编造提交里没有的内容；空分类直接省略。
- 版本号遵循现有文案习惯（是否带 `v` 前缀）。
- 如果仓库没有 `CHANGELOG.md`，先询问是否新建。
