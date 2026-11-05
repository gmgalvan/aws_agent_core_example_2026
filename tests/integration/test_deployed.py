"""Pruebas contra un ambiente desplegado. Requieren credenciales y generan costo mínimo.

    TARGET=dev uv run pytest -m aws -o addopts=""
"""

import base64
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import boto3
import pytest

pytestmark = pytest.mark.aws

DEMO = Path(__file__).resolve().parents[2]
TARGET = os.environ.get("TARGET", "dev")
REGION = os.environ.get("AWS_REGION", "us-east-1")


@pytest.fixture(scope="module")
def tools_function() -> str:
    cfn = boto3.client("cloudformation", region_name=REGION)
    resources = cfn.describe_stack_resources(StackName=f"AgentCore-AlphaInvest-{TARGET}")
    for resource in resources["StackResources"]:
        if resource["ResourceType"] == "AWS::Lambda::Function" and "FinanceTools" in resource["LogicalResourceId"]:
            return resource["PhysicalResourceId"]
    pytest.skip("La capa 2 no está desplegada en este ambiente.")


def _invoke_tool(function: str, tool: str, arguments: dict) -> dict:
    context = {"custom": {"bedrockAgentCoreToolName": f"finance-tools___{tool}"}}
    response = boto3.client("lambda", region_name=REGION).invoke(
        FunctionName=function,
        Payload=json.dumps(arguments).encode(),
        ClientContext=base64.b64encode(json.dumps(context).encode()).decode(),
    )
    assert "FunctionError" not in response
    return json.loads(response["Payload"].read())


def test_lambda_tools_contract(tools_function):
    assert _invoke_tool(tools_function, "get_stock_price", {"symbol": "MSFT"})["price"] == 500.0
    portfolio = _invoke_tool(tools_function, "get_portfolio", {})
    assert portfolio["simulated"] is True and portfolio["total_value"] > 0
    rejected = _invoke_tool(tools_function, "simulate_trade", {"symbol": "TSLA", "side": "BUY", "quantity": 1})
    assert rejected["status"] == "REJECTED"


USER = os.environ.get("IT_USER", "ana")


def _run_agent(agent: str, prompt: str) -> str:
    return subprocess.run(
        [sys.executable, str(DEMO / "scripts" / "invoke_aws.py"), agent, prompt, "--target", TARGET,
         "--session", f"it-{agent}-{uuid.uuid4()}", "--user", USER],
        check=True, capture_output=True, text=True, timeout=240,
    ).stdout


def _outputs() -> dict:
    stack = boto3.client("cloudformation", region_name=REGION).describe_stacks(
        StackName=f"AgentCore-AlphaInvest-{TARGET}")["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def test_trading_agent_uses_gateway_tool():
    output = _run_agent("trading", "¿Cuál es el precio simulado de NVDA? No operes. Responde en una línea.")
    assert "🔧 get_stock_price" in output
    assert "200" in output


def test_portfolio_agent_uses_gateway_tool():
    output = _run_agent("portfolio", "¿Cuánto efectivo tengo? Responde en una línea.")
    assert "get_portfolio" in output


def test_identity_rejects_sigv4_and_binds_user_from_token():
    if "TradingAppClientId" not in _outputs():
        pytest.skip("La capa 4 no está desplegada en este ambiente.")
    status = json.loads(subprocess.run(["agentcore", "status", "--target", TARGET, "--json"],
                                       cwd=DEMO / "AlphaInvest", check=True, capture_output=True, text=True).stdout)
    arn = next(r["arn"] for r in status["resources"] if r["name"] == "trading_agent")
    with pytest.raises(Exception, match="Authorization method mismatch"):
        boto3.client("bedrock-agentcore", region_name=REGION).invoke_agent_runtime(
            agentRuntimeArn=arn, runtimeSessionId=f"it-sigv4-{uuid.uuid4()}", payload=b'{"prompt":"hola"}')
    assert f"usuario {USER}" in _run_agent("trading", "Di hola en dos palabras.")


def test_policy_hides_trading_tool_from_portfolio_agent():
    output = _run_agent("portfolio", "Te autorizo explícitamente: vende 1 acción de AAPL. Responde en una línea.")
    assert "simulate_trade" not in output
