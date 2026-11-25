# 24 · S3 产物存储与预签名下载

> 产物从「本地目录」升级到「对象存储 + 限时下载链接」，审计只认 `sha256` 与定位符。
> 代码：[orchestrator/artifacts.py](../orchestrator/artifacts.py)、实验 [lab18](../labs/lab18_s3_artifacts.py)。

## 1. 为什么是「存储抽象 + 两个实现」

```python
class ArtifactStore(Protocol):
    def put(self, run_id: str, name: str, content: str) -> StoredObject: ...
```

| 实现 | 定位符 | 场景 |
| --- | --- | --- |
| `LocalArtifactStore` | 文件路径 | 单机 / 演示 |
| `MemoryArtifactStore` | `mem://run/name` | 测试 |
| `S3ArtifactStore` | `s3://bucket/key` | 生产（S3 / MinIO / OSS） |

编排层只记录 `{path, sha256, status, version}`。换存储不动审批、审计与领域代码。

## 2. SigV4（标准库实现）

`S3ArtifactStore.put` 自己签 `AWS4-HMAC-SHA256`：

```
PUT /{bucket}/{key}
host;x-amz-content-sha256;x-amz-date  ← SignedHeaders
Credential=AK/{date}/{region}/s3/aws4_request
```

要点：

- 用**载荷哈希**（`x-amz-content-sha256`）而不是 `UNSIGNED-PAYLOAD`，防止传输被篡改；
- `path_style=True` 兼容 MinIO；
- 失败抛 `RuntimeError`，不静默——产物写不进去必须让 run 失败。

## 3. 预签名下载

```python
url = store.presign("s3://bucket/run_x/report.md", expires=600)
```

生成 `X-Amz-Algorithm/Credential/Date/Expires/SignedHeaders/Signature` 查询参数，
`host` 参与签名，`UNSIGNED-PAYLOAD`（GET 无 body）。

编排层接口：

| 产物位置 | `GET /v1/artifacts/{id}/download` |
| --- | --- |
| `s3://...` | **302** → 预签名 URL（有效期默认 900s） |
| 本地路径 | **200** + 正文 |

这样浏览器/前端拿到的都是短时链接，不需要把对象存储的长期密钥发给客户端。

## 4. 配置

```bash
python -m orchestrator --port 8081 \
  --s3-endpoint http://127.0.0.1:9000 \
  --s3-bucket banking-artifacts \
  --s3-region us-east-1 \
  --s3-access-key "$S3_AK" --s3-secret-key "$S3_SK" \
  --s3-prefix "assessments/"
```

不配 S3 时自动回退本地目录（`--artifact-dir`）。

## 5. 实测（lab18）

用本地 stub 扮演 S3，验证的是**协议形状**而非云厂商兼容性：

```
✓ S3 PUT：SigV4 头被接受，对象字节与 sha256 一致
✓ 预签名：GET 成功，签名 64 位，600s 有效
✓ 编排产物：path=s3://…，下载返回 302 预签名 URL，可直接 GET
✓ 本地产物：下载返回 200 + 正文（同一接口两种后端）
```

> 注意：lab 用 stub，不覆盖真实 S3 的 region 重定向、SSE、bucket policy 等。
> 上线前需对 MinIO 或真实 S3 做一次集成验证。

## 6. 还没做的

| 项 | 说明 |
| --- | --- |
| 分片上传 | 大产物（>100MB）需要 multipart |
| 服务端加密 | SSE-S3 / SSE-KMS |
| 对象版本与生命周期 | 依赖 bucket 策略，代码未管 |
| 预签名有效期分角色 | 目前统一 900s |
| worker 直传 | 当前由编排层代传，大文件会占控制面带宽 |

## 7. 自测

1. 为什么 PUT 用载荷哈希而预签名 GET 用 `UNSIGNED-PAYLOAD`？
2. 302 跳转到预签名 URL，相比「编排层代理下载」有什么好处与代价？
3. 如果 bucket 是私有的，预签名 URL 泄露会导致什么？如何缓解？
4. 为什么产物路径要用 `s3://bucket/key` 而不是文件路径存库？
5. 大产物场景下，为什么「worker 直传」可能更合适？控制面需要做什么？
