"""Muestra los eventos del Trading Agent (JSON por línea) de forma legible."""

import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        print(line)
        continue
    if isinstance(event, str):
        print(event)
        continue
    kind = event.get("type")
    if kind == "tool_use":
        print(f"🔧 {event['tool']} {json.dumps(event.get('input', {}), ensure_ascii=False)}")
    elif kind == "text":
        print(f"\n{event['text']}\n")
    elif kind == "result":
        print(
            f"✔ {event.get('stop_reason')} · turnos {event.get('turns')} · {event.get('duration_ms')} ms"
            f" · tokens {event.get('input_tokens')}/{event.get('output_tokens')} · USD {event.get('cost_usd')}"
        )
    elif kind == "error":
        print(f"✖ {event.get('message')}")
    elif kind == "start":
        print(f"▶ sesión {event.get('session_id')} · usuario {event.get('user')} · herramientas: {event.get('tools_connected')}")
    sys.stdout.flush()
