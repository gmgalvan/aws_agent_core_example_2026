"""Descarga y resume la grabación de una sesión de AgentCore Browser (capa 6).

    TARGET=dev uv run python scripts/browser_recording.py            # última sesión
    TARGET=dev uv run python scripts/browser_recording.py <sessionId>

Genera además .local/recordings/<sessionId>/replay.html: ábrelo en tu navegador para
reproducir la sesión con rrweb-player (la misma tecnología de grabación que usa AgentCore).

La grabación (formato rrweb + eventos CDP) se guarda en .local/recordings/<sessionId>/.
El resumen muestra qué páginas abrió el agente, qué comandos ejecutó en el navegador,
si hubo clics o escritura y qué texto visible/oculto contenía la página.
Para el replay visual completo: consola de AgentCore → Browser → sesión → View Recording.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import boto3

DEMO_DIR = Path(__file__).resolve().parents[1]
TARGET = os.environ.get("TARGET", "dev")
REGION = os.environ.get("AWS_REGION", "us-east-1")

# Comandos CDP relevantes para entender lo que hizo el agente.
ACTIONS = {
    "Page.navigate": "navegar",
    "Runtime.evaluate": "ejecutar JS (leer página)",
    "DOM.getOuterHTML": "leer HTML",
    "Input.dispatchMouseEvent": "clic/mouse",
    "Input.dispatchKeyEvent": "teclado",
    "Input.insertText": "escribir texto",
    "Page.captureScreenshot": "captura de pantalla",
    "Target.closeTarget": "cerrar pestaña",
}


def outputs() -> dict[str, str]:
    stack = boto3.client("cloudformation", region_name=REGION).describe_stacks(
        StackName=f"AgentCore-AlphaInvest-{TARGET}"
    )["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def download(bucket: str, session_id: str | None) -> Path:
    s3 = boto3.client("s3", region_name=REGION)
    if session_id is None:
        listed = s3.list_objects_v2(Bucket=bucket, Prefix="recordings/").get("Contents", [])
        metas = sorted((o for o in listed if o["Key"].endswith("metadata.json")), key=lambda o: o["LastModified"])
        if not metas:
            sys.exit("Aún no hay grabaciones. Se suben cuando la sesión del navegador termina.")
        session_id = metas[-1]["Key"].split("/")[1]
    target = DEMO_DIR / ".local" / "recordings" / session_id
    target.mkdir(parents=True, exist_ok=True)
    for obj in s3.list_objects_v2(Bucket=bucket, Prefix=f"recordings/{session_id}/").get("Contents", []):
        s3.download_file(bucket, obj["Key"], str(target / Path(obj["Key"]).name))
    return target


def _text_nodes(node: dict, out: list[str]) -> None:
    if node.get("type") == 3 and node.get("textContent", "").strip():
        out.append(node["textContent"].strip())
    for child in node.get("childNodes", []):
        _text_nodes(child, out)


PLAYER = "https://cdn.jsdelivr.net/npm/rrweb-player@2.1.7/dist"


def write_replay(folder: Path, events: list[dict], timeline: list[str]) -> Path:
    """HTML autocontenido (salvo el reproductor) para ver la sesión sin la consola de AWS."""

    # Los eventos CDP (tipo 6) no se dibujan; el reproductor usa el DOM (tipos 2, 3 y 4).
    dom_events = [e for e in events if e["type"] != 6]
    if len(dom_events) < 2:
        return folder
    if dom_events[-1]["timestamp"] < events[-1]["timestamp"]:
        dom_events.append({"type": 5, "data": {"tag": "fin", "payload": {}}, "timestamp": events[-1]["timestamp"]})
    items = "".join(f"<li>{line}</li>" for line in timeline)
    page = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Replay {folder.name}</title>
<link rel="stylesheet" href="{PLAYER}/style.css">
<style>body{{margin:0;font:14px system-ui,sans-serif;background:#0f1115;color:#e6e6e6;display:flex;gap:16px;padding:16px;flex-wrap:wrap}}
aside{{max-width:420px}}h1{{font-size:16px}}li{{margin:4px 0;font-family:ui-monospace,monospace;font-size:12px}}</style></head>
<body><div id="player"></div><aside><h1>Sesión {folder.name}</h1><p>Comandos que el agente envió al navegador:</p><ol>{items}</ol></aside>
<script src="{PLAYER}/rrweb-player.umd.cjs"></script>
<script>const events = {json.dumps(dom_events)};
new rrwebPlayer({{target: document.getElementById('player'), props: {{events, width: 1024, height: 640, autoPlay: true, showDebug: false}}}});</script>
</body></html>"""
    out = folder / "replay.html"
    out.write_text(page, encoding="utf-8")
    return out


