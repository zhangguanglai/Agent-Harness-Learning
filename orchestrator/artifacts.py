"""产物存储抽象：本地目录 / 内存（生产换对象存储）。

约定：
- `put` 返回 `{path, sha256, size}`，其中 `path` 是**对调用方可见的定位符**
  （本地是文件路径，对象存储是 `s3://bucket/key`）；
- 版本与状态由编排层记录，存储层只负责字节。

生产接 S3/OSS 时实现同一个 Protocol 即可，编排与审批逻辑不用改。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class StoredObject:
    path: str
    sha256: str
    size: int


class ArtifactStore(Protocol):
    def put(self, run_id: str, name: str, content: str) -> StoredObject: ...


class LocalArtifactStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, run_id: str, name: str, content: str) -> StoredObject:
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / name
        path.write_text(content, encoding="utf-8")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return StoredObject(path=str(path), sha256=digest, size=len(content.encode("utf-8")))


class MemoryArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, str] = {}

    def put(self, run_id: str, name: str, content: str) -> StoredObject:
        key = f"mem://{run_id}/{name}"
        self.objects[key] = content
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return StoredObject(path=key, sha256=digest, size=len(content.encode("utf-8")))
