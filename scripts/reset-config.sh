#!/usr/bin/env bash
# Regresa la configuración local a la línea base de la capa 1 (solo Runtime y Harness),
# para volver a recorrer las capas desde cero y ver el diff de cada una.
#   scripts/reset-config.sh
# Solo edita agentcore.json, harness.yaml y cdk.json: no toca AWS. Para borrar los
# recursos desplegados, usa antes scripts/cleanup.sh.
source "$(dirname "$0")/common.sh"
require jq python3
track_config

log "Quitando Gateway, Memory, Policy, evaluaciones e Identity de agentcore.json"
edit_json "$SPEC" '.agentCoreGateways = [] | .memories = [] | .policyEngines = [] | .onlineEvalConfigs = []
  | (.runtimes[] | select(.name == "trading_agent")) |= del(.requestHeaderAllowlist)'

log "Desactivando los contextos alphainvest:* en cdk.json"
edit_json "$CDK_JSON" '.context |= with_entries(select(.key | startswith("alphainvest:") | not))'

log "Regresando harness.yaml a memory disabled y solo @finance"
box "editando" "harness.yaml → memory: disabled: {}  ·  allowedTools: - \"@browser\""
python3 - "$HARNESS_YAML" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
text = re.sub(r'^  - "@browser"\n', "", text, flags=re.M)
text, count = re.subn(r"^memory:\n(?:[ ]{2,}.*\n)+", "memory:\n  disabled: {}\n", text, count=1, flags=re.M)
if count != 1:
    sys.exit("No se encontró el bloque memory: en harness.yaml")
open(path, "w", encoding="utf-8").write(text)
PY

show_changes
log "Configuración en la línea base de la capa 1. Siguiente: TARGET=$TARGET layers/1-agents.sh"
