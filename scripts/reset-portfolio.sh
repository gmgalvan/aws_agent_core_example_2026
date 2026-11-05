#!/usr/bin/env bash
# Restaura el portafolio simulado del ambiente al estado inicial.
#   TARGET=dev scripts/reset-portfolio.sh            # solo el estado
#   TARGET=dev scripts/reset-portfolio.sh --history  # estado + bitácora
# Localmente: rm .local/portfolio.json
source "$(dirname "$0")/common.sh"
require aws jq
[[ "$TARGET" == "prod" ]] && die "No se reinicia prod desde este script."

STACK="AgentCore-AlphaInvest-$TARGET"
log "Buscando la tabla DynamoDB del stack $STACK"
box "ejecutando" "\$ TABLE=\$(aws cloudformation describe-stacks --stack-name $STACK --query \"…OutputKey=='PortfolioTableName'…\")"
TABLE="$(aws cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='PortfolioTableName'].OutputValue" --output text)"
[[ -n "$TABLE" && "$TABLE" != "None" ]] || die "El stack $STACK no tiene tabla (¿capa 2 aplicada?)."

log "Borrando el estado del portafolio (se recrea con los valores iniciales)"
run aws dynamodb delete-item --table-name "$TABLE" --key '{"pk":{"S":"PORTFOLIO#demo"},"sk":{"S":"STATE"}}'
if [[ "${1:-}" == "--history" ]]; then
  log "Borrando la bitácora de operaciones (TRADE#…)"
  box "ejecutando" "\$ aws dynamodb query --table-name \$TABLE --key-condition-expression \"pk = :pk AND begins_with(sk, :t)\" … | aws dynamodb delete-item (una por operación)"
  aws dynamodb query --table-name "$TABLE" --key-condition-expression "pk = :pk AND begins_with(sk, :t)" \
    --expression-attribute-values '{":pk":{"S":"PORTFOLIO#demo"},":t":{"S":"TRADE#"}}' \
    --projection-expression "pk, sk" --output json \
    | jq -c '.Items[]' | while read -r key; do
        aws dynamodb delete-item --table-name "$TABLE" --key "$key"
      done
fi
log "Portafolio de $TARGET restaurado (se recrea en la siguiente consulta)."
