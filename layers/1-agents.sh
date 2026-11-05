#!/usr/bin/env bash
# Capa 1 — Los agentes: Trading Agent (Runtime + Claude Agent SDK) y
# Portfolio Agent (Harness). Sin herramientas todavía: el agente reconoce que
# no tiene datos. Observabilidad básica (ADOT -> CloudWatch) ya activa.
source "$(dirname "$0")/../scripts/common.sh"
track_config
require agentcore jq uv

has '.runtimes' trading_agent || die "Falta el Runtime trading_agent en agentcore.json."
has '.harnesses' portfolio || die "Falta el Harness portfolio en agentcore.json."

log "Verificando lockfile del Trading Agent"
run uv --directory app/trading_agent lock --check

deploy
