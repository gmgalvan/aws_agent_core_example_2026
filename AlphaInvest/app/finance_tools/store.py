"""Implementaciones de ``PortfolioStore``.

- ``JsonFileStore``: desarrollo local y pruebas; un archivo JSON.
- ``DynamoDBStore``: AWS; estado compartido entre agentes y sesiones, con
  bloqueo optimista y escritura transaccional de estado + bitácora.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from finance_core import ConcurrencyError, PortfolioState, TradeRecord

_STATE_SK = "STATE"


class JsonFileStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            initial = PortfolioState.initial()
            return {
                "state": {"cash_cents": initial.cash_cents, "positions": initial.positions, "version": 0},
                "trades": [],
            }
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def load(self) -> PortfolioState:
        state = self._read()["state"]
        return PortfolioState(
            cash_cents=int(state["cash_cents"]),
            positions={k: int(v) for k, v in state["positions"].items()},
            version=int(state["version"]),
        )

    def commit_trade(self, new_state: PortfolioState, trade: TradeRecord) -> None:
        with self._lock:
            data = self._read()
            if int(data["state"]["version"]) != new_state.version - 1:
                raise ConcurrencyError("El portafolio cambió.")
            data["state"] = {
                "cash_cents": new_state.cash_cents,
                "positions": new_state.positions,
                "version": new_state.version,
            }
            data["trades"].append(trade.to_dict())
            self._write(data)

    def record_rejection(self, trade: TradeRecord) -> None:
        with self._lock:
            data = self._read()
            data["trades"].append(trade.to_dict())
            self._write(data)

    def list_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._read()["trades"][-limit:]

    def reset(self) -> None:
        with self._lock:
            if self.path.exists():
                self.path.unlink()


class DynamoDBStore:
    """Tabla con clave ``pk`` + ``sk``.

    - ``pk=PORTFOLIO#<id>, sk=STATE``: efectivo, posiciones y versión.
    - ``pk=PORTFOLIO#<id>, sk=TRADE#<timestamp>#<trade_id>``: bitácora.
    """

    def __init__(self, table_name: str, portfolio_id: str = "demo", client: Any = None):
        if client is None:
            import boto3

            client = boto3.client("dynamodb")
        self.client = client
        self.table = table_name
        self.pk = f"PORTFOLIO#{portfolio_id}"

    @staticmethod
    def _serialize_positions(positions: dict[str, int]) -> dict[str, Any]:
        return {"M": {k: {"N": str(v)} for k, v in positions.items()}}

    def _state_item(self, state: PortfolioState) -> dict[str, Any]:
        return {
            "pk": {"S": self.pk},
            "sk": {"S": _STATE_SK},
            "cash_cents": {"N": str(state.cash_cents)},
            "positions": self._serialize_positions(state.positions),
            "version": {"N": str(state.version)},
        }

    def _trade_item(self, trade: TradeRecord) -> dict[str, Any]:
        return {
            "pk": {"S": self.pk},
            "sk": {"S": f"TRADE#{trade.timestamp}#{trade.trade_id}"},
            "record": {"S": json.dumps(trade.to_dict(), ensure_ascii=False)},
        }

    def load(self) -> PortfolioState:
        response = self.client.get_item(
            TableName=self.table,
            Key={"pk": {"S": self.pk}, "sk": {"S": _STATE_SK}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            initial = PortfolioState.initial()
            try:
                self.client.put_item(
                    TableName=self.table,
                    Item=self._state_item(initial),
                    ConditionExpression="attribute_not_exists(pk)",
                )
            except self.client.exceptions.ConditionalCheckFailedException:
                return self.load()
            return initial
        return PortfolioState(
            cash_cents=int(item["cash_cents"]["N"]),
            positions={k: int(v["N"]) for k, v in item["positions"]["M"].items()},
            version=int(item["version"]["N"]),
        )

    def commit_trade(self, new_state: PortfolioState, trade: TradeRecord) -> None:
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table,
                            "Item": self._state_item(new_state),
                            "ConditionExpression": "version = :expected",
                            "ExpressionAttributeValues": {":expected": {"N": str(new_state.version - 1)}},
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table,
                            "Item": self._trade_item(trade),
                            "ConditionExpression": "attribute_not_exists(sk)",
                        }
                    },
                ]
            )
        except self.client.exceptions.TransactionCanceledException as error:
            raise ConcurrencyError("El portafolio cambió.") from error

    def record_rejection(self, trade: TradeRecord) -> None:
        self.client.put_item(TableName=self.table, Item=self._trade_item(trade))

    def list_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        response = self.client.query(
            TableName=self.table,
            KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
            ExpressionAttributeValues={":pk": {"S": self.pk}, ":prefix": {"S": "TRADE#"}},
            ScanIndexForward=False,
            Limit=limit,
        )
        return [json.loads(item["record"]["S"]) for item in response.get("Items", [])][::-1]

    def reset(self) -> None:
        self.client.delete_item(TableName=self.table, Key={"pk": {"S": self.pk}, "sk": {"S": _STATE_SK}})


def store_from_env() -> JsonFileStore | DynamoDBStore:
    """Elige el almacenamiento con variables de entorno."""

    table = os.environ.get("PORTFOLIO_TABLE")
    portfolio_id = os.environ.get("PORTFOLIO_ID", "demo")
    if table:
        return DynamoDBStore(table, portfolio_id)
    return JsonFileStore(os.environ.get("PORTFOLIO_FILE", "portfolio.json"))
