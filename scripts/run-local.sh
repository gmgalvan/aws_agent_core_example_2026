#!/usr/bin/env bash
# Ejecuta el Trading Agent localmente (puerto 8080) con el servidor MCP local y
# envía trazas OpenTelemetry a Jaeger (scripts/jaeger-local.sh).
# Solo consume Amazon Bedrock; el portafolio vive en .local/portfolio.json.
source "$(dirname "$0")/common.sh"
require uv

AGENT_DIR="$PROJECT_DIR/app/trading_agent"
(cd "$DEMO_DIR" && uv sync -q) && (cd "$AGENT_DIR" && uv sync -q)

export PATH="$AGENT_DIR/.venv/bin:$PATH"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export LOCAL_FINANCE_MCP_COMMAND="$DEMO_DIR/.venv/bin/python $DEMO_DIR/dev/local_mcp_server.py"
export OTEL_SERVICE_NAME="${OTEL_SERVICE_NAME:-trading-agent-local}"
export OTEL_TRACES_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4318}"
export OTEL_METRICS_EXPORTER=none OTEL_LOGS_EXPORTER=none
export AGENT_OBSERVABILITY_ENABLED=false

log "Trading Agent local en http://localhost:8080 · trazas en http://localhost:16686"
cd "$AGENT_DIR" && exec opentelemetry-instrument python main.py
