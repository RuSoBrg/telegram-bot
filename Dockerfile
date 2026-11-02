# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:latest AS uv
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:/usr/local/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/

# Install dependencies in a cacheable layer before copying application code.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY app.py ./
COPY agents ./agents
COPY domain ./domain
COPY prompts ./prompts
COPY tools ./tools
COPY workflows ./workflows
COPY graph.py ./
COPY litellm_config.yaml ./

RUN uv sync --locked --no-dev --no-install-project \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uv", "run", "--no-dev", "app.py"]
