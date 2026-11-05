#!/usr/bin/env bash
# Invoca el Trading Agent local: scripts/invoke-local.sh "¿Cuál es el precio de NVDA?"
source "$(dirname "$0")/common.sh"
PROMPT="${1:-Analiza AAPL y MSFT y realiza una operación de paper trading si consideras que hay una oportunidad.}"
SESSION="${SESSION_ID:-local-$(date +%s)-0000000000000000000000}"
curl -sN -X POST http://localhost:8080/invocations \
  -H 'Content-Type: application/json' \
  -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: $SESSION" \
  -d "$(jq -n --arg p "$PROMPT" '{prompt: $p}')" \
  | sed -un 's/^data: //p' | python3 "$DEMO_DIR/scripts/render_events.py"
