#!/usr/bin/env bash
# Pruebas unitarias (sin AWS) y validaciones estáticas del proyecto.
#   scripts/test.sh          # unitarias + build de CDK
#   scripts/test.sh --aws    # además, pruebas contra el ambiente $TARGET
source "$(dirname "$0")/common.sh"
require uv agentcore

log "Pruebas unitarias"
run uv sync -q --project "$DEMO_DIR"
run uv run --project "$DEMO_DIR" pytest -q "$DEMO_DIR/tests"

log "Lockfile del Trading Agent"
run uv --directory app/trading_agent lock --check

log "Síntesis de CloudFormation (agentcore build)"
run agentcore build

if [[ "${1:-}" == "--aws" ]]; then
  log "Pruebas de integración contra $TARGET"
  run env TARGET="$TARGET" uv run --project "$DEMO_DIR" pytest -q -m aws -o addopts="" "$DEMO_DIR/tests"
fi
