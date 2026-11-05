#!/usr/bin/env bash
# Lista o cierra las sesiones del navegador de la capa 6.
#   TARGET=dev scripts/browser-sessions.sh          # listar (Live View: consola → Browser)
#   TARGET=dev scripts/browser-sessions.sh --stop   # cerrar las activas, subir la grabación y resumirla
# Una sesión abierta sigue facturando y su grabación no llega a S3 hasta que termina.
source "$(dirname "$0")/common.sh"
require aws jq uv

box "ejecutando" "\$ BROWSER_ID=\$(aws cloudformation describe-stacks --stack-name AgentCore-AlphaInvest-$TARGET --query \"…OutputKey=='TerminalBrowserId'…\")"
BROWSER_ID="$(aws cloudformation describe-stacks --stack-name "AgentCore-AlphaInvest-$TARGET" \
  --query "Stacks[0].Outputs[?OutputKey=='TerminalBrowserId'].OutputValue" --output text)"
[[ -n "$BROWSER_ID" && "$BROWSER_ID" != "None" ]] || die "El ambiente $TARGET no tiene la capa 6."

box "ejecutando" "\$ aws bedrock-agentcore list-browser-sessions --browser-identifier \$BROWSER_ID"
ACTIVE="$(aws bedrock-agentcore list-browser-sessions --browser-identifier "$BROWSER_ID" --output json \
  | jq -r '.items[] | select(.status != "TERMINATED") | .sessionId')"

if [[ "${1:-}" != "--stop" ]]; then
  (cd "$PROJECT_DIR" && aws bedrock-agentcore list-browser-sessions --browser-identifier "$BROWSER_ID" \
    --query 'items[].[sessionId,status,createdAt]' --output table)
  exit 0
fi

[[ -n "$ACTIVE" ]] || { log "No hay sesiones activas."; exit 0; }
for session in $ACTIVE; do
  log "Cerrando $session"
  box "ejecutando" "\$ aws bedrock-agentcore stop-browser-session --browser-identifier \$BROWSER_ID --session-id $session"
  aws bedrock-agentcore stop-browser-session --browser-identifier "$BROWSER_ID" --session-id "$session" >/dev/null
done

last="$(echo "$ACTIVE" | tail -1)"
log "Esperando la grabación de $last en S3"
box "ejecutando (cada 10 s, hasta 3 min)" "\$ uv run python scripts/browser_recording.py $last"
for _ in $(seq 1 18); do
  (cd "$DEMO_DIR" && TARGET="$TARGET" uv run python scripts/browser_recording.py "$last" 2>/dev/null) && exit 0
  sleep 10
done
die "La grabación aún no aparece; reintenta: TARGET=$TARGET uv run python scripts/browser_recording.py $last"
