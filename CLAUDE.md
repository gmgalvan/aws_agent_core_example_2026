# CLAUDE.md

AlphaInvest AI: demo por capas de Amazon Bedrock AgentCore para la charla «Crear un agente de IA es solo el principio». Arquitectura, capas, ambientes, costos y limpieza están en [`README.md`](README.md); léelo antes de cambiar algo.

## Estructura

- `AlphaInvest/`: proyecto del AgentCore CLI.
  - `agentcore/agentcore.json` y `agentcore/cdk/`: configuración e infraestructura (AWS CDK, TypeScript).
  - `app/trading_agent/`: Trading Agent en AgentCore Runtime (Claude Agent SDK).
  - `app/portfolio/`: Portfolio Agent en AgentCore Harness (`harness.yaml`).
  - `app/finance_tools/`: Lambda detrás de AgentCore Gateway y la terminal ficticia AlphaTrader Web.
  - `app/policies/`: políticas Cedar de AgentCore Policy.
- `layers/`: un script idempotente por capa (1–9) que modifica la configuración y despliega.
- `scripts/`: utilidades (`common.sh`, invocación, limpieza, Jaeger, grabaciones de Browser).
- `dev/`: servidor MCP local y Jaeger para desarrollo sin AWS.
- `tests/unit/` (sin AWS) y `tests/integration/` (marcadas `aws`).

## Comandos (desde la raíz)

```bash
uv sync
uv run pytest                                   # pruebas unitarias, sin AWS
scripts/test.sh                                 # unitarias + agentcore build
TARGET=dev uv run pytest -m aws -o addopts=""   # integración; requiere un ambiente desplegado
DRY_RUN=1 TARGET=dev layers/<n>-<capa>.sh       # aplica la configuración de una capa sin desplegar
scripts/reset-config.sh                         # devuelve la configuración local a la capa 1
```

## Reglas

- **No despliegues ni borres recursos de AWS sin confirmación explícita.** `agentcore deploy`, los scripts de `layers/` sin `DRY_RUN=1`, `scripts/cleanup.sh` y `scripts/browser-sessions.sh` generan costos o eliminan recursos.
- No muestres IDs de cuenta completos, ARNs reales, URLs de Lambda ni contraseñas de `.local/`: la salida puede proyectarse en la charla. Usa `123456789012` o variables de entorno en ejemplos.
- No versiones `agentcore/aws-targets.json`, `agentcore/.cli/` ni `.local/` (ver `.gitignore`).
- Si una capa cambia, actualiza `README.md` y las pruebas de `tests/unit/test_agent_config.py`, y corre `uv run pytest`.
- Aplica privilegio mínimo en IAM; si simplificas un permiso con fines didácticos, señálalo en la sección «Seguridad» del README.
- Contenido en español; nombres de productos, APIs y comandos en inglés. Verifica nombres y parámetros de AgentCore en la [documentación oficial](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html); no presentes una capacidad en preview como disponible en general.
