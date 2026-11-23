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
    DSH_HOME=/tmp/dsh-home \
    HOME=/tmp \
    DSH_VERSION=0.2.2-rc.5

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g "@deepseek-ai/dsh@${DSH_VERSION}" \
 && npm cache clean --force

# npm 全局安装会把平台原生的 node-addon-require-builtin-* 提升到 dsh/node_modules，
# 而 loader 期望它在主包内（或 build/ 下的本地构建产物）。这里补两处软链，
# 否则 headless 启动时报 "Cannot find module .../require_builtin.node"。
RUN mkdir -p /usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/node-addon-require-builtin/build/napi/napi-v9-linux-x64-gnu \
 && ln -sfn /usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/node-addon-require-builtin-linux-x64-gnu/prebuilt/linux-x64-gnu-napi-v9.node \
    /usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/node-addon-require-builtin/build/napi/napi-v9-linux-x64-gnu/require_builtin.node \
 && mkdir -p /usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/node-addon-require-builtin/node_modules \
 && ln -sfn ../../node-addon-require-builtin-linux-x64-gnu \
    /usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/node-addon-require-builtin/node_modules/node-addon-require-builtin-linux-x64-gnu \
 && ln -sf /usr/bin/python3 /usr/local/bin/python \
 && useradd --create-home --uid 10001 harness \
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
