#!/usr/bin/env bash
# Genera agentcore/aws-targets.json con los ambientes dev, uat y prod.
# Las cuentas vienen de variables de entorno; por defecto, la cuenta actual.
#   DEV_ACCOUNT=111111111111 UAT_ACCOUNT=222222222222 PROD_ACCOUNT=333333333333 scripts/targets.sh
# El archivo contiene IDs de cuenta: está en .gitignore.
source "$(dirname "$0")/common.sh"
require aws jq

log "Qué hace: le dice a agentcore a qué cuenta y región desplegar cada ambiente (dev, uat, prod)"
echo "  TARGET=dev en los scripts de capas elige la entrada \"dev\" de este archivo; el stack se llama AgentCore-AlphaInvest-dev."
echo "  Solo escribe un archivo local: no crea nada en AWS."

log "Cuenta actual"
box "ejecutando" "\$ CURRENT_ACCOUNT=\$(aws sts get-caller-identity --query Account --output text)"
CURRENT_ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

log "Escribiendo AlphaInvest/agentcore/aws-targets.json"
box "escribiendo" "jq -n --arg dev … --arg uat … --arg prod … --arg region $REGION '[…]' > AlphaInvest/agentcore/aws-targets.json"
jq -n \
  --arg dev "${DEV_ACCOUNT:-$CURRENT_ACCOUNT}" \
  --arg uat "${UAT_ACCOUNT:-$CURRENT_ACCOUNT}" \
  --arg prod "${PROD_ACCOUNT:-$CURRENT_ACCOUNT}" \
  --arg region "$REGION" '[
    {name: "dev",  description: "Desarrollo",             account: $dev,  region: $region},
    {name: "uat",  description: "Pruebas de aceptación",  account: $uat,  region: $region},
    {name: "prod", description: "Producción",             account: $prod, region: $region}
  ]' > "$PROJECT_DIR/agentcore/aws-targets.json"

log "Contenido del archivo (cuentas ocultas)"
jq 'map(.account = "********" + .account[-4:])' "$PROJECT_DIR/agentcore/aws-targets.json"
echo "  Para separar ambientes por cuenta: DEV_ACCOUNT=… UAT_ACCOUNT=… PROD_ACCOUNT=… scripts/targets.sh"
