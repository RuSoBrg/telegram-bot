# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.13.6 AS uv
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=180 \
    UV_CONCURRENT_DOWNLOADS=2 \
    PATH="/app/.venv/bin:/usr/local/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/

# Install dependencies in a cacheable layer before copying application code.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-build --no-install-project

COPY app.py bot.py ./
COPY agents ./agents
COPY domain ./domain
COPY prompts ./prompts
COPY tools ./tools
COPY workflows ./workflows
COPY graph.py ./
COPY litellm_config.yaml ./

RUN uv sync --locked --no-dev --no-build --no-install-project \
    && test -f /app/bot.py \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uv", "run", "--locked", "--no-dev", "--no-build", "app.py"]
