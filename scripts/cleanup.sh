#!/usr/bin/env bash
# Elimina los recursos de un ambiente para detener costos.
#   TARGET=dev scripts/cleanup.sh
# Borra el stack AgentCore-AlphaInvest-<target> (Runtime, Harness, Gateway,
# Lambda, Memory, ECR, roles). No modifica agentcore.json: los demás ambientes
# siguen intactos. En prod la tabla DynamoDB se conserva (RETAIN) a propósito.
source "$(dirname "$0")/common.sh"
require aws

STACK="AgentCore-AlphaInvest-$TARGET"
read -r -p "¿Eliminar el stack $STACK? Escribe '$TARGET' para confirmar: " answer
[[ "$answer" == "$TARGET" ]] || die "Cancelado."

log "Eliminando $STACK"
run aws cloudformation delete-stack --stack-name "$STACK"
run aws cloudformation wait stack-delete-complete --stack-name "$STACK"
log "Stack eliminado."

echo "Recursos que pueden quedar y conviene revisar:"
echo "  - Log groups de CloudWatch con retención (prefijo /aws/bedrock-agentcore/ y /aws/lambda/AlphaInvest-$TARGET)."
[[ "$TARGET" == "prod" ]] && echo "  - Tabla DynamoDB de prod (RETAIN + deletion protection)."
echo "  - Spans en aws/spans (Transaction Search) expiran según su retención."
