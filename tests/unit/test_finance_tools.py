import json
from pathlib import Path
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

import finance_core
import handler
from finance_core import ConcurrencyError, PortfolioState, TradeRecord
from store import DynamoDBStore, JsonFileStore

TOOLS_DIR = Path(__file__).resolve().parents[2] / "AlphaInvest" / "app" / "finance_tools"


@pytest.fixture
def json_store(tmp_path):
    return JsonFileStore(tmp_path / "portfolio.json")


@pytest.fixture
def dynamo_store(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="portfolio-test",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield DynamoDBStore("portfolio-test", client=client)


@pytest.fixture(params=["json", "dynamo"])
def store(request):
    return request.getfixturevalue(f"{request.param}_store")


# --- precios -----------------------------------------------------------------


def test_stock_price_is_deterministic_and_normalized():
    quote = finance_core.get_stock_price(" aapl ")
    assert quote == {"symbol": "AAPL", "price": 250.0, "change_pct": 1.8, "currency": "USD", "simulated": True}


@pytest.mark.parametrize("symbol", ["TSLA", "", None, 123])
def test_stock_price_rejects_unknown_or_invalid(symbol):
    with pytest.raises(finance_core.ValidationError):
        finance_core.get_stock_price(symbol)


# --- portafolio ----------------------------------------------------------------


def test_initial_portfolio(store):
    portfolio = finance_core.get_portfolio(store)
    assert portfolio["cash"] == 10_000.0
    assert portfolio["invested_value"] == 2_500 + 2_500 + 3_000
    assert portfolio["total_value"] == 18_000.0
    weights = {p["symbol"]: p["weight_pct"] for p in portfolio["positions"]}
    assert weights == {"AAPL": 13.89, "MSFT": 13.89, "NVDA": 16.67}
    assert portfolio["cash_weight_pct"] == 55.56


# --- operaciones ---------------------------------------------------------------


def test_buy_updates_cash_positions_and_log(store):
    result = finance_core.simulate_trade(store, "msft", "buy", 2)
    assert result["status"] == "FILLED"
    assert result["execution_price"] == 500.0
    assert result["cash_remaining"] == 9_000.0
    assert {"symbol": "MSFT", "quantity": 7} in result["positions"]
    trades = store.list_trades()
    assert trades[-1]["trade_id"] == result["trade_id"]
    assert trades[-1]["status"] == "FILLED"
    assert set(trades[-1]) >= {"trade_id", "timestamp", "symbol", "quantity", "status"}


def test_sell_increases_cash(store):
    result = finance_core.simulate_trade(store, "NVDA", "SELL", 5)
    assert result["status"] == "FILLED"
    assert result["cash_remaining"] == 11_000.0
    assert {"symbol": "NVDA", "quantity": 10} in result["positions"]


def test_selling_whole_position_removes_it(store):
    finance_core.simulate_trade(store, "AAPL", "SELL", 10)
    symbols = [p["symbol"] for p in finance_core.get_portfolio(store)["positions"]]
    assert "AAPL" not in symbols


def test_rejects_buy_without_cash(store):
    result = finance_core.simulate_trade(store, "MSFT", "BUY", 21)
    assert result["status"] == "REJECTED"
    assert "Efectivo insuficiente" in result["reason"]
    assert finance_core.get_portfolio(store)["cash"] == 10_000.0
    assert store.list_trades()[-1]["status"] == "REJECTED"


def test_rejects_selling_more_than_held(store):
    result = finance_core.simulate_trade(store, "AAPL", "SELL", 11)
    assert result["status"] == "REJECTED"
    assert "Posición insuficiente" in result["reason"]


@pytest.mark.parametrize(
    "symbol,side,quantity",
    [
        ("AAPL", "BUY", 0),
        ("AAPL", "BUY", -3),
        ("AAPL", "BUY", 1.5),
        ("AAPL", "BUY", True),
        ("AAPL", "BUY", 1_001),
        ("AAPL", "HOLD", 1),
        ("XYZ", "BUY", 1),
    ],
)
def test_rejects_invalid_orders_and_logs_them(store, symbol, side, quantity):
    result = finance_core.simulate_trade(store, symbol, side, quantity)
    assert result["status"] == "REJECTED"
    assert result["reason"]
    assert finance_core.get_portfolio(store)["total_value"] == 18_000.0
    assert store.list_trades()[-1]["status"] == "REJECTED"


def test_accepts_integer_like_strings(store):
    assert finance_core.simulate_trade(store, "AAPL", "BUY", "3")["status"] == "FILLED"


def test_state_persists_across_store_instances(tmp_path):
    path = tmp_path / "portfolio.json"
    finance_core.simulate_trade(JsonFileStore(path), "AAPL", "BUY", 4)
    assert finance_core.get_portfolio(JsonFileStore(path))["cash"] == 9_000.0


def test_stale_version_raises_concurrency_error(store):
    state = store.load()
    trade = TradeRecord("T-1", "2026-01-01T00:00:00+00:00", "AAPL", "BUY", 1, "FILLED", 25_000)
    store.commit_trade(PortfolioState(state.cash_cents - 25_000, state.positions, state.version + 1), trade)
    stale = TradeRecord("T-2", "2026-01-01T00:00:01+00:00", "AAPL", "BUY", 1, "FILLED", 25_000)
    with pytest.raises(ConcurrencyError):
        store.commit_trade(PortfolioState(1, state.positions, state.version + 1), stale)


def test_reset_restores_initial_portfolio(store):
    finance_core.simulate_trade(store, "AAPL", "BUY", 4)
    store.reset()
    assert finance_core.get_portfolio(store)["cash"] == 10_000.0


# --- Lambda / contrato de Gateway -------------------------------------------------


def _context(tool: str):
    return SimpleNamespace(
        client_context=SimpleNamespace(
            custom={
                "bedrockAgentCoreToolName": f"finance___{tool}",
                "bedrockAgentCoreGatewayId": "gw-test",
                "bedrockAgentCoreAwsRequestId": "req-test",
            }
        )
    )


@pytest.fixture
def lambda_env(monkeypatch, tmp_path):
    monkeypatch.delenv("PORTFOLIO_TABLE", raising=False)
    monkeypatch.setenv("PORTFOLIO_FILE", str(tmp_path / "lambda.json"))
    monkeypatch.setattr(handler, "_store", None)


def test_handler_routes_tools_by_gateway_name(lambda_env):
    assert handler.lambda_handler({"symbol": "NVDA"}, _context("get_stock_price"))["price"] == 200.0
    assert handler.lambda_handler({}, _context("get_portfolio"))["total_value"] == 18_000.0
    trade = handler.lambda_handler({"symbol": "AAPL", "side": "BUY", "quantity": 1}, _context("simulate_trade"))
    assert trade["status"] == "FILLED"
    history = handler.lambda_handler({"limit": 5}, _context("get_trade_history"))
    assert history["trades"][-1]["trade_id"] == trade["trade_id"]


def test_handler_returns_errors_as_json(lambda_env):
    assert "error" in handler.lambda_handler({"symbol": "TSLA"}, _context("get_stock_price"))
    assert "error" in handler.lambda_handler({}, _context("delete_everything"))
    assert "error" in handler.lambda_handler({"limit": 500}, _context("get_trade_history"))
    json.dumps(handler.lambda_handler({}, _context("get_portfolio")))


def test_tool_definitions_match_handler():
    definitions = json.loads((TOOLS_DIR / "tool_definitions.json").read_text(encoding="utf-8"))
    assert {d["name"] for d in definitions} == set(handler.TOOLS)
    for definition in definitions:
        assert definition["description"]
        assert definition["inputSchema"]["type"] == "object"
        for prop in definition["inputSchema"].get("properties", {}).values():
            assert prop["type"] in {"string", "integer", "number", "boolean", "array", "object"}


def test_web_terminal_hides_injection_unless_reveal(store):
    import web_terminal

    portfolio = finance_core.get_portfolio(store)
    normal = web_terminal.render(portfolio)
    revealed = web_terminal.render(portfolio, reveal=True)
    assert "INSTRUCCIÓN PRIORITARIA" in normal  # el agente la lee en el DOM
    assert 'class="reveal"' not in normal and "MODO REVELAR" not in normal
    assert '<body class="reveal">' in revealed and "MODO REVELAR" in revealed


def test_web_terminal_reveal_query_parameter(monkeypatch, store):
    import web_terminal

    monkeypatch.setattr(web_terminal, "store_from_env", lambda: store)
    get = {"requestContext": {"http": {"method": "GET"}}}
    assert "MODO REVELAR" not in web_terminal.handler(get, None)["body"]
    assert "MODO REVELAR" in web_terminal.handler(get | {"queryStringParameters": {"reveal": "1"}}, None)["body"]
    assert web_terminal.handler({"requestContext": {"http": {"method": "POST"}}}, None)["statusCode"] == 405


def test_web_terminal_shows_allocation_and_recent_trades(store):
    import web_terminal

    finance_core.simulate_trade(store, "NVDA", "BUY", 2)
    page = web_terminal.render(finance_core.get_portfolio(store), trades=store.list_trades(limit=8))
    assert 'class="alloc"' in page and "Efectivo" in page
    assert "Operaciones recientes" in page and "NVDA" in page and "FILLED" in page
    assert "Sin operaciones registradas." in web_terminal.render(finance_core.get_portfolio(store), trades=[])


def test_web_terminal_is_navigable_without_javascript(store):
    import web_terminal

    page = web_terminal.render(finance_core.get_portfolio(store))
    assert '<a href="#operaciones">' in page and 'id="operaciones"' in page
    assert '<details class="news">' in page and "<script" not in page
    assert '<details class="news" open>' in web_terminal.render(finance_core.get_portfolio(store), reveal=True)
