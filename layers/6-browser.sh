#!/usr/bin/env bash
# Capa 6 — AgentCore Browser: el Portfolio Agent consulta "AlphaTrader Web", una terminal
# de trading ficticia sin API (estilo plataforma de escritorio), con un navegador
# administrado y aislado que graba cada sesión en S3 (replay en la consola).
#   - La terminal es de solo lectura: las órdenes siguen pasando por Gateway + Policy.
#   - Una noticia contiene una instrucción maliciosa oculta (prompt injection) a propósito.
source "$(dirname "$0")/../scripts/common.sh"
track_config
require agentcore jq python3

log "Activando el contexto alphainvest:browser en cdk.json"
edit_json "$CDK_JSON" '.context["alphainvest:browser"] = true'

log "Permitiendo la herramienta browser en harness.yaml"
box "editando" "harness.yaml → allowedTools: + \"@browser\""
python3 - "$HARNESS_YAML" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
# El patrón @<nombre> habilita la herramienta "browser" (igual que @finance para el Gateway).
if '"@browser"' not in text:
    text, n = re.subn(r"^allowedTools:\n", 'allowedTools:\n  - "@browser"\n', text, count=1, flags=re.M)
    if n != 1:
        sys.exit("No se encontró allowedTools: en harness.yaml")
open(path, "w", encoding="utf-8").write(text)
PY

deploy
[[ "$DRY_RUN" == "1" ]] && exit 0

URL="$(aws cloudformation describe-stacks --stack-name "AgentCore-AlphaInvest-$TARGET" \
  --query "Stacks[0].Outputs[?OutputKey=='AlphaTraderWebUrl'].OutputValue" --output text)"
log "AlphaTrader Web: $URL"
