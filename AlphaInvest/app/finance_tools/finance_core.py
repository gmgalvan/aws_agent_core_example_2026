"""Lógica de negocio del servicio financiero simulado.

Todo el dinero se maneja en centavos enteros para evitar errores de punto
flotante. Este módulo no conoce AWS ni MCP: recibe un ``PortfolioStore`` y
devuelve diccionarios serializables a JSON.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

CURRENCY = "USD"
MAX_ORDER_QUANTITY = 1_000

# Datos de mercado ficticios y deterministas para que la demo sea repetible.
MARKET_DATA: dict[str, dict[str, int | float]] = {
    "AAPL": {"price_cents": 25_000, "change_pct": 1.8},
    "MSFT": {"price_cents": 50_000, "change_pct": -0.6},
    "NVDA": {"price_cents": 20_000, "change_pct": 3.2},
}

INITIAL_POSITIONS = {"AAPL": 10, "MSFT": 5, "NVDA": 15}
INITIAL_CASH_CENTS = 1_000_000


class ValidationError(ValueError):
    """Entrada inválida enviada por el agente."""


class ConcurrencyError(RuntimeError):
    """El portafolio cambió entre la lectura y la escritura."""


@dataclass
class PortfolioState:
    cash_cents: int
    positions: dict[str, int]
    version: int = 0

    @classmethod
    def initial(cls) -> "PortfolioState":
        return cls(cash_cents=INITIAL_CASH_CENTS, positions=dict(INITIAL_POSITIONS))


@dataclass
class TradeRecord:
    trade_id: str
    timestamp: str
    symbol: str
    side: str
    quantity: int
    status: str
    execution_price_cents: int | None = None
    reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "trade_id": self.trade_id,
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "status": self.status,
        }
        if self.execution_price_cents is not None:
            data["execution_price"] = cents_to_usd(self.execution_price_cents)
        if self.reason:
            data["reason"] = self.reason
        return data


class PortfolioStore(Protocol):
    """Almacenamiento intercambiable: JSON local o DynamoDB en AWS."""

    def load(self) -> PortfolioState: ...

    def commit_trade(self, new_state: PortfolioState, trade: TradeRecord) -> None:
        """Guarda el nuevo estado y el registro de la operación de forma atómica.

        Debe lanzar ``ConcurrencyError`` si ``new_state.version - 1`` ya no es
        la versión almacenada.
        """
        ...

    def record_rejection(self, trade: TradeRecord) -> None: ...

    def list_trades(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def reset(self) -> None: ...


def cents_to_usd(cents: int) -> float:
    return round(cents / 100, 2)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_symbol(symbol: Any) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValidationError("symbol debe ser un texto no vacío, por ejemplo 'AAPL'.")
    normalized = symbol.strip().upper()
    if normalized not in MARKET_DATA:
        raise ValidationError(
            f"Símbolo desconocido: {normalized}. Disponibles: {', '.join(sorted(MARKET_DATA))}."
        )
    return normalized


def normalize_side(side: Any) -> str:
    if not isinstance(side, str) or side.strip().upper() not in {"BUY", "SELL"}:
        raise ValidationError("side debe ser BUY o SELL.")
    return side.strip().upper()


def normalize_quantity(quantity: Any) -> int:
    # bool es subclase de int: se rechaza explícitamente.
    if isinstance(quantity, bool):
        raise ValidationError("quantity debe ser un entero positivo.")
    if isinstance(quantity, str) and quantity.strip().isdigit():
        quantity = int(quantity.strip())
    if isinstance(quantity, float) and quantity.is_integer():
        quantity = int(quantity)
    if not isinstance(quantity, int) or quantity <= 0:
        raise ValidationError("quantity debe ser un entero positivo.")
    if quantity > MAX_ORDER_QUANTITY:
        raise ValidationError(f"quantity excede el máximo por orden ({MAX_ORDER_QUANTITY}).")
    return quantity


def get_stock_price(symbol: Any) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    quote = MARKET_DATA[normalized]
    return {
        "symbol": normalized,
        "price": cents_to_usd(int(quote["price_cents"])),
        "change_pct": quote["change_pct"],
        "currency": CURRENCY,
        "simulated": True,
    }


def _positions_view(positions: dict[str, int]) -> tuple[list[dict[str, Any]], int]:
    rows = []
    invested_cents = 0
    for symbol in sorted(positions):
        quantity = positions[symbol]
        if quantity <= 0:
            continue
        price_cents = int(MARKET_DATA[symbol]["price_cents"])
        value_cents = price_cents * quantity
        invested_cents += value_cents
        rows.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "price": cents_to_usd(price_cents),
                "market_value": cents_to_usd(value_cents),
            }
        )
    return rows, invested_cents


def get_portfolio(store: PortfolioStore) -> dict[str, Any]:
    state = store.load()
    positions, invested_cents = _positions_view(state.positions)
    total_cents = invested_cents + state.cash_cents
    for row in positions:
        row["weight_pct"] = round(row["market_value"] * 100 / cents_to_usd(total_cents), 2)
    return {
        "positions": positions,
        "cash": cents_to_usd(state.cash_cents),
        "cash_weight_pct": round(state.cash_cents * 100 / total_cents, 2),
        "invested_value": cents_to_usd(invested_cents),
        "total_value": cents_to_usd(total_cents),
        "currency": CURRENCY,
        "simulated": True,
    }


def simulate_trade(
    store: PortfolioStore, symbol: Any, side: Any, quantity: Any, max_retries: int = 3
) -> dict[str, Any]:
    """Ejecuta una operación de paper trading con validación de saldo y posiciones."""

    trade_id = f"T-{uuid.uuid4().hex[:12]}"
    try:
        symbol_n = normalize_symbol(symbol)
        side_n = normalize_side(side)
        quantity_n = normalize_quantity(quantity)
    except ValidationError as error:
        rejected = TradeRecord(
            trade_id=trade_id,
            timestamp=_now(),
            symbol=str(symbol)[:16],
            side=str(side)[:8],
            quantity=quantity if isinstance(quantity, int) and not isinstance(quantity, bool) else 0,
            status="REJECTED",
            reason=str(error),
        )
        store.record_rejection(rejected)
        return rejected.to_dict()

    price_cents = int(MARKET_DATA[symbol_n]["price_cents"])
    notional_cents = price_cents * quantity_n

    for _ in range(max_retries):
        state = store.load()
        positions = dict(state.positions)
        held = positions.get(symbol_n, 0)
        reason = None
        if side_n == "BUY" and notional_cents > state.cash_cents:
            reason = (
                f"Efectivo insuficiente: se requieren {cents_to_usd(notional_cents)} {CURRENCY} "
                f"y hay {cents_to_usd(state.cash_cents)} {CURRENCY}."
            )
        if side_n == "SELL" and quantity_n > held:
            reason = f"Posición insuficiente: intentas vender {quantity_n} {symbol_n} y tienes {held}."

        trade = TradeRecord(
            trade_id=trade_id,
            timestamp=_now(),
            symbol=symbol_n,
            side=side_n,
            quantity=quantity_n,
            status="REJECTED" if reason else "FILLED",
            execution_price_cents=None if reason else price_cents,
            reason=reason,
        )
        if reason:
            store.record_rejection(trade)
            return trade.to_dict()

        if side_n == "BUY":
            positions[symbol_n] = held + quantity_n
            cash = state.cash_cents - notional_cents
        else:
            positions[symbol_n] = held - quantity_n
            cash = state.cash_cents + notional_cents
        new_state = PortfolioState(cash_cents=cash, positions=positions, version=state.version + 1)
        try:
            store.commit_trade(new_state, trade)
        except ConcurrencyError:
            continue

        result = trade.to_dict()
        rows, _ = _positions_view(new_state.positions)
        result.update(
            {
                "notional": cents_to_usd(notional_cents),
                "cash_remaining": cents_to_usd(new_state.cash_cents),
                "positions": [{"symbol": r["symbol"], "quantity": r["quantity"]} for r in rows],
                "currency": CURRENCY,
                "simulated": True,
            }
        )
        return result

    raise ConcurrencyError("No se pudo registrar la operación por escrituras concurrentes.")
