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


# --------------------------------------------------------------------------- #
# S3 兼容对象存储（SigV4，标准库实现）
# --------------------------------------------------------------------------- #
import datetime as _dt
import hmac as _hmac
import urllib.error
import urllib.request
import urllib.parse as _url

_EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_ALGORITHM = "AWS4-HMAC-SHA256"


@dataclass
class S3Config:
    endpoint: str
    bucket: str
    region: str = "us-east-1"
    access_key: str = ""
    secret_key: str = ""
    prefix: str = ""
    path_style: bool = True


def _sign(key: bytes, message: str) -> bytes:
    return _hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


class S3ArtifactStore:
    """把产物写到 S3 兼容存储（MinIO/OSS/S3），并签发预签名下载 URL。"""

    def __init__(self, config: S3Config, timeout: float = 10):
        self.config = config
        self.timeout = timeout
        self.root = config.endpoint.rstrip("/")

    def _host(self) -> str:
        return _url.urlparse(self.root).netloc

    def _key(self, run_id: str, name: str) -> str:
        return f"{self.config.prefix}{run_id}/{name}"

    def _object_path(self, key: str) -> str:
        return f"/{self.config.bucket}/{key}" if self.config.path_style else f"/{key}"

    def put(self, run_id: str, name: str, content: str) -> StoredObject:
        key = self._key(run_id, name)
        body = content.encode("utf-8")
        payload_hash = hashlib.sha256(body).hexdigest()
        now = _dt.datetime.now(_dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = now.strftime("%Y%m%d")
        canonical_uri = self._object_path(key)
        canonical_headers = f"host:{self._host()}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join([
            "PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash,
        ])
        scope = f"{date}/{self.config.region}/s3/aws4_request"
        string_to_sign = "\n".join([
            _ALGORITHM, amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest(),
        ])
        signature = _hmac.new(
            _sign(_sign(_sign(_sign(("AWS4" + self.config.secret_key).encode(), date), self.config.region), "s3"), "aws4_request"),
            string_to_sign.encode(),
            hashlib.sha256,
        ).hexdigest()
        authorization = (
            f"{_ALGORITHM} Credential={self.config.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        request = urllib.request.Request(
            self.root + canonical_uri,
            data=body,
            method="PUT",
            headers={
                "host": self._host(),
                "x-amz-content-sha256": payload_hash,
                "x-amz-date": amz_date,
                "authorization": authorization,
                "content-type": "text/markdown; charset=utf-8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"S3 PUT 失败（{exc.code}）：{exc.read()[:300]!r}") from exc
        return StoredObject(path=f"s3://{self.config.bucket}/{key}", sha256=payload_hash, size=len(body))

    def presign(self, path_or_key: str, expires: int = 900) -> str:
        key = path_or_key
        if key.startswith("s3://"):
            _, _, rest = key.partition("s3://")
            bucket, _, key = rest.partition("/")
            if bucket != self.config.bucket:
                raise ValueError(f"bucket 不匹配：{bucket}")
        now = _dt.datetime.now(_dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = now.strftime("%Y%m%d")
        scope = f"{date}/{self.config.region}/s3/aws4_request"
        canonical_uri = self._object_path(key)
        params = {
            "X-Amz-Algorithm": _ALGORITHM,
            "X-Amz-Credential": f"{self.config.access_key}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(int(expires)),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_query = "&".join(
            f"{_url.quote(k, safe='-_.~')}={_url.quote(v, safe='-_.~')}" for k, v in sorted(params.items())
        )
        canonical_request = "\n".join([
            "GET", canonical_uri, canonical_query, f"host:{self._host()}\n", "host", "UNSIGNED-PAYLOAD",
        ])
        string_to_sign = "\n".join([
            _ALGORITHM, amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest(),
        ])
        signature = _hmac.new(
            _sign(_sign(_sign(_sign(("AWS4" + self.config.secret_key).encode(), date), self.config.region), "s3"), "aws4_request"),
            string_to_sign.encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"{self.root}{canonical_uri}?{canonical_query}&X-Amz-Signature={signature}"
