"""Entrypoint assíncrono de produção para Telegram e FastAPI."""

import asyncio
import os

import uvicorn


async def main() -> None:
    """Inicializa o ciclo de vida HTTP/Telegram num único runtime assíncrono."""
    from bot import api

    server = uvicorn.Server(
        uvicorn.Config(
            api,
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            log_level="info",
        )
    )
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
