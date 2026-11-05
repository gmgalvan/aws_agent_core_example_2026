"""Invoca los agentes desplegados y muestra herramientas, respuesta y uso.

    uv run python scripts/invoke_aws.py trading   "Analiza AAPL y MSFT ..."
    uv run python scripts/invoke_aws.py portfolio "Analiza mi portafolio ..."

Opciones: --target dev|uat|prod, --session <id>, --user <actor>.
Pruebas de la capa 4 (Trading Agent):
    --iam                 fuerza SigV4 aunque Identity esté activa (debe rechazarse)
    --payload-user <otro> manda otro user_id en el body junto al JWT (debe ignorarse)
Los ARN se leen de `agentcore status --json`; no hay identificadores en el código.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

DEMO_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = DEMO_DIR / "AlphaInvest"
NAMES = {"trading": "trading_agent", "portfolio": "portfolio"}
_TTY = sys.stdout.isatty() and not __import__("os").environ.get("NO_COLOR")


def _box(title: str, *lines: str) -> None:
    """Recuadro con la llamada que se hace, igual que los scripts de capas. Nunca imprime secretos."""

    gray, yellow, reset = ("\033[0;90m", "\033[1;33m", "\033[0m") if _TTY else ("", "", "")
    print(f"{gray}┌─ {title}{reset}")
    for line in lines:
        print(f"{gray}│{reset} {yellow}{line}{reset}")
    print(f"{gray}└─{reset}", flush=True)


def _short(arn: str) -> str:
    return "…/" + arn.rsplit("/", 1)[-1]


def _find_arn(node, name: str) -> str | None:
    if isinstance(node, dict):
        if node.get("name") == name:
            for key, value in node.items():
                if key.lower().endswith("arn") and isinstance(value, str) and value.startswith("arn:"):
                    return value
        for value in node.values():
            found = _find_arn(value, name)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_arn(item, name)
            if found:
                return found
    return None


def resolve(target: str, name: str) -> tuple[str, str]:
    out = subprocess.run(
        ["agentcore", "status", "--target", target, "--json"],
        cwd=PROJECT_DIR,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    arn = _find_arn(json.loads(out), name)
    if not arn:
        sys.exit(f"No encontré el ARN de '{name}' en el target '{target}'. ¿Está desplegado?")
    return arn, arn.split(":")[3]


def stack_outputs(target: str, region: str) -> dict[str, str]:
    cfn = boto3.client("cloudformation", region_name=region)
    stack = cfn.describe_stacks(StackName=f"AgentCore-AlphaInvest-{target}")["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def cognito_token(target: str, region: str, client_id: str, user: str) -> str:
    """Access token del usuario (capa 4). Credenciales creadas con scripts/identity-user.sh."""

    creds_file = DEMO_DIR / ".local" / f"cognito-{target}-{user}.json"
    if not creds_file.exists():
        sys.exit(f"No hay credenciales para '{user}'. Ejecuta: TARGET={target} scripts/identity-user.sh {user}")
    creds = json.loads(creds_file.read_text())
    _box("Identity: login en Cognito",
         f"cognito-idp.initiate_auth(AuthFlow=USER_PASSWORD_AUTH, USERNAME={user}, PASSWORD=********)",
         f"contraseña leída de .local/{creds_file.name}")
    auth = boto3.client("cognito-idp", region_name=region).initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": creds["username"], "PASSWORD": creds["password"]},
    )
    return auth["AuthenticationResult"]["AccessToken"]


def _render(lines) -> None:
    renderer = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("render_events.py"))], stdin=subprocess.PIPE, text=True
    )
    for line in lines:
        if line.startswith("data: "):
            renderer.stdin.write(line[6:] + "\n")
            renderer.stdin.flush()
    renderer.stdin.close()
    renderer.wait()


def invoke_trading_jwt(arn: str, region: str, prompt: str, session: str, token: str, payload_user: str | None = None) -> None:
    url = (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
        f"{urllib.parse.quote(arn, safe='')}/invocations?qualifier=DEFAULT"
    )
    _box("ejecutando · autenticación: JWT (Bearer) — el Runtime rechaza IAM",
         f"POST https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{_short(arn)}/invocations?qualifier=DEFAULT",
         "Authorization: Bearer <access token de Cognito>  ·  body: {\"prompt\": …"
         + (f", \"user_id\": \"{payload_user}\"}}  ← intento de suplantación" if payload_user else "}"))
    body = {"prompt": prompt} | ({"user_id": payload_user} if payload_user else {})
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            _render(raw.decode("utf-8").rstrip("\n") for raw in response)
    except urllib.error.HTTPError as error:
        sys.exit(f"HTTP {error.code}: {error.read().decode()[:300]}")


def invoke_trading(arn: str, region: str, prompt: str, session: str, user: str) -> None:
    _box("ejecutando · autenticación: IAM (SigV4)",
         f"bedrock-agentcore.invoke_agent_runtime(agentRuntimeArn={_short(arn)}, runtimeUserId={user}, runtimeSessionId={session})")
    client = boto3.client("bedrock-agentcore", region_name=region)
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId=session,
            runtimeUserId=user,
            contentType="application/json",
            accept="text/event-stream",
            payload=json.dumps({"prompt": prompt, "user_id": user}).encode(),
        )
    except ClientError as error:
        # Con la capa 4, AgentCore rechaza SigV4 antes de que el agente se ejecute.
        sys.exit(f"✖ Rechazado por AgentCore: {error.response['Error']['Code']}: {error.response['Error']['Message']}")
    _render(raw.decode("utf-8") for raw in response["response"].iter_lines())


def invoke_portfolio(arn: str, region: str, prompt: str, session: str, user: str) -> None:
    _box("ejecutando · autenticación: IAM (SigV4)",
         f"bedrock-agentcore.invoke_harness(harnessArn={_short(arn)}, actorId={user}, runtimeSessionId={session})")
    client = boto3.client("bedrock-agentcore", region_name=region)
    started = time.monotonic()
    response = client.invoke_harness(
        harnessArn=arn,
        runtimeSessionId=session,
        runtimeUserId=user,
        actorId=user,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
    )
    print(f"▶ sesión {session}")
    tool_inputs: dict[int, str] = {}
    tool_names: dict[int, str] = {}
    for event in response["stream"]:
        if "contentBlockStart" in event:
            start = event["contentBlockStart"]
            tool = start.get("start", {}).get("toolUse")
            if tool:
                tool_names[start["contentBlockIndex"]] = tool["name"]
                tool_inputs[start["contentBlockIndex"]] = ""
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"]
            index = delta.get("contentBlockIndex")
            body = delta.get("delta", {})
            if "text" in body:
                print(body["text"], end="", flush=True)
            elif "toolUse" in body and index in tool_inputs:
                tool_inputs[index] += body["toolUse"].get("input", "")
        elif "contentBlockStop" in event:
            index = event["contentBlockStop"].get("contentBlockIndex")
            if index in tool_names:
                print(f"\n🔧 {tool_names.pop(index)} {tool_inputs.pop(index, '')}", flush=True)
        elif "messageStop" in event:
            reason = event["messageStop"].get("stopReason")
            if reason not in {"tool_use", "tool_result"}:
                print(f"\n■ stopReason: {reason}")
        elif "metadata" in event:
            usage = event["metadata"].get("usage", {})
            if usage:
                print(f"· tokens {usage.get('inputTokens')}/{usage.get('outputTokens')}")
        elif "runtimeClientError" in event:
            print(f"\n✖ {event['runtimeClientError'].get('message')}")
    print(f"✔ {round(time.monotonic() - started, 1)} s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("agent", choices=sorted(NAMES))
    parser.add_argument("prompt")
    parser.add_argument("--target", default="dev")
    parser.add_argument("--session", default=None, help="Mínimo 33 caracteres; reutilízalo para continuar.")
    parser.add_argument("--user", default="demo-investor")
    parser.add_argument("--iam", action="store_true", help="Capa 4: fuerza SigV4 (debe rechazarse).")
    parser.add_argument("--payload-user", help="Capa 4: user_id falso en el body (debe ignorarse).")
    args = parser.parse_args()

    session = args.session or f"{args.agent}-{uuid.uuid4()}"
    if len(session) < 33:
        sys.exit("--session debe tener al menos 33 caracteres.")
    arn, region = resolve(args.target, NAMES[args.agent])
    if args.agent == "portfolio":
        invoke_portfolio(arn, region, args.prompt, session, args.user)
        return
    client_id = stack_outputs(args.target, region).get("TradingAppClientId")
    if client_id and not args.iam:
        # Capa 4: el Runtime solo acepta JWT; el usuario lo determina el token.
        token = cognito_token(args.target, region, client_id, args.user)
        invoke_trading_jwt(arn, region, args.prompt, session, token, args.payload_user)
    else:
        invoke_trading(arn, region, args.prompt, session, args.user)


if __name__ == "__main__":
    main()
