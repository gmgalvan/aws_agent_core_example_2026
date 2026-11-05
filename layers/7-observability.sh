#!/usr/bin/env bash
# Capa 7 — Observabilidad. No crea recursos nuevos: verifica y muestra lo que
# ya está activo desde la capa 1.
#   - AgentCore Observability: ADOT (OpenTelemetry) en el Runtime y trazas del Harness.
#   - CloudWatch Transaction Search: spans OTel en el log group aws/spans.
#   - Jaeger: local, y opcionalmente con spans traídos de CloudWatch.
#       TARGET=dev layers/7-observability.sh            # estado + trazas recientes
#       TARGET=dev layers/7-observability.sh --jaeger   # además, copia spans a Jaeger local
source "$(dirname "$0")/../scripts/common.sh"
require aws agentcore uv

log "Transaction Search (destino de segmentos X-Ray)"
run aws xray get-trace-segment-destination --output table

log "Trazas recientes del Trading Agent (Runtime)"
run agentcore traces runtime list --name trading_agent --target "$TARGET" --since 1h --limit 5 || true

log "Trazas recientes del Portfolio Agent (Harness)"
run agentcore traces harness list --name portfolio --target "$TARGET" --since 1h --limit 5 || true

echo
echo "CloudWatch → GenAI Observability → Bedrock AgentCore: sesiones, trazas, tokens y errores."
echo "Logs:   (cd AlphaInvest && agentcore logs runtime --name trading_agent --target $TARGET --since 30m)"

if [[ "${1:-}" == "--jaeger" ]]; then
  run "$DEMO_DIR/scripts/jaeger-local.sh"
  log "Copiando spans de los últimos 60 min a Jaeger"
  run uv run --project "$DEMO_DIR" python "$DEMO_DIR/scripts/cloudwatch_to_jaeger.py" --since-minutes 60
fi
