#!/usr/bin/env bash
# Capa 5 — Policy: reglas Cedar deterministas en el Gateway, fuera del código del agente.
#   - Ambos agentes leen (precios, portafolio, historial).
#   - Solo el Trading Agent opera, máximo 10 acciones por orden.
#   - Todo lo demás se niega por defecto.
# Las plantillas viven en AlphaInvest/app/policies/*.cedar; cdk-stack.ts sustituye
# el ARN del Gateway y los roles de cada ambiente.
source "$(dirname "$0")/../scripts/common.sh"
track_config
require agentcore jq

if ! has '.policyEngines' trading_guardrails; then
  log "Añadiendo Policy Engine 'trading_guardrails' en modo ENFORCE"
  run agentcore add policy-engine --name trading_guardrails \
    --description "Reglas deterministas para herramientas financieras" \
    --attach-to-gateways finance --attach-mode enforce
fi

deploy
