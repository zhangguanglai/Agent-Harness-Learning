"""检查 Markdown 相对链接是否有效（CI 用）。

只查相对路径（`](docs/x.md)`、`](../a/b.md)`），忽略 http(s)、锚点、mailto。
退出码：0 全部有效；1 有断链。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\]\((?!https?:|#|mailto:)([^)#]+)")


def markdown_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "AGENTS.md"]
    files += sorted((ROOT / "docs").rglob("*.md"))
    files += sorted((ROOT / "deploy").rglob("*.md"))
    files += sorted((ROOT / "spike").rglob("*.md"))
    files += sorted((ROOT / "domain").rglob("*.md"))
    return [path for path in files if path.is_file()]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    broken: list[tuple[str, str]] = []
    checked = 0
    for md in markdown_files():
        text = md.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            target = match.group(1).strip().rstrip(">")
            if not target or target.startswith("//"):
                continue
            checked += 1
            resolved = (md.parent / target).resolve()
            if not resolved.exists():
                broken.append((md.relative_to(ROOT).as_posix(), target))

    print(f"链接检查：{len(markdown_files())} 个文件，{checked} 条相对链接，断链 {len(broken)} 条")
    for source, target in broken[:30]:
        print(f"  {source} → {target}")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
