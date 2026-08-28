#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$ROOT/scripts/stop_qwen_benchmark_server.sh" \
  "$ROOT/outputs/benchmarks/mac3108/services/production_bf16_c16_graph_prefix1/server.pid"
