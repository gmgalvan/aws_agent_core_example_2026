#!/usr/bin/env bash
# Capa 4 — Identity: el Trading Agent solo acepta usuarios autenticados.
#   - Cognito (user pool + app client) en el stack de cada ambiente.
#   - El Runtime cambia de IAM a autorizador JWT (CUSTOM_JWT) de AgentCore Identity.
#   - El agente lee el usuario del token: Memory queda aislada por usuario real.
# El Portfolio Agent (Harness) sigue con IAM: es un agente interno.
#   TARGET=dev layers/4-identity.sh [usuario-demo]
source "$(dirname "$0")/../scripts/common.sh"
track_config
require agentcore jq

log "Activando el contexto alphainvest:identity en cdk.json"
edit_json "$CDK_JSON" '.context["alphainvest:identity"] = true'

log "Permitiendo el header Authorization hacia el código del Trading Agent"
edit_json "$SPEC" '(.runtimes[] | select(.name == "trading_agent") | .requestHeaderAllowlist) = ["Authorization"]'

deploy

[[ "$DRY_RUN" == "1" ]] || run "$DEMO_DIR/scripts/identity-user.sh" "${1:-ana}"
