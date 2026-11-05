# Instructions for coding agents: runtime `trading_agent`

You are writing the code for an AgentCore runtime. The AgentCore CLI deploys
this folder. Follow these rules exactly, or `agentcore dev` and
`agentcore deploy` will fail. `README.md` in this folder explains each rule
in more detail.

## Must

- Put the entrypoint in `main.py` at the root of this folder. To use another
  file, name it in `pyproject.toml` under `[tool.agentcore]` as
  `entrypoint = "server.py"` (optionally `"server.py:app"`); the CLI copies it
  into `agentcore/agentcore.json` on `agentcore dev`, `build`, and `deploy`.
- Implement the runtime contract with the AgentCore SDK for Python
  (`bedrock-agentcore`, https://github.com/aws/bedrock-agentcore-sdk-python);
  examples: https://github.com/awslabs/amazon-bedrock-agentcore-samples.
- Serve the AgentCore HTTP contract on `0.0.0.0` port 8080: `POST /invocations`
  (JSON in; JSON or `text/event-stream` out) and `GET /ping` (returns
  `{"status": "Healthy"}` or `{"status": "HealthyBusy"}`). Use
  `BedrockAgentCoreApp` from `bedrock_agentcore.runtime` with an
  `@app.entrypoint` function; it implements both.
- Keep a module-level `app` and end `main.py` with
  `if __name__ == "__main__": app.run()`.
- Declare every dependency in `[project].dependencies` in `pyproject.toml`.
  Do not use `requirements.txt`.
- Include `bedrock-agentcore` and `aws-opentelemetry-distro` in the
  dependencies.
- Set `requires-python` to a range that includes at least one of
  3.10, 3.11, 3.12, 3.13 and 3.14. It constrains `runtimeVersion` in `agentcore.json`:
  the CLI keeps the current version while the range allows it, and only
  replaces it (with the highest allowed) when the range excludes it.
- Only use packages that publish `aarch64` manylinux wheels (glibc ≤ 2.34).
- Read resource IDs from environment variables (`MEMORY_<NAME>_ID`,
  `AGENTCORE_GATEWAY_<NAME>_URL`, `CREDENTIAL_<NAME>_NAME`, ...) and handle
  them being missing.
- Treat a Gateway URL according to its protocol: an MCP Gateway URL is already
  an MCP endpoint; a `protocolType: "None"` URL is a bare origin, so append
  `/mcp` for MCP or the route required by the HTTP target.
- Use the `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header (or
  `context.session_id`) to tell conversations apart. Custom headers only
  arrive if they are named `X-Amzn-Bedrock-AgentCore-Runtime-Custom-*` and
  listed in `requestHeaderAllowlist`.

## Must not

- Do not hardcode secrets or put `.env` files in this folder. Use
  `agentcore add credential` and `@requires_api_key`.
- Do not edit `agentcore/agentcore.json` unless the user asks. Set the build
  settings in the code instead (`Dockerfile`, `requires-python`,
  `[tool.agentcore] entrypoint`); the CLI updates `agentcore.json` from them.
- Do not add a `Dockerfile` unless the user wants a container build.
  Adding one switches this runtime to container mode.
- If you implement `/ping` yourself, do not set `time_of_last_update` to the
  current time on every ping; only change it when the status changes.

## Check your work

- Run `uv sync`, then `agentcore dev`, and invoke it locally.
- Run `agentcore deploy` when the user is ready.
- Contract reference: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html
