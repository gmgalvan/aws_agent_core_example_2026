FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

# claude-agent-sdk incluye el binario de Claude Code para linux/arm64.
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1 \
    UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Proxy SigV4 hacia AgentCore Gateway en un entorno aislado, con dependencias fijadas.
RUN uv tool install mcp-proxy-for-aws-cli==1.8.0

RUN useradd --create-home --uid 1000 bedrock_agentcore

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=bedrock_agentcore:bedrock_agentcore . .
RUN uv sync --frozen --no-dev

USER bedrock_agentcore
EXPOSE 8080

CMD ["opentelemetry-instrument", "python", "main.py"]