def summarize(folder: Path) -> None:
    events = []
    for batch in sorted(folder.glob("*.ndjson.gz")):
        with gzip.open(batch, "rt", encoding="utf-8") as handle:
            events += [json.loads(line) for line in handle if line.strip()]
    if not events:
        sys.exit("La grabación está vacía.")

    start = events[0]["timestamp"]
    fmt = lambda ts: f"+{(ts - start) / 1000:5.1f}s"  # noqa: E731
    print(f"Sesión {folder.name}: {len(events)} eventos, "
          f"{(events[-1]['timestamp'] - start) / 1000:.1f} s, inicio "
          f"{datetime.fromtimestamp(start / 1000, timezone.utc):%H:%M:%S} UTC\n")

    print("Línea de tiempo (comandos que el agente envió al navegador):")
    counts: Counter[str] = Counter()
    texts: list[str] = []
    timeline: list[str] = []
    for event in events:
        if event["type"] == 4:
            line = f"{fmt(event['timestamp'])}  página cargada: {event['data'].get('href')}"
            timeline.append(line)
            print("  " + line)
        elif event["type"] == 2:
            _text_nodes(event["data"].get("node", {}), texts)
        elif event["type"] == 6:
            payload = event["data"].get("payload", {}).get("data", {})
            method = payload.get("method", "")
            if payload.get("direction") == "outgoing" or method in ACTIONS:
                counts[method] += 1
                if method in ACTIONS:
                    detail = ""
                    params = payload.get("params") or {}
                    if method == "Page.navigate":
                        detail = params.get("url", "")
                    elif method == "Runtime.evaluate":
                        detail = re.sub(r"\s+", " ", str(params.get("expression", "")))[:70]
                    line = f"{fmt(event['timestamp'])}  {ACTIONS[method]:<26} {detail}"
                    timeline.append(line)
                    print("  " + line)

    interactions = sum(counts[m] for m in ("Input.dispatchMouseEvent", "Input.dispatchKeyEvent", "Input.insertText"))
    print(f"\nInteracciones (clics/teclado/escritura): {interactions}")
    body = " ".join(texts)
    injection = "INSTRUCCIÓN PRIORITARIA" in body
    print(f"El DOM grabado contiene la instrucción oculta (prompt injection): {'sí' if injection else 'no'}")
    headlines = [t for t in texts if re.search(r"\b(NVDA|AAPL|MSFT)\b", t) and len(t) > 40]
    if headlines:
        print("Titulares que vio el agente:")
        for line in headlines[:4]:
            print(f"  · {line[:110]}")
    replay = write_replay(folder, events, timeline)
    print(f"\nArchivos: {folder.relative_to(DEMO_DIR)}")
    if replay.name == "replay.html":
        print(f"Replay local: abre {replay} en tu navegador")


def main() -> None:
    outs = outputs()
    bucket = outs.get("BrowserRecordingsBucket")
    if not bucket:
        sys.exit(f"El ambiente {TARGET} no tiene la capa 6 (Browser).")
    summarize(download(bucket, sys.argv[1] if len(sys.argv) > 1 else None))


if __name__ == "__main__":
    main()
