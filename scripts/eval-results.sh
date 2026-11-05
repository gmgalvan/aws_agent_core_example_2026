#!/usr/bin/env bash
# Resume los resultados de evaluación en línea (capa 8) de ambos agentes.
#   scripts/eval-results.sh [ventana]      # por defecto 2h; p. ej. 30m, 1d
# Las sesiones se evalúan ~5 minutos después de quedar inactivas.
source "$(dirname "$0")/common.sh"
require agentcore jq

SINCE="${1:-2h}"
cd "$PROJECT_DIR"
log "Configuraciones de evaluación en línea del ambiente $TARGET"
box "ejecutando" "\$ agentcore eval online-eval list --json"
for id in $(agentcore eval online-eval list --json | jq -r --arg t "_${TARGET}_" \
    '.onlineEvaluationConfigs[] | select(.onlineEvaluationConfigName | contains($t)) | .onlineEvaluationConfigId'); do
  log "${id%-*}"
  box "ejecutando" "\$ agentcore eval online-eval logs --id $id --since $SINCE --limit 200 --json"
  agentcore eval online-eval logs --id "$id" --since "$SINCE" --limit 200 --json 2>/dev/null \
    | jq -r '.message | fromjson | .attributes
        | select(.["gen_ai.evaluation.name"] != null)
        | [.["gen_ai.evaluation.name"], (.["session.id"] // "")[0:28], (.["gen_ai.evaluation.score.value"]|tostring),
           (.["gen_ai.evaluation.score.label"] // ""), ((.["gen_ai.evaluation.explanation"] // "")[0:90])] | @tsv' \
    | column -t -s $'\t' || true
done
