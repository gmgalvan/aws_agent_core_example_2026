"""Configuración por variables de entorno; nada específico de una cuenta en el código."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _first_env(prefix: str, suffix: str) -> str | None:
    """Busca variables inyectadas por AgentCore, p. ej. AGENTCORE_GATEWAY_<NAME>_URL."""

    for key, value in os.environ.items():
        if key.startswith(prefix) and key.endswith(suffix) and value:
            return value
    return None


@dataclass(frozen=True)
class Settings:
    model_id: str
    region: str
    gateway_url: str | None
    memory_id: str | None
    local_mcp_command: str | None
    mcp_proxy_command: str
    sessions_dir: str
    max_turns: int
    max_budget_usd: float
    timeout_seconds: int
    max_prompt_chars: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            model_id=os.environ.get("ANTHROPIC_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
            region=os.environ.get("AWS_REGION", "us-east-1"),
            gateway_url=os.environ.get("FINANCE_GATEWAY_URL") or _first_env("AGENTCORE_GATEWAY_", "_URL"),
            memory_id=os.environ.get("TRADING_MEMORY_ID") or _first_env("MEMORY_", "_ID"),
            local_mcp_command=os.environ.get("LOCAL_FINANCE_MCP_COMMAND"),
            mcp_proxy_command=os.environ.get("MCP_PROXY_COMMAND", "mcp-proxy-for-aws-cli"),
            sessions_dir=os.environ.get("SESSIONS_DIR", "/tmp/trading-sessions"),
            max_turns=int(os.environ.get("AGENT_MAX_TURNS", "10")),
            max_budget_usd=float(os.environ.get("AGENT_MAX_BUDGET_USD", "0.25")),
            timeout_seconds=int(os.environ.get("AGENT_TIMEOUT_SECONDS", "120")),
            max_prompt_chars=int(os.environ.get("AGENT_MAX_PROMPT_CHARS", "4000")),
        )
