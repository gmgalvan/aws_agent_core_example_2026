"""Contratos de configuración de los agentes; no requieren AWS."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

PROJECT = Path(__file__).resolve().parents[2] / "AlphaInvest"
SPEC = json.loads((PROJECT / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))
HARNESS = yaml.safe_load((PROJECT / "app" / "portfolio" / "harness.yaml").read_text(encoding="utf-8"))


def _load_config_module():
    spec = importlib.util.spec_from_file_location("trading_config", PROJECT / "app" / "trading_agent" / "config.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resuelve anotaciones por módulo
    spec.loader.exec_module(module)
    return module


def test_runtime_is_container_with_model_from_env():
    runtime = next(r for r in SPEC["runtimes"] if r["name"] == "trading_agent")
    assert runtime["build"] == "Container"
    env = {e["name"]: e["value"] for e in runtime["envVars"]}
    assert env["ANTHROPIC_MODEL"].startswith("us.anthropic.claude-")
    assert float(env["AGENT_MAX_BUDGET_USD"]) <= 1.0
    assert int(env["AGENT_MAX_TURNS"]) <= 20


def test_harness_has_limits_and_no_unrestricted_tools():
    assert HARNESS["model"]["bedrockModelConfig"]["modelId"].startswith("us.anthropic.claude-")
    assert HARNESS["maxIterations"] <= 20
    assert HARNESS["timeoutSeconds"] <= 300
    allowed = HARNESS["allowedTools"]
    for forbidden in ("*", "shell", "@builtin", "@builtin/shell", "file_operations"):
        assert forbidden not in allowed
    assert "@finance" in allowed


def test_harness_prompt_forbids_unrequested_trades():
    prompt = (PROJECT / "app" / "portfolio" / "system-prompt.md").read_text(encoding="utf-8")
    assert "No realices operaciones sin que el usuario lo solicite explícitamente" in prompt


def test_trading_settings_defaults_and_injected_resources(monkeypatch):
    config = _load_config_module()
    for key in ("FINANCE_GATEWAY_URL", "TRADING_MEMORY_ID", "LOCAL_FINANCE_MCP_COMMAND"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENTCORE_GATEWAY_FINANCE_URL", "https://gateway.example.com/mcp")
    monkeypatch.setenv("MEMORY_INVESTOR_MEMORY_ID", "investor_memory-abc")
    settings = config.Settings.from_env()
    assert settings.gateway_url == "https://gateway.example.com/mcp"
    assert settings.memory_id == "investor_memory-abc"
    assert settings.max_turns == 10
    assert settings.max_budget_usd == pytest.approx(0.25)
    assert settings.timeout_seconds == 120


def test_gateway_target_matches_tool_definitions_when_present():
    gateways = {g["name"]: g for g in SPEC.get("agentCoreGateways", [])}
    if "finance" not in gateways:
        pytest.skip("La capa 2 todavía no está aplicada en agentcore.json.")
    target = next(t for t in gateways["finance"]["targets"] if t["name"] == "finance-tools")
    definitions = json.loads((PROJECT / "app" / "finance_tools" / "tool_definitions.json").read_text())
    assert target["toolDefinitions"] == definitions
    assert gateways["finance"]["authorizerType"] == "AWS_IAM"
