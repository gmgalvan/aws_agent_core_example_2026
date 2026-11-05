"""Lambda que AgentCore Gateway expone como herramientas MCP.

Gateway envía los argumentos de la herramienta como ``event`` y el nombre en
``context.client_context.custom['bedrockAgentCoreToolName']`` con el formato
``<target>___<tool>``.
https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-lambda.html
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable

import finance_core
from store import store_from_env

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_DELIMITER = "___"
_store = None


def _get_store():
    global _store
    if _store is None:
        _store = store_from_env()
    return _store


def _history(event: dict[str, Any]) -> dict[str, Any]:
    limit = event.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise finance_core.ValidationError("limit debe ser un entero entre 1 y 50.")
    return {"trades": _get_store().list_trades(limit), "simulated": True}


TOOLS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "get_stock_price": lambda e: finance_core.get_stock_price(e.get("symbol")),
    "get_portfolio": lambda e: finance_core.get_portfolio(_get_store()),
    "simulate_trade": lambda e: finance_core.simulate_trade(
        _get_store(), e.get("symbol"), e.get("side"), e.get("quantity")
    ),
    "get_trade_history": _history,
}


def _tool_name(context: Any) -> tuple[str, dict[str, Any]]:
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    full_name = custom.get("bedrockAgentCoreToolName", "")
    name = full_name.split(_DELIMITER, 1)[1] if _DELIMITER in full_name else full_name
    metadata = {
        "gateway_id": custom.get("bedrockAgentCoreGatewayId"),
        "gateway_request_id": custom.get("bedrockAgentCoreAwsRequestId"),
    }
    return name, metadata


def lambda_handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    started = time.perf_counter()
    event = event or {}
    tool, metadata = _tool_name(context)
    outcome = "ok"
    try:
        handler = TOOLS.get(tool)
        if handler is None:
            outcome = "unknown_tool"
            return {"error": f"Herramienta desconocida: {tool or '(vacía)'}."}
        result = handler(event)
        if result.get("status") == "REJECTED":
            outcome = "rejected"
        return result
    except finance_core.ValidationError as error:
        outcome = "validation_error"
        return {"error": str(error)}
    except Exception:
        outcome = "internal_error"
        logger.exception("Error inesperado en la herramienta %s", tool)
        return {"error": "Error interno del servicio financiero simulado."}
    finally:
        # Sin argumentos completos ni credenciales en los logs.
        logger.info(
            json.dumps(
                {
                    "event": "tool_invocation",
                    "tool": tool,
                    "outcome": outcome,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    **metadata,
                }
            )
        )
