#!/bin/bash
set -euo pipefail

# ─── Configuração do Webhook / Túnel ───

# 1. Se estiver a usar Ngrok ou um Webhook URL externo diretamente (sem Cloudflare Tunnel)
if [ -n "${TELEGRAM_WEBHOOK_URL:-}" ] && [ -z "${TUNNEL_TOKEN:-}" ]; then
    echo "═══ Modo NGROK / Webhook Externo Detetado ═══"
    echo "URL: $TELEGRAM_WEBHOOK_URL"

# 2. Se TUNNEL_TOKEN estiver definido, inicia o Cloudflare Tunnel nomeado
elif [ -n "${TUNNEL_TOKEN:-}" ]; then
    echo "═══ Modo PRODUÇÃO: Túnel Cloudflare nomeado (URL fixo) ═══"
    cloudflared tunnel run --token "$TUNNEL_TOKEN" &
    CLOUDFLARED_PID=$!

    sleep 3
    if ! kill -0 "$CLOUDFLARED_PID" 2>/dev/null; then
        echo "Erro: O túnel Cloudflare não conseguiu arrancar. Verifica o TUNNEL_TOKEN."
        exit 1
    fi
    echo "Túnel Cloudflare nomeado ativo (PID: $CLOUDFLARED_PID)."

# 3. Fallback: Quick Tunnel efémero
else
    echo "═══ Modo DEV: Quick Tunnel Cloudflare (URL temporário) ═══"
    cloudflared tunnel --url http://localhost:${PORT:-8000} --metrics localhost:20241 &
    CLOUDFLARED_PID=$!

    echo "A aguardar pela geração do URL público..."
    TUNNEL_URL=""
    for i in $(seq 1 15); do
        TUNNEL_URL=$(curl -s http://localhost:20241/metrics 2>/dev/null \
            | grep -o 'https://[a-zA-Z0-9.-]*\.trycloudflare\.com' \
            | head -n 1 || true)
        if [ -n "$TUNNEL_URL" ]; then
            break
        fi
        sleep 1
    done

    if [ -z "$TUNNEL_URL" ]; then
        echo "Erro: Não foi possível obter o URL do Quick Tunnel."
        kill "$CLOUDFLARED_PID" 2>/dev/null || true
        exit 1
    fi

    echo "URL do Quick Tunnel detetado: $TUNNEL_URL"
    export TELEGRAM_WEBHOOK_URL="${TUNNEL_URL}/telegram/webhook"
fi

echo "A iniciar o bot do Telegram..."
exec python bot.py
