FROM ghcr.io/astral-sh/uv:python3.11-bookworm AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml ./
RUN uv venv /app/.venv && uv sync --no-dev --no-install-project
COPY app.py bot.py graph.py ./
COPY agents ./agents
COPY domain ./domain
COPY prompts ./prompts
COPY tools ./tools
COPY workflows ./workflows
RUN uv sync --no-dev

FROM python:3.11-slim-bookworm AS runtime

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

COPY --from=builder /app/.venv /app/.venv
COPY app.py bot.py graph.py ./
COPY agents ./agents
COPY domain ./domain
COPY prompts ./prompts
COPY tools ./tools
COPY workflows ./workflows

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["python", "bot.py"]
