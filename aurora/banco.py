"""Dados do condominio em SQLite: reservas e autorizacoes de visita.

Os arquivos de `dados/` sao so o estado inicial; tudo que o assistente muda vai
para `estado/condominio.db`.

Garantia 5 (exclusividade): o indice unico parcial `uq_reserva_ativa` impede
duas reservas ATIVAS para a mesma area na mesma data. A checagem acontece no
proprio INSERT, dentro do banco, entao duas aprovacoes simultaneas nunca gravam
as duas: a segunda recebe IntegrityError e vira uma resposta normal.

Cancelar nao apaga a linha (so marca `ativa = 0`), e a chave primaria `codigo`
garante que um codigo novo nunca repete o de outra reserva, cancelada ou nao.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from .config import BANCO_CONDOMINIO, DADOS, ESTADO

ESQUEMA = """
CREATE TABLE IF NOT EXISTS reservas (
    codigo       TEXT PRIMARY KEY,
    apartamento  TEXT NOT NULL,
    area         TEXT NOT NULL,
    data         TEXT NOT NULL,
    ativa        INTEGER NOT NULL DEFAULT 1,
    atualizada_em TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_reserva_ativa ON reservas (area, data) WHERE ativa = 1;
CREATE INDEX IF NOT EXISTS ix_reservas_apartamento ON reservas (apartamento);

CREATE TABLE IF NOT EXISTS visitantes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento  TEXT NOT NULL,
    nome         TEXT NOT NULL,
    data         TEXT NOT NULL,
    criada_em    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_visitantes_apartamento ON visitantes (apartamento);
"""


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conexao() -> Iterator[sqlite3.Connection]:
    ESTADO.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(BANCO_CONDOMINIO, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        con.executescript(ESQUEMA)
        yield con
    finally:
        con.close()


def _ler_json(nome: str) -> list[dict]:
    return json.loads((DADOS / nome).read_text(encoding="utf-8"))


AREAS: dict[str, dict] = {a["id"]: a for a in _ler_json("areas.json")}
APARTAMENTOS: dict[str, dict] = {a["numero"]: a for a in _ler_json("apartamentos.json")}


def restaurar() -> None:
    """Volta reservas e visitantes ao estado de `dados/`."""
    with conexao() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM reservas")
        con.execute("DELETE FROM visitantes")
        for r in _ler_json("reservas.json"):
            con.execute(
                "INSERT INTO reservas (codigo, apartamento, area, data, ativa, atualizada_em) VALUES (?, ?, ?, ?, 1, ?)",
                (r["codigo"], r["apartamento"], r["area"], r["data"], _agora()),
            )
        for v in _ler_json("visitantes.json"):
            con.execute(
                "INSERT INTO visitantes (apartamento, nome, data, criada_em) VALUES (?, ?, ?, ?)",
                (v["apartamento"], v["nome"], v["data"], _agora()),
            )
        con.execute("COMMIT")


def reservas_do_apartamento(apartamento: str) -> list[dict]:
    with conexao() as con:
        linhas = con.execute(
            "SELECT codigo, area, data FROM reservas WHERE apartamento = ? AND ativa = 1 ORDER BY data, codigo",
            (apartamento,),
        ).fetchall()
    return [dict(l) for l in linhas]


def visitantes_do_apartamento(apartamento: str) -> list[dict]:
    with conexao() as con:
        linhas = con.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, id", (apartamento,)
        ).fetchall()
    return [dict(l) for l in linhas]


def area_livre(area: str, data: str) -> bool:
    """So diz se a data esta livre; nunca de quem e a reserva."""
    with conexao() as con:
        linha = con.execute("SELECT 1 FROM reservas WHERE area = ? AND data = ? AND ativa = 1", (area, data)).fetchone()
    return linha is None


def criar_reserva(apartamento: str, area: str, data: str) -> str | None:
    """Grava a reserva e devolve o codigo; None se a area ja estiver reservada na data.

    A exclusividade vale no instante do INSERT (indice unico parcial), nao numa
    consulta feita antes.
    """
    with conexao() as con:
        for _ in range(20):
            codigo = f"RSV-{secrets.randbelow(9000) + 1000}"
            try:
                con.execute(
                    "INSERT INTO reservas (codigo, apartamento, area, data, ativa, atualizada_em) VALUES (?, ?, ?, ?, 1, ?)",
                    (codigo, apartamento, area, data, _agora()),
                )
                return codigo
            except sqlite3.IntegrityError as erro:
                if "reservas.codigo" in str(erro):
                    continue  # codigo ja usado (inclusive por reserva cancelada): sorteia outro
                return None  # uq_reserva_ativa: alguem reservou a area nessa data
    raise RuntimeError("nao foi possivel gerar um codigo de reserva unico")


def cancelar_reserva(apartamento: str, area: str | None, data: str | None, codigo: str | None) -> list[dict]:
    """Cancela reservas ativas DO APARTAMENTO que casam com os filtros informados."""
    filtros, valores = ["apartamento = ?", "ativa = 1"], [apartamento]
    for coluna, valor in (("area", area), ("data", data), ("codigo", codigo)):
        if valor:
            filtros.append(f"{coluna} = ?")
            valores.append(valor)
    if len(filtros) == 2:
        return []
    onde = " AND ".join(filtros)
    with conexao() as con:
        con.execute("BEGIN IMMEDIATE")
        alvo = [dict(l) for l in con.execute(f"SELECT codigo, area, data FROM reservas WHERE {onde}", valores)]
        con.execute(f"UPDATE reservas SET ativa = 0, atualizada_em = ? WHERE {onde}", [_agora(), *valores])
        con.execute("COMMIT")
    return alvo


def autorizar_visitante(apartamento: str, nome: str, data: str) -> None:
    with conexao() as con:
        con.execute(
            "INSERT INTO visitantes (apartamento, nome, data, criada_em) VALUES (?, ?, ?, ?)",
            (apartamento, nome, data, _agora()),
        )
