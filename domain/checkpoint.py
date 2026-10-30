import os
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def create_checkpoint_saver() -> SqliteSaver:
    """Cria um checkpointer SQLite durável e mantém a ligação aberta durante o processo."""
    database_path = Path(os.getenv("CHECKPOINT_DB_PATH", "workspace-checkpoints.db"))
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    return saver
