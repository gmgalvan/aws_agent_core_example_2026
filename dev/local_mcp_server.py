"""Servidor MCP local con las mismas herramientas que publica AgentCore Gateway.

Permite desarrollar el Trading Agent sin AWS: usa ``JsonFileStore`` y la
misma lógica de ``finance_core``.

    uv run python dev/local_mcp_server.py            # stdio
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "AlphaInvest" / "app" / "finance_tools"))

import finance_core  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from store import JsonFileStore  # noqa: E402

store = JsonFileStore(os.environ.get("PORTFOLIO_FILE", str(ROOT / ".local" / "portfolio.json")))
mcp = MCPServer("alphainvest-finance", log_level="WARNING")


def _safe(call):
    try:
        return call()
    except finance_core.ValidationError as error:
        return {"error": str(error)}


@mcp.tool()
def get_stock_price(symbol: str) -> dict:
    """Consulta el precio simulado y el cambio porcentual diario de AAPL, MSFT o NVDA."""
    return _safe(lambda: finance_core.get_stock_price(symbol))


@mcp.tool()
def get_portfolio() -> dict:
    """Devuelve el portafolio simulado compartido con pesos porcentuales."""
    return finance_core.get_portfolio(store)


@mcp.tool()
def simulate_trade(symbol: str, side: str, quantity: int) -> dict:
    """Ejecuta una operación de paper trading (BUY o SELL). Nunca opera dinero real."""
    return finance_core.simulate_trade(store, symbol, side, quantity)


@mcp.tool()
def get_trade_history(limit: int = 10) -> dict:
    """Lista las operaciones simuladas más recientes."""
    return {"trades": store.list_trades(max(1, min(limit, 50))), "simulated": True}


if __name__ == "__main__":
    mcp.run()
