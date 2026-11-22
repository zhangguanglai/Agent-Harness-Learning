"""实验共用引导：让 `python labs/xxx.py` 直接可跑。

做两件事：
1. 把仓库根加入 sys.path，使 `import mini_harness` 成立；
2. 把标准输出切到 UTF-8（Windows 控制台默认 GBK，打印 ✓ 会报错）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

TMP_ROOT = Path(__file__).resolve().parent / "_tmp"
