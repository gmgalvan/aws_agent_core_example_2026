#!/usr/bin/env bash
# Capa 8 — Evaluations: evaluación en línea con evaluadores integrados (LLM-as-a-Judge)
# sobre las trazas de ambos agentes:
#   Builtin.GoalSuccessRate (sesión), Builtin.ToolSelectionAccuracy (herramientas),
#   Builtin.Helpfulness (respuesta).
#   - Trading Agent (Runtime): declarado en agentcore.json con `agentcore add online-eval`.
#   - Portfolio Agent (Harness): en cdk-stack.ts, con su log group real por ambiente.
# Muestreo: 100% en dev/uat, 10% en prod (Harness). Los resultados aparecen en
# CloudWatch → GenAI Observability → Evaluations.
source "$(dirname "$0")/../scripts/common.sh"
track_config
require agentcore jq

if ! has '.onlineEvalConfigs' trading_quality; then
  log "Añadiendo evaluación en línea 'trading_quality' para el Trading Agent"
  run agentcore add online-eval --name trading_quality --agent trading_agent \
    --evaluators Builtin.GoalSuccessRate Builtin.ToolSelectionAccuracy Builtin.Helpfulness \
    --sampling-rate 100 --description "Calidad del Trading Agent: objetivo, herramientas y utilidad"
fi

log "Activando el contexto alphainvest:evaluations en cdk.json"
edit_json "$CDK_JSON" '.context["alphainvest:evaluations"] = true'

deploy
