#!/usr/bin/env bash
# Capa 3 — AgentCore Memory: una memoria compartida por ambos agentes.
# Corto plazo: turnos de la sesión. Largo plazo: preferencias del inversionista.
source "$(dirname "$0")/../scripts/common.sh"
track_config
require agentcore jq python3

if ! has '.memories' investor_memory; then
  log "Añadiendo Memory 'investor_memory' (USER_PREFERENCE)"
  run agentcore add memory --name investor_memory --strategies USER_PREFERENCE \
    --event-expiry-duration 30 --description "Preferencias y contexto del inversionista"
fi

log "Conectando el Harness a investor_memory"
box "editando" "harness.yaml → memory: agentCoreMemoryConfiguration: {name: investor_memory}"
python3 - "$HARNESS_YAML" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
block = "memory:\n  agentCoreMemoryConfiguration:\n    name: investor_memory\n"
text, count = re.subn(r"^memory:\n(?:[ ]{2,}.*\n)+", block, text, count=1, flags=re.M)
if count != 1:
    sys.exit("No se encontró el bloque memory: en harness.yaml")
open(path, "w", encoding="utf-8").write(text)
PY

deploy
