"""Aplica as migrações do schema `televendas` no Postgres.

Rodar DENTRO do container da API (é ele que tem as variáveis PG_*):

    docker compose exec api-sankhya python scripts/migrar.py            # aplica as pendentes
    docker compose exec api-sankhya python scripts/migrar.py --status   # só lista

Cada arquivo de migrations/televendas/ roda UMA vez, em ordem de nome, numa
transação própria: se falhar no meio, nada daquele arquivo fica gravado e as
seguintes não rodam. O que já rodou fica registrado em televendas.migracao.
Migração aplicada não se edita: corrige-se com uma nova.
"""

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from pg import conectar_postgres  # noqa: E402

PASTA = os.path.join(RAIZ, "migrations", "televendas")


def main():
    so_status = "--status" in sys.argv
    arquivos = sorted(a for a in os.listdir(PASTA) if a.endswith(".sql"))

    conexao = conectar_postgres()
    try:
        with conexao, conexao.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS migracao (
                    nome       text PRIMARY KEY,
                    aplicada   timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute("SELECT nome, aplicada FROM migracao")
            feitas = dict(cur.fetchall())

        pendentes = [a for a in arquivos if a not in feitas]
        for a in arquivos:
            quando = feitas.get(a)
            print(f"  {'OK ' if quando else '...'} {a}" + (f"  ({quando:%d/%m/%Y %H:%M})" if quando else ""))
        if so_status or not pendentes:
            print("Nada a aplicar." if not pendentes else f"{len(pendentes)} pendente(s).")
            return

        for a in pendentes:
            with open(os.path.join(PASTA, a), encoding="utf-8") as f:
                sql = f.read()
            # `with conexao` = uma transação: commit no fim, rollback se levantar.
            with conexao, conexao.cursor() as cur:
                cur.execute(sql)
                cur.execute("INSERT INTO migracao (nome) VALUES (%s)", (a,))
            print(f"  aplicada {a}")
        print("Pronto.")
    finally:
        conexao.close()


if __name__ == "__main__":
    main()
