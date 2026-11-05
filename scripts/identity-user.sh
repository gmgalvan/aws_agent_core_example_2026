#!/usr/bin/env bash
# Crea un usuario inversionista en el user pool de Cognito del ambiente (capa 4).
#   TARGET=dev scripts/identity-user.sh ana
# La contraseña se genera al azar y se guarda en .local/cognito-<target>-<usuario>.json
# (permisos 600, fuera de git). Solo para la demo: en producción el usuario se registra
# desde la aplicación y nunca se guardan contraseñas.
# Los recuadros muestran los comandos con variables en lugar de valores: nunca la contraseña.
source "$(dirname "$0")/common.sh"
require aws jq python3

USERNAME="${1:?Uso: scripts/identity-user.sh <usuario>}"
STACK="AgentCore-AlphaInvest-$TARGET"
output() { aws cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }

log "Buscando el user pool de Cognito del stack $STACK"
box "ejecutando" "\$ POOL_ID=\$(aws cloudformation describe-stacks --stack-name $STACK --query \"…OutputKey=='InvestorsUserPoolId'…\")"
POOL_ID="$(output InvestorsUserPoolId)"
[[ -n "$POOL_ID" && "$POOL_ID" != "None" ]] || die "El stack $STACK no tiene Identity (aplica layers/4-identity.sh)."

log "Generando una contraseña aleatoria (no se muestra)"
PASSWORD="$(python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("Ai-"+"".join(secrets.choice(a) for _ in range(20))+"!9")')"

log "Creando el usuario '$USERNAME' sin enviarle correo"
box "ejecutando" "\$ aws cognito-idp admin-create-user --user-pool-id \$POOL_ID --username $USERNAME --message-action SUPPRESS"
aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username "$USERNAME" \
  --message-action SUPPRESS >/dev/null 2>&1 || log "El usuario ya existía; se reemplaza su contraseña."

log "Asignando la contraseña como permanente"
box "ejecutando" "\$ aws cognito-idp admin-set-user-password --user-pool-id \$POOL_ID --username $USERNAME --password ******** --permanent"
aws cognito-idp admin-set-user-password --user-pool-id "$POOL_ID" --username "$USERNAME" \
  --password "$PASSWORD" --permanent

mkdir -p "$DEMO_DIR/.local"
FILE="$DEMO_DIR/.local/cognito-$TARGET-$USERNAME.json"
box "guardando" "{username, password} → ${FILE#"$DEMO_DIR/"} (permisos 600)"
(umask 077 && jq -n --arg u "$USERNAME" --arg p "$PASSWORD" '{username: $u, password: $p}' > "$FILE")
log "Usuario '$USERNAME' listo en $TARGET (credenciales en ${FILE#"$DEMO_DIR/"})."
