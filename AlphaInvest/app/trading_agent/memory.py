"""Integración opcional con AgentCore Memory.

Si el Runtime no tiene Memory (capas 1 y 2), todas las funciones son no-op.
Un fallo de Memory nunca debe tumbar una invocación: se registra y se sigue.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

logger = logging.getLogger("trading_agent.memory")

MAX_TURNS_IN_CONTEXT = 5
MAX_STORED_CHARS = 8_000


@lru_cache(maxsize=1)
def _client(region: str):
    from bedrock_agentcore.memory import MemoryClient

    return MemoryClient(region_name=region)


def _text(record: dict[str, Any]) -> str:
    content = record.get("content") or {}
    return content.get("text", "") if isinstance(content, dict) else str(content)


def build_context(settings, actor_id: str, session_id: str, prompt: str) -> str:
    """Devuelve preferencias de largo plazo y turnos recientes como texto de contexto."""

    if not settings.memory_id:
        return ""
    sections: list[str] = []
    client = _client(settings.region)
    try:
        records = client.retrieve_memories(
            memory_id=settings.memory_id,
            namespace=f"/users/{actor_id}/preferences",
            query=prompt,
            top_k=5,
        )
        prefs = [t for t in (_text(r) for r in records) if t]
        if prefs:
            sections.append("Preferencias conocidas del usuario (AgentCore Memory):\n- " + "\n- ".join(prefs))
    except Exception:
        logger.warning("No se pudieron recuperar memorias de largo plazo", exc_info=True)
    try:
        turns = client.get_last_k_turns(
            memory_id=settings.memory_id, actor_id=actor_id, session_id=session_id, k=MAX_TURNS_IN_CONTEXT
        )
        lines = []
        for turn in turns:
            for message in turn:
                role = message.get("role", "")
                text = (message.get("content") or {}).get("text", "")
                if text:
                    lines.append(f"{role}: {text}")
        if lines:
            sections.append("Conversación previa en esta sesión:\n" + "\n".join(lines))
    except Exception:
        logger.warning("No se pudo recuperar la conversación previa", exc_info=True)
    return "\n\n".join(sections)


def save_turn(settings, actor_id: str, session_id: str, prompt: str, answer: str) -> None:
    if not settings.memory_id or not answer:
        return
    try:
        _client(settings.region).create_event(
            memory_id=settings.memory_id,
            actor_id=actor_id,
            session_id=session_id,
            messages=[(prompt[:MAX_STORED_CHARS], "USER"), (answer[:MAX_STORED_CHARS], "ASSISTANT")],
        )
    except Exception:
        logger.warning("No se pudo guardar el turno en Memory", exc_info=True)
