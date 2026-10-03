"""Conexão com o Postgres do televendas (schema `televendas`).

O banco é o Postgres do check-my-load no nd-db-02, alcançado pela porta do
host. O usuário `televendas` é dono só do próprio schema e não enxerga as
tabelas do check-my-load.

Fuso: o servidor do Postgres roda em UTC. Toda coluna de data/hora é
timestamptz, e a sessão é aberta em America/Maceio, então `now()::date`,
`current_date` e a conversão para texto já saem no horário de quem usa o
sistema — e batem com as datas do Sankhya (o Oracle roda em UTC-3).
"""

import os

import psycopg2
import psycopg2.extras

_OBRIGATORIAS = ("PG_HOST", "PG_DB", "PG_USER", "PG_PASS")


class PostgresNaoConfigurado(RuntimeError):
    """Faltam variáveis PG_* no ambiente do container."""


def conectar_postgres():
    """Abre uma conexão nova. Quem chama fecha (`with` ou `finally`).

    Levanta PostgresNaoConfigurado se faltar variável, e psycopg2.Error se o
    banco recusar — diferente do conectar_oracle, que devolve None: aqui o
    chamador precisa saber o MOTIVO para responder 503 em vez de 500.
    """
    faltando = [v for v in _OBRIGATORIAS if not os.environ.get(v)]
    if faltando:
        raise PostgresNaoConfigurado(
            "Postgres do televendas não configurado. Faltam no .env: " + ", ".join(faltando)
        )
    return psycopg2.connect(
        host=os.environ["PG_HOST"],
        port=int(os.environ.get("PG_PORT") or 5432),
        dbname=os.environ["PG_DB"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASS"],
        connect_timeout=5,
        application_name="api_sankhya",
        options="-c search_path=televendas -c timezone=America/Maceio",
    )


def cursor_dict(conexao):
    """Cursor que devolve cada linha como dict (coluna -> valor)."""
    return conexao.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
