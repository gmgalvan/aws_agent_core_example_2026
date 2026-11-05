"""Trading Agent autónomo: Claude Agent SDK alojado en AgentCore Runtime.

El loop agéntico es de Claude Agent SDK. AgentCore Runtime aporta aislamiento
por sesión, escalado e identidad (rol de ejecución). Las herramientas llegan por
MCP desde AgentCore Gateway; sin Gateway el agente funciona pero sin datos.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

import memory
from config import Settings

app = BedrockAgentCoreApp()
logger = logging.getLogger("trading_agent")
settings = Settings.from_env()

SYSTEM_PROMPT = """Eres un agente autónomo especializado en trading cuantitativo educativo. \
Tu objetivo es analizar oportunidades de inversión utilizando exclusivamente datos simulados. \
Puedes consultar herramientas financieras, comparar activos y realizar operaciones de paper trading. \
Debes justificar tus decisiones con información obtenida mediante herramientas. \
Nunca ejecutes operaciones reales.

Reglas operativas:
- Responde en español, de forma breve y estructurada.
- Si no tienes herramientas financieras disponibles, dilo con claridad y no inventes datos.
- Decide tú si conviene operar o abstenerte; abstenerse es una decisión válida.
- Antes de operar, revisa el portafolio y el efectivo disponible.
- Cita los datos (precio, cambio porcentual, efectivo) que justifican tu decisión."""


def _safe_id(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", value or "")[:100]
    return cleaned or fallback


def _jwt_claims(headers: dict[str, str]) -> dict[str, Any]:
    """Claims del JWT de entrada. AgentCore Runtime ya validó firma, emisor y expiración."""

    auth = next((v for k, v in headers.items() if k.lower() == "authorization"), "")
    if not auth.lower().startswith("bearer "):
        return {}
    try:
        body = auth.split()[1].split(".")[1]
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (IndexError, ValueError):
        logger.warning("Authorization presente pero no es un JWT legible")
        return {}


def _actor_id(payload: dict[str, Any], context: Any) -> str:
    headers = getattr(context, "request_headers", None) or {}
    claims = _jwt_claims(headers)
    if claims:
        # Con Identity (capa 4) el usuario sale del token: el payload no puede suplantarlo.
        return _safe_id(str(claims.get("username") or claims.get("sub") or ""), "anonymous")
    header_user = next(
        (v for k, v in headers.items() if k.lower() == "x-amzn-bedrock-agentcore-runtime-user-id"), None
    )
    return _safe_id(str(payload.get("user_id") or header_user or ""), "demo-user")


def _mcp_servers() -> dict[str, Any]:
    if settings.gateway_url:
        # Gateway con autorización AWS_IAM: el proxy firma con SigV4 usando el
        # rol de ejecución del Runtime. No hay secretos en el contenedor.
        return {
            "finance": {
                "type": "stdio",
                "command": settings.mcp_proxy_command,
                "args": [
                    settings.gateway_url,
                    "--service",
                    "bedrock-agentcore",
                    "--region",
                    settings.region,
                    "--disable-telemetry",
                    "--log-level",
                    "WARNING",
                ],
            }
        }
    if settings.local_mcp_command:
        command, *args = settings.local_mcp_command.split()
        return {"finance": {"type": "stdio", "command": command, "args": args}}
    return {}


def _summarize(value: Any, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def _short_tool_name(name: str) -> str:
    # mcp__finance__<target>___simulate_trade -> simulate_trade
    return name.rsplit("___", 1)[-1].rsplit("__", 1)[-1]


async def _run_agent(prompt: str, session_id: str, actor_id: str) -> AsyncIterator[dict[str, Any]]:
    workdir = Path(settings.sessions_dir) / _safe_id(session_id, "default-session")
    workdir.mkdir(parents=True, exist_ok=True)

    context_block = memory.build_context(settings, actor_id, session_id, prompt)
    system_prompt = SYSTEM_PROMPT + (f"\n\n{context_block}" if context_block else "")

    servers = _mcp_servers()
    options = ClaudeAgentOptions(
        model=settings.model_id,
        system_prompt=system_prompt,
        cwd=str(workdir),
        # Sin herramientas integradas: nada de Bash, Read, Write ni WebFetch.
        tools=[],
        mcp_servers=servers,
        strict_mcp_config=True,
        allowed_tools=[f"mcp__{name}" for name in servers],
        permission_mode="dontAsk",
        setting_sources=[],
        max_turns=settings.max_turns,
        max_budget_usd=settings.max_budget_usd,
        env={
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": settings.region,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": settings.model_id,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": settings.model_id,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": settings.model_id,
            "DISABLE_TELEMETRY": "1",
            "DISABLE_AUTOUPDATER": "1",
            "MCP_TIMEOUT": "60000",
        },
    )

    yield {"type": "start", "session_id": session_id, "user": actor_id, "tools_connected": bool(servers)}
    final_text: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    yield {"type": "tool_use", "tool": _short_tool_name(block.name), "input": block.input}
                elif isinstance(block, TextBlock) and block.text.strip():
                    final_text.append(block.text)
                    yield {"type": "text", "text": block.text}
        elif isinstance(message, ResultMessage):
            usage = message.usage or {}
            yield {
                "type": "result",
                "stop_reason": message.subtype,
                "turns": message.num_turns,
                "duration_ms": message.duration_ms,
                "cost_usd": message.total_cost_usd,
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
            }

    memory.save_turn(settings, actor_id, session_id, prompt, "\n".join(final_text))


@app.entrypoint
async def invoke(payload, context):
    """Payload: {"prompt": "...", "user_id": "opcional"}. Responde con eventos JSON en streaming."""

    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        yield {"type": "error", "message": "Incluye un campo 'prompt' con texto."}
        return
    if len(prompt) > settings.max_prompt_chars:
        yield {"type": "error", "message": f"El prompt excede {settings.max_prompt_chars} caracteres."}
        return

    session_id = getattr(context, "session_id", None) or "default-session"
    actor_id = _actor_id(payload, context)
    started = time.monotonic()
    stream = _run_agent(prompt.strip(), session_id, actor_id).__aiter__()
    try:
        while True:
            remaining = settings.timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise asyncio.TimeoutError
            try:
                event = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                break
            if event["type"] == "tool_use":
                logger.info("tool_use %s %s", event["tool"], _summarize(event["input"]))
            yield event
    except asyncio.TimeoutError:
        logger.warning("Invocación detenida por tiempo límite (%ss)", settings.timeout_seconds)
        yield {"type": "error", "message": f"Tiempo límite de {settings.timeout_seconds}s alcanzado."}
    finally:
        await stream.aclose()


if __name__ == "__main__":
    app.run()
