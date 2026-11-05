"""Capa 9 (opcional) — AWS Agent Registry: catálogo gobernado de agentes y herramientas.

Publica el Gateway como servidor MCP y los dos agentes como registros AGENT,
los envía a aprobación, los aprueba (rol de curador) y hace una búsqueda.

    TARGET=dev uv run python layers/9_registry.py            # publicar y buscar
    TARGET=dev uv run python layers/9_registry.py --delete   # eliminar el registry

Usa la API vigente `agent-registry-control` / `agent-registry`; el namespace de
preview en `bedrock-agentcore-control` dejó de estar soportado el 30-oct-2026.
https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry.html
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import boto3

DEMO_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = DEMO_DIR / "AlphaInvest"
TARGET = os.environ.get("TARGET", "dev")
REGISTRY_NAME = f"alphainvest-{TARGET}"


def status() -> dict:
    out = subprocess.run(
        ["agentcore", "status", "--target", TARGET, "--json"],
        cwd=PROJECT_DIR, check=True, capture_output=True, text=True,
    ).stdout
    return {r["name"]: r for r in json.loads(out).get("resources", [])}


def wait_record(control, registry_id: str, record_id: str) -> str:
    for _ in range(60):
        state = control.get_registry_record(registryId=registry_id, recordId=record_id)["status"]
        if state not in {"CREATING", "UPDATING"}:
            return state
        time.sleep(2)
    sys.exit(f"El registro {record_id} no terminó de crearse.")


def find_registry(control) -> dict | None:
    paginator = control.get_paginator("list_registries") if control.can_paginate("list_registries") else None
    pages = paginator.paginate() if paginator else [control.list_registries()]
    for page in pages:
        for registry in page.get("registries", []):
            if registry.get("name") == REGISTRY_NAME:
                return registry
    return None


def ensure_registry(control) -> str:
    existing = find_registry(control)
    if existing:
        return existing["registryId"]
    # Sin reglas de auto-aprobación: cada registro pasa por un curador.
    created = control.create_registry(
        name=REGISTRY_NAME,
        description=f"Catálogo de agentes y herramientas de AlphaInvest ({TARGET})",
        discoveryConfiguration={"authorizerType": "AWS_IAM"},
    )
    return created["registryId"]


def publish(control, registry_id: str, name: str, record_type: str, description: str, descriptors: dict) -> str:
    records = control.list_registry_records(registryId=registry_id).get("registryRecords", [])
    record = next((r for r in records if r.get("name") == name), None)
    if record is None:
        record = control.create_registry_record(
            registryId=registry_id, name=name, recordType=record_type,
            description=description, descriptors=descriptors, recordVersion="1.0.0",
        )
    record_id = record["recordId"]
    state = wait_record(control, registry_id, record_id)
    if state == "DRAFT":
        control.submit_registry_record_for_approval(registryId=registry_id, recordId=record_id)
        state = wait_record(control, registry_id, record_id)
    if state == "PENDING_APPROVAL":
        control.update_registry_record_status(
            registryId=registry_id, recordId=record_id, status="APPROVED",
            statusReason="Revisado en la demo: datos simulados, sin acceso a brokers reales.",
        )
        state = wait_record(control, registry_id, record_id)
    print(f"  {record_type:<6} {name:<16} {state}")
    return record_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--query", default="herramientas para analizar un portafolio de acciones")
    args = parser.parse_args()

    region = os.environ.get("AWS_REGION", "us-east-1")
    control = boto3.client("agent-registry-control", region_name=region)

    if args.delete:
        registry = find_registry(control)
        if registry:
            for record in control.list_registry_records(registryId=registry["registryId"]).get("registryRecords", []):
                control.delete_registry_record(registryId=registry["registryId"], recordId=record["recordId"])
            control.delete_registry(registryId=registry["registryId"])
            print(f"Registry {REGISTRY_NAME} eliminado.")
        return

    resources = status()
    for required in ("finance", "trading_agent", "portfolio"):
        if required not in resources:
            sys.exit(f"Falta '{required}' en el target {TARGET}; aplica antes las capas 1 y 2.")

    registry_id = ensure_registry(control)
    print(f"Registry {REGISTRY_NAME} ({registry_id})")

    tools = json.loads((PROJECT_DIR / "app" / "finance_tools" / "tool_definitions.json").read_text(encoding="utf-8"))
    server = {
        "name": "io.alphainvest/finance-tools",
        "description": "Precios, portafolio y paper trading simulados vía AgentCore Gateway (IAM).",
        "version": "1.0.0",
    }
    publish(control, registry_id, "finance-tools", "MCP", server["description"], {
        "mcpServer": {
            "data": json.dumps(server), "dataSchemaVersion": "2025-12-11",
            "additionalData": {"tools": {"data": json.dumps({"tools": tools}), "dataSchemaVersion": "2025-11-25"}},
        }
    })
    agents = {
        "trading-agent": ("trading_agent", "Agente autónomo de paper trading (Claude Agent SDK en AgentCore Runtime)."),
        "portfolio-agent": ("portfolio", "Agente de análisis de portafolio y diversificación (AgentCore Harness)."),
    }
    for record_name, (resource, description) in agents.items():
        publish(control, registry_id, record_name, "AGENT", description, {
            "custom": {"data": json.dumps({
                "arn": resources[resource]["arn"],
                "kind": resources[resource]["resourceType"],
                "tools": ["finance-tools"],
                "dataClassification": "simulated",
            })}
        })

    print(f"\nBúsqueda: “{args.query}”")
    discovery = boto3.client("agent-registry", region_name=region)
    time.sleep(5)  # la indexación de búsqueda es asíncrona
    found = discovery.search_discoverable_registry_records(searchQuery=args.query, registryIds=[registry_id], maxResults=5)
    for record in found.get("registryRecords", found.get("records", [])):
        print(f"  → {record.get('recordType', ''):<6} {record.get('name')}: {record.get('description', '')}")


if __name__ == "__main__":
    main()
