from contextlib import AbstractContextManager

from langgraph.checkpoint.postgres import PostgresSaver

from domain.database import database_url


_checkpoint_context: AbstractContextManager | None = None
_checkpoint_saver: PostgresSaver | None = None


def create_checkpoint_saver() -> PostgresSaver:
    """Cria o checkpointer PostgreSQL e mantém a ligação aberta no processo."""
    global _checkpoint_context, _checkpoint_saver
    if _checkpoint_saver is None:
        _checkpoint_context = PostgresSaver.from_conn_string(database_url())
        _checkpoint_saver = _checkpoint_context.__enter__()
        _checkpoint_saver.setup()
    return _checkpoint_saver
