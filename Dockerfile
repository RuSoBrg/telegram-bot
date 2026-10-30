# ─── Stage 1: Instalar dependências Python com uv ───
FROM ghcr.io/astral-sh/uv:python3.11-bookworm AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock* ./
RUN uv venv /app/.venv && uv sync --locked --no-dev --no-install-project --no-build
COPY app.py bot.py graph.py ./
COPY agents ./agents
COPY domain ./domain
COPY prompts ./prompts
COPY tools ./tools
COPY workflows ./workflows
RUN uv sync --locked --no-dev --no-build


# ─── Stage 2: Runtime leve com cloudflared ───
FROM python:3.11-slim-bookworm AS runtime

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# Instalar cloudflared (binário oficial da Cloudflare para Debian/Ubuntu)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
         -o /tmp/cloudflared.deb \
    && dpkg -i /tmp/cloudflared.deb \
    && rm -f /tmp/cloudflared.deb \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Copiar o venv e o código
COPY --from=builder /app/.venv /app/.venv
COPY app.py bot.py graph.py ./
COPY agents ./agents
COPY domain ./domain
COPY prompts ./prompts
COPY tools ./tools
COPY workflows ./workflows

# Copiar e preparar o entrypoint ANTES de mudar de utilizador
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

# Utilizador não-root para segurança
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["./entrypoint.sh"]
