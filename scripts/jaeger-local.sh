#!/usr/bin/env bash
# Inicia Jaeger v2 local para ver trazas OpenTelemetry del desarrollo local.
# Usa Docker si está disponible; si no, descarga el binario oficial (verificado por SHA-256).
#   scripts/jaeger-local.sh            # inicia
#   scripts/jaeger-local.sh stop       # detiene
source "$(dirname "$0")/common.sh"

VERSION="2.22.0"
BIN_DIR="$DEMO_DIR/.local/jaeger-$VERSION"
PID_FILE="$DEMO_DIR/.local/jaeger.pid"

if [[ "${1:-start}" == "stop" ]]; then
  if docker info >/dev/null 2>&1; then
    run docker compose -f "$DEMO_DIR/dev/observability/docker-compose.yml" down
  fi
  [[ -f "$PID_FILE" ]] && kill "$(cat "$PID_FILE")" 2>/dev/null && rm -f "$PID_FILE"
  log "Jaeger detenido"; exit 0
fi

if docker info >/dev/null 2>&1; then
  run docker compose -f "$DEMO_DIR/dev/observability/docker-compose.yml" up -d
else
  case "$(uname -m)" in x86_64) ARCH=amd64 ;; aarch64|arm64) ARCH=arm64 ;; *) die "Arquitectura no soportada" ;; esac
  TARBALL="jaeger-$VERSION-linux-$ARCH.tar.gz"
  if [[ ! -x "$BIN_DIR/jaeger" ]]; then
    log "Descargando $TARBALL"
    tmp="$(mktemp -d)"
    base="https://github.com/jaegertracing/jaeger/releases/download/v$VERSION"
    curl -fsSL -o "$tmp/$TARBALL" "$base/$TARBALL"
    curl -fsSL -o "$tmp/sums.txt" "$base/jaeger-$VERSION-linux-$ARCH.sha256sum.txt"
    (cd "$tmp" && grep " $TARBALL\$" sums.txt | sha256sum -c -) || die "Checksum inválido"
    mkdir -p "$BIN_DIR"
    tar -xzf "$tmp/$TARBALL" -C "$tmp"
    find "$tmp" -type f -name jaeger -perm -u+x -exec cp {} "$BIN_DIR/jaeger" \;
    rm -rf "$tmp"
  fi
  nohup "$BIN_DIR/jaeger" > "$DEMO_DIR/.local/jaeger.log" 2>&1 &
  echo $! > "$PID_FILE"
fi

for _ in $(seq 1 30); do curl -fs http://localhost:16686 >/dev/null && break; sleep 0.5; done
log "Jaeger UI: http://localhost:16686  ·  OTLP HTTP: http://localhost:4318"
