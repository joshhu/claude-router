#!/usr/bin/env bash
# 在本機用 llama.cpp（b11773 以上才有 /v1/systemone）跑 h2o-lightning-4b 決策模型
set -euo pipefail
cd "$(dirname "$0")/.."

LLAMA_SERVER="${LLAMA_SERVER:-vendor/llama/llama-server}"
MODEL="${DECISION_GGUF:-models/h2o-lightning-4b/h2o-lightning-4b-Q8_0.gguf}"
PORT="${DECISION_PORT:-8741}"

# 參數照 h2oai/h2o-lightning-4b-gguf 模型卡：單一 slot 拿整個 context
exec "$LLAMA_SERVER" -m "$MODEL" --alias h2oai/h2o-lightning-4b \
  -c 40960 -np 1 --no-kv-unified --host 127.0.0.1 --port "$PORT"
