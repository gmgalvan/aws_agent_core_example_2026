"""Copia spans de CloudWatch a Jaeger local.

AgentCore Observability guarda los spans OpenTelemetry en dos lugares:
  - `aws/spans` (Transaction Search): Harness, Gateway y Lambda;
  - el stream `spans` del log group de cada Runtime (/aws/bedrock-agentcore/runtimes/…):
    los spans del código del agente, p. ej. el Trading Agent con Claude Agent SDK.
Este script lee ambos y los reenvía como OTLP/HTTP JSON a Jaeger, para ver las trazas
desplegadas en la misma UI que el desarrollo local.

    uv run python scripts/cloudwatch_to_jaeger.py --since-minutes 60
    uv run python scripts/cloudwatch_to_jaeger.py --filter AlphaInvest

Es una herramienta de inspección para desarrollo: CloudWatch sigue siendo el sistema
de registro en AWS. No modifica nada en la cuenta.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from collections import defaultdict
from typing import Any

import boto3

_KINDS = {
    "INTERNAL": "SPAN_KIND_INTERNAL",
    "SERVER": "SPAN_KIND_SERVER",
    "CLIENT": "SPAN_KIND_CLIENT",
    "PRODUCER": "SPAN_KIND_PRODUCER",
    "CONSUMER": "SPAN_KIND_CONSUMER",
}
_STATUS = {"UNSET": "STATUS_CODE_UNSET", "OK": "STATUS_CODE_OK", "ERROR": "STATUS_CODE_ERROR"}


def _value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_value(v) for v in value]}}
    if isinstance(value, dict):
        return {"stringValue": json.dumps(value, ensure_ascii=False)}
    return {"stringValue": str(value)}


def _attributes(attrs: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [{"key": k, "value": _value(v)} for k, v in (attrs or {}).items() if v is not None]


def _otlp_span(span: dict[str, Any]) -> dict[str, Any]:
    start = int(span.get("startTimeUnixNano") or 0)
    end = span.get("endTimeUnixNano")
    if end is None:
        end = start + int(span.get("durationNano") or 0)
    status = span.get("status") or {}
    otlp = {
        "traceId": span["traceId"],
        "spanId": span["spanId"],
        "name": span.get("name", "span"),
        "kind": _KINDS.get(str(span.get("kind", "INTERNAL")).upper(), "SPAN_KIND_INTERNAL"),
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(int(end)),
        "attributes": _attributes(span.get("attributes")),
        "status": {"code": _STATUS.get(str(status.get("code", "UNSET")).upper(), "STATUS_CODE_UNSET")},
    }
    if span.get("parentSpanId"):
        otlp["parentSpanId"] = span["parentSpanId"]
    return otlp


RUNTIME_LOG_GROUPS = "/aws/bedrock-agentcore/runtimes/"


def _span_sources(logs) -> list[dict[str, Any]]:
    """aws/spans más el stream `spans` de cada log group de Runtime que lo tenga."""

    sources: list[dict[str, Any]] = [{"logGroupName": "aws/spans"}]
    for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=RUNTIME_LOG_GROUPS):
        for group in page.get("logGroups", []):
            name = group["logGroupName"]
            streams = logs.describe_log_streams(logGroupName=name, logStreamNamePrefix="spans", limit=5)
            if any(s["logStreamName"] == "spans" for s in streams.get("logStreams", [])):
                sources.append({"logGroupName": name, "logStreamNames": ["spans"]})
    return sources


def fetch_spans(logs, since_minutes: int, text_filter: str | None) -> list[dict[str, Any]]:
    start = int((time.time() - since_minutes * 60) * 1000)
    spans: dict[tuple[str, str], dict[str, Any]] = {}
    for source in _span_sources(logs):
        kwargs: dict[str, Any] = {**source, "startTime": start}
        if text_filter:
            kwargs["filterPattern"] = f'"{text_filter}"'
        for page in logs.get_paginator("filter_log_events").paginate(**kwargs):
            for event in page.get("events", []):
                try:
                    span = json.loads(event["message"])
                except json.JSONDecodeError:
                    continue
                if span.get("traceId") and span.get("spanId"):
                    spans[(span["traceId"], span["spanId"])] = span  # sin duplicados entre fuentes
    return list(spans.values())


def to_otlp(spans: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    resources: dict[str, dict[str, Any]] = {}
    for span in spans:
        resource = span.get("resource", {}).get("attributes", {})
        resource_key = json.dumps(resource, sort_keys=True)
        resources[resource_key] = resource
        scope = (span.get("scope") or {}).get("name", "aws.cloudwatch")
        groups[(resource_key, scope)].append(_otlp_span(span))
    resource_spans: dict[str, dict[str, Any]] = {}
    for (resource_key, scope), items in groups.items():
        entry = resource_spans.setdefault(
            resource_key, {"resource": {"attributes": _attributes(resources[resource_key])}, "scopeSpans": []}
        )
        entry["scopeSpans"].append({"scope": {"name": scope}, "spans": items})
    return {"resourceSpans": list(resource_spans.values())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since-minutes", type=int, default=60)
    parser.add_argument("--filter", default=None, help="Texto que debe aparecer en el span, p. ej. AlphaInvest.")
    parser.add_argument("--endpoint", default=os.environ.get("JAEGER_OTLP_HTTP", "http://localhost:4318/v1/traces"))
    args = parser.parse_args()

    logs = boto3.client("logs", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    spans = fetch_spans(logs, args.since_minutes, args.filter)
    if not spans:
        print("No hay spans en el periodo. ¿Transaction Search está ACTIVE?")
        return

    payload = to_otlp(spans)
    batch = 500
    sent = 0
    for resource in payload["resourceSpans"]:
        for scope in resource["scopeSpans"]:
            for i in range(0, len(scope["spans"]), batch):
                body = {"resourceSpans": [{**resource, "scopeSpans": [{**scope, "spans": scope["spans"][i:i + batch]}]}]}
                request = urllib.request.Request(
                    args.endpoint, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    response.read()
                sent += len(body["resourceSpans"][0]["scopeSpans"][0]["spans"])
    traces = len({s["traceId"] for s in spans})
    print(f"Enviados {sent} spans de {traces} trazas a Jaeger → http://localhost:16686")


if __name__ == "__main__":
    main()
