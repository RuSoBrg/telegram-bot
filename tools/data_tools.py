"""Tools seguras para inspeção de ficheiros tabulares e bases SQLite."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
from langchain_core.tools import tool

from tools.safe_execution import safe_tool_execution

_READ_ONLY_SQL = re.compile(r"^\s*(select|with|explain|pragma)\b", re.IGNORECASE)
_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|"
    r"vacuum|reindex|transaction|begin|commit|rollback)\b",
    re.IGNORECASE,
)


def _json_default(value: Any) -> str:
    return str(value)


@tool
@safe_tool_execution
def analisar_ficheiro_dados(
    caminho: str,
    limite_linhas: int = 5,
) -> str:
    """Lê um CSV ou JSON local e devolve um resumo estruturado para análise.

    Args:
        caminho: Caminho absoluto ou relativo para um ficheiro ``.csv`` ou
            ``.json`` acessível pela aplicação. O ficheiro é lido sem executar
            código e sem alterar o conteúdo original.
        limite_linhas: Número máximo de registos de exemplo incluídos no
            resultado, entre 0 e 20. Use 0 quando só precisar do schema e das
            estatísticas.

    Returns:
        JSON com ``linhas``, ``colunas``, tipos inferidos, valores nulos,
        estatísticas numéricas e exemplos. A resposta é adequada para o
        agente decidir a análise seguinte; não substitui uma validação dos
        dados de negócio.

    Raises:
        ValueError: Se a extensão não for suportada ou o limite for inválido.
        FileNotFoundError: Se o caminho não existir.
    """
    path = Path(caminho).expanduser()
    if path.suffix.lower() not in {".csv", ".json"}:
        raise ValueError("Apenas ficheiros CSV e JSON são suportados.")
    if not 0 <= limite_linhas <= 20:
        raise ValueError("limite_linhas deve estar entre 0 e 20.")
    if not path.is_file():
        raise FileNotFoundError(f"Ficheiro não encontrado: {path}")

    frame = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_json(path)
    numeric = frame.select_dtypes(include="number")
    summary = {
        "ficheiro": str(path),
        "linhas": int(len(frame)),
        "colunas": list(frame.columns.astype(str)),
        "tipos": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
        "nulos": {str(column): int(value) for column, value in frame.isna().sum().items()},
        "estatisticas_numericas": numeric.describe().round(4).to_dict()
        if not numeric.empty
        else {},
        "exemplos": frame.head(limite_linhas).to_dict(orient="records"),
    }
    return json.dumps(summary, ensure_ascii=False, default=_json_default)


@tool
@safe_tool_execution
def executar_query_sql(
    caminho_base_dados: str,
    query: str,
    limite_linhas: int = 100,
) -> str:
    """Executa uma query SQLite estritamente de leitura e devolve linhas JSON.

    Args:
        caminho_base_dados: Caminho para a base SQLite de rascunho ou leitura.
            A tool não cria tabelas nem modifica a base.
        query: Query SQL de leitura iniciada por ``SELECT``, ``WITH``,
            ``EXPLAIN`` ou ``PRAGMA``. Queries múltiplas e instruções de escrita
            são rejeitadas antes da execução.
        limite_linhas: Máximo de linhas devolvidas, entre 1 e 1000, para
            proteger o contexto do agente e o Telegram.

    Returns:
        JSON com os nomes das colunas, as linhas e o total devolvido.

    Raises:
        ValueError: Se a query não for read-only ou o limite for inválido.
        FileNotFoundError: Se a base de dados não existir.
    """
    database = Path(caminho_base_dados).expanduser()
    if not database.is_file():
        raise FileNotFoundError(f"Base de dados não encontrada: {database}")
    if not 1 <= limite_linhas <= 1000:
        raise ValueError("limite_linhas deve estar entre 1 e 1000.")
    normalized = query.strip()
    if not normalized or not _READ_ONLY_SQL.match(normalized):
        raise ValueError("A query deve ser uma operação SQL de leitura.")
    if _FORBIDDEN_SQL.search(normalized) or normalized.rstrip().count(";") > 1:
        raise ValueError("A query contém uma instrução proibida ou múltiplas queries.")

    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        cursor = connection.execute(normalized)
        rows = [dict(row) for row in cursor.fetchmany(limite_linhas)]
        return json.dumps(
            {
                "colunas": [column[0] for column in cursor.description or []],
                "linhas": rows,
                "total_devolvido": len(rows),
            },
            ensure_ascii=False,
            default=_json_default,
        )
    finally:
        connection.close()
