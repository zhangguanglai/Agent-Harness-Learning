# S0-1：容器内运行 DSH（headless / worker）
#
# 构建（在仓库根目录）：
#   docker build -f deploy/s0-container/Dockerfile -t banking-a/s0:local .
#
# 运行 headless（需要模型 Key）：
#   docker run --rm -e DEEPSEEK_API_KEY -v "$PWD/workspace:/workspace" banking-a/s0:local headless
#
# 安全默认：非 root、只读根文件系统（由运行命令加 --read-only + tmpfs）、
# 工作区单独挂载、默认只放行模型 API（网络策略见 README）。

FROM node:24-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    BANKING_A_HOME=/opt/banking-a \
    PYTHONPATH=/opt/banking-a \
    DSH_HOME=/home/harness/.dsh \
    DSH_VERSION=0.2.2-rc.5

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g "@deepseek-ai/dsh@${DSH_VERSION}" \
 && npm cache clean --force

RUN useradd --create-home --uid 10001 harness \
 && mkdir -p /opt/banking-a /workspace \
 && chown -R harness:harness /opt/banking-a /workspace

COPY --chown=harness:harness deploy/s0-container/entrypoint.sh /opt/banking-a/entrypoint.sh
COPY --chown=harness:harness spike/dsh-bundle /opt/banking-a/bundle
COPY --chown=harness:harness spike/dsh-worker /opt/banking-a/worker
COPY --chown=harness:harness domain /opt/banking-a/domain

RUN chmod +x /opt/banking-a/entrypoint.sh

USER harness
WORKDIR /workspace
EXPOSE 8080

ENTRYPOINT ["/opt/banking-a/entrypoint.sh"]
CMD ["headless"]
