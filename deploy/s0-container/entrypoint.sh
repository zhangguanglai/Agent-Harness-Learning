#!/usr/bin/env bash
# S0-1 容器入口：headless 一次性任务 / worker 常驻服务 / shell 排查
set -euo pipefail

MODE="${1:-headless}"
shift || true

BUNDLE_PATCH="/opt/banking-a/bundle/cordis.patch.yml"

case "$MODE" in
  headless)
    TASK="${TASK:-请列出当前目录的文件，然后写一个 HELLO.md，内容只有一行：ok}"
    echo "[s0] headless task: $TASK"
    exec dsh headless --patch "$BUNDLE_PATCH" "$TASK"
    ;;
  worker)
    export DSH_BACKEND="${DSH_BACKEND:-sdk}"
    export DSH_BIN="${DSH_BIN:-dsh}"
    export DSH_ARGS="${DSH_ARGS:---profile sdk --patch $BUNDLE_PATCH}"
    export DSH_PROVIDER="${DSH_PROVIDER:-deepseek-official}"
    export DSH_MODEL="${DSH_MODEL:-deepseek-chat}"
    export PORT="${PORT:-8080}"
    echo "[s0] worker on :$PORT (provider=$DSH_PROVIDER model=$DSH_MODEL)"
    exec node /opt/banking-a/worker/src/server.js
    ;;
  shell)
    exec bash "$@"
    ;;
  *)
    echo "unknown mode: $MODE (expected headless|worker|shell)" >&2
    exit 2
    ;;
esac
