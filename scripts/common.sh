#!/usr/bin/env bash
# Funciones compartidas por los scripts de capas. Se carga con `source`.
set -euo pipefail

DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$DEMO_DIR/AlphaInvest"
SPEC="$PROJECT_DIR/agentcore/agentcore.json"
HARNESS_YAML="$PROJECT_DIR/app/portfolio/harness.yaml"
CDK_JSON="$PROJECT_DIR/agentcore/cdk/cdk.json"
CONFIG_FILES=("$SPEC" "$HARNESS_YAML" "$CDK_JSON")

# Ambiente destino: dev | uat | prod (entradas de agentcore/aws-targets.json).
TARGET="${TARGET:-dev}"

# Colores solo en una terminal; NO_COLOR=1 los desactiva.
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_BOX=$'\033[0;90m' C_CMD=$'\033[1;33m' C_ASK=$'\033[1;35m' C_RST=$'\033[0m'
else
  C_BOX="" C_CMD="" C_ASK="" C_RST=""
fi

log() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

require() {
  for cmd in "$@"; do command -v "$cmd" >/dev/null || die "Falta el comando '$cmd'."; done
}

require_target() {
  [[ -f "$PROJECT_DIR/agentcore/aws-targets.json" ]] || die "No hay targets. Ejecuta scripts/targets.sh."
  jq -e --arg t "$TARGET" 'any(.[]; .name == $t)' "$PROJECT_DIR/agentcore/aws-targets.json" >/dev/null \
    || die "El target '$TARGET' no existe en aws-targets.json. Ejecuta scripts/targets.sh."
}

has() { # has <jq-path-to-array> <name>
  jq -e --arg n "$2" "any(${1}[]?; .name == \$n)" "$SPEC" >/dev/null
}

# Recuadro con lo que se va a ejecutar: box <título> <texto>
box() {
  printf '%s┌─ %s%s\n' "$C_BOX" "$1" "$C_RST"
  printf '%s│%s %s%s%s\n' "$C_BOX" "$C_RST" "$C_CMD" "$2" "$C_RST"
  printf '%s└─%s\n' "$C_BOX" "$C_RST"
}

# Argumentos como se teclearían, con rutas relativas a demo/.
_quote() {
  local out="" arg
  for arg in "$@"; do
    [[ "$arg" == *[[:space:]\"\'\$\|\&\;\(\)\<\>\*\?]* ]] && arg="\"${arg//\"/\\\"}\""
    out+="$arg "
  done
  out="${out% }"
  out="${out//$DEMO_DIR\//}"
  printf '%s' "${out//$DEMO_DIR/.}"
}

# Muestra el comando en un recuadro y lo ejecuta desde AlphaInvest/.
# Oculta cualquier ID de cuenta (12 dígitos) y la ruta local de demo/ en la salida, para poder proyectarla.
run() {
  box "ejecutando" "\$ $(_quote "$@")"
  (cd "$PROJECT_DIR" && "$@") 2>&1 | sed -u -E -e 's/[0-9]{12}/************/g' -e "s|${DEMO_DIR//|/\\|}/||g"
}

# Edita un JSON con jq y muestra el filtro: edit_json <archivo> <filtro>
edit_json() {
  local tmp
  box "editando" "jq '$2' ${1#"$DEMO_DIR/"}"
  tmp="$(mktemp)"
  jq "$2" "$1" > "$tmp" && mv "$tmp" "$1"
}

# Copia de agentcore.json, harness.yaml y cdk.json para mostrar el diff antes de desplegar.
track_config() {
  SNAPSHOT_DIR="$(mktemp -d)"
  trap 'rm -rf "$SNAPSHOT_DIR"' EXIT
  local f
  for f in "${CONFIG_FILES[@]}"; do cp -p "$f" "$SNAPSHOT_DIR/$(basename "$f")"; done
}

restore_config() {
  local f
  for f in "${CONFIG_FILES[@]}"; do cp -p "$SNAPSHOT_DIR/$(basename "$f")" "$f"; done
}

show_changes() {
  log "Cambios en la configuración"
  local f before after changed=0 color=never
  [[ -n "$C_RST" ]] && color=always
  for f in "${CONFIG_FILES[@]}"; do
    before="$SNAPSHOT_DIR/$(basename "$f")"
    cmp -s "$before" "$f" && continue
    after="$f"
    if [[ "$f" == *.json ]]; then
      # agentcore reescribe el JSON y reordena llaves: se compara el contenido, no el formato.
      jq -S . "$before" > "$SNAPSHOT_DIR/before.json" && jq -S . "$f" > "$SNAPSHOT_DIR/after.json"
      before="$SNAPSHOT_DIR/before.json" after="$SNAPSHOT_DIR/after.json"
      cmp -s "$before" "$after" && continue
    fi
    changed=1
    diff -u --color="$color" --label "antes    ${f#"$DEMO_DIR/"}" --label "después  ${f#"$DEMO_DIR/"}" "$before" "$after" || true
  done
  (( changed )) || echo "  Sin cambios: agentcore.json, harness.yaml y cdk.json ya están al día."
}

# DRY_RUN=1: aplica los cambios de configuración y sintetiza (agentcore build) sin desplegar.
# YES=1: despliega sin pedir confirmación.
DRY_RUN="${DRY_RUN:-0}"

deploy() {
  require_target
  [[ -n "${SNAPSHOT_DIR:-}" ]] && show_changes
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY_RUN: agentcore build (no se despliega nada)"
    run agentcore build
    return
  fi
  if [[ "${YES:-0}" != "1" ]]; then
    local answer=""
    printf '\n%s? ¿Desplegar en %s? Genera costos en AWS. [y/N] %s' "$C_ASK" "$TARGET" "$C_RST"
    read -r answer || true
    if [[ ! "$answer" =~ ^[yYsS] ]]; then
      [[ -n "${SNAPSHOT_DIR:-}" ]] && restore_config
      log "Cancelado: no se desplegó nada y la configuración quedó como estaba."
      exit 0
    fi
  fi
  log "agentcore deploy --target $TARGET"
  run agentcore deploy --target "$TARGET"
}

# Resuelve el ARN de un recurso desplegado: status_arn <runtimes|harnesses|gateways|memories> <name>
status_arn() {
  (cd "$PROJECT_DIR" && agentcore status --target "$TARGET" --json) \
    | jq -r --arg kind "$1" --arg n "$2" '
        [.. | objects | select(has("name") and .name == $n and (has("arn") or has("harnessArn") or has("runtimeArn")))]
        | first | (.arn // .harnessArn // .runtimeArn // empty)'
}
