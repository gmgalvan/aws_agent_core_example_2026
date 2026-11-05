#!/usr/bin/env bash
# Capa 2 — AgentCore Gateway: las herramientas financieras (Lambda + DynamoDB)
# se publican como MCP y ambos agentes las comparten con autorización IAM.
source "$(dirname "$0")/../scripts/common.sh"
track_config
require agentcore jq

if ! has '.agentCoreGateways' finance; then
  log "Añadiendo Gateway 'finance' (MCP, AWS_IAM)"
  run agentcore add gateway --name finance --protocol-type MCP \
    --authorizer-type AWS_IAM --description "Herramientas financieras simuladas de AlphaInvest"
fi

if ! jq -e '.agentCoreGateways[] | select(.name=="finance") | any(.targets[]?; .name=="finance-tools")' "$SPEC" >/dev/null; then
  log "Añadiendo target Lambda 'finance-tools'"
  target_json="$(mktemp)"
  jq -n --slurpfile tools "$PROJECT_DIR/app/finance_tools/tool_definitions.json" '{
    name: "finance-tools",
    targetType: "lambda",
    toolDefinitions: $tools[0],
    compute: {
      host: "Lambda",
      implementation: {language: "Python", path: "app/finance_tools", handler: "handler.lambda_handler"},
      pythonVersion: "PYTHON_3_12",
      timeout: 15,
      memorySize: 256
    }
  }' > "$target_json"
  run agentcore add gateway-target --gateway finance --target-configuration "file://$target_json"
  rm -f "$target_json"
fi

# La herramienta del Harness se conecta en agentcore/cdk/lib/cdk-stack.ts con el
# ARN real de cada ambiente; no hay ARN fijo en harness.yaml.
deploy
