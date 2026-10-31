"""Gestão de checkpoints de persistência do LangGraph em PostgreSQL."""

import logging
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from domain.database import database_url

logger = logging.getLogger(__name__)

_sync_context: Optional[AbstractContextManager] = None
_sync_saver: Optional[PostgresSaver] = None

_async_context: Optional[AbstractAsyncContextManager] = None
_async_saver: Optional[AsyncPostgresSaver] = None


def create_checkpoint_saver() -> PostgresSaver:
    """Cria o checkpointer PostgreSQL síncrono e mantém a ligação aberta no processo."""
    global _sync_context, _sync_saver
    if _sync_saver is None:
        _sync_context = PostgresSaver.from_conn_string(database_url())
        _sync_saver = _sync_context.__enter__()
        _sync_saver.setup()
    return _sync_saver


async def create_async_checkpoint_saver() -> AsyncPostgresSaver:
    """Cria o checkpointer PostgreSQL assíncrono (AsyncPostgresSaver)."""
    global _async_context, _async_saver
    if _async_saver is None:
        _async_context = AsyncPostgresSaver.from_conn_string(database_url())
        _async_saver = await _async_context.__aenter__()
        await _async_saver.setup()
    return _async_saver


def get_checkpoint_saver(*, fallback_to_memory: bool = True) -> BaseCheckpointSaver:
    """Retorna o checkpointer configurado, com fallback resiliente para MemorySaver se DB indisponível."""
    try:
        return create_checkpoint_saver()
    except Exception as error:
        if fallback_to_memory:
            logger.warning(
                "PostgreSQL indisponível para checkpointing (%s). A usar MemorySaver como fallback resiliente.",
                error,
            )
            return MemorySaver()
        raise
