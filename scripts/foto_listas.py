"""Foto diária das listas do televendas (televendas.foto_dia / foto_lista).

Grava, para a data de hoje (relógio do Oracle), quem estava na carteira de cada
televendas — com o representante e se a escala liberava o cliente — e quem
estava na fila interna. É a base da positivação dos dashboards (Fase 4).

Usa EXATAMENTE a regra das listas (televendas_listas), sem contatos nem
enriquecimento. Rodar uma vez por dia, de manhã (cron no host):

    0 10 * * 1-6 docker exec api_sankhya python scripts/foto_listas.py >> $HOME/backups/televendas/foto.log 2>&1

(10:00 UTC = 07:00 em Maceió, de segunda a sábado.) Rodar de novo no mesmo dia
não duplica: a foto do dia é refeita do zero, numa transação.
"""

import os
import sys
from datetime import datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import televendas_listas as L  # noqa: E402
from db import conectar_oracle  # noqa: E402
from pg import conectar_postgres  # noqa: E402

# Todos os televendas que têm algum representante apontando para eles.
SQL_TELEVENDAS = """
    SELECT DISTINCT V.AD_CODVEND FROM TGFVEN V
     WHERE NVL(V.AD_CODVEND, 0) > 0 AND V.ATIVO = 'S'
     ORDER BY 1
"""


def main():
    inicio = datetime.now()
    ora = conectar_oracle()
    if not ora:
        sys.exit("Sem conexão com o Oracle.")
    pg = conectar_postgres()
    try:
        cur, cur_pg = ora.cursor(), pg.cursor()
        config = L.ler_configuracao(cur_pg)
        cur.execute(SQL_TELEVENDAS)
        televendas = [int(r[0]) for r in cur.fetchall()]

        linhas, hoje = [], None
        for tv in televendas:
            hoje, dia_semana, clientes = L.buscar_clientes(cur, "CARTEIRA", config, tv)
            escala = L.ler_escala(cur_pg, {c["codVendExterno"] for c in clientes})
            L.anexar_rota(clientes, escala, dia_semana)
            linhas += [("CARTEIRA", tv, c["codParc"], c["codVendExterno"], c["rota"]["liberado"]) for c in clientes]
        hoje, _, internos = L.buscar_clientes(cur, "INTERNA", config)
        linhas += [("INTERNA", 0, c["codParc"], None, True) for c in internos]

        with pg, pg.cursor() as w:
            w.execute("DELETE FROM foto_dia WHERE data = %s", (hoje,))  # refaz o dia (CASCADE apaga as linhas)
            w.execute("INSERT INTO foto_dia (data, carteiras, clientes) VALUES (%s, %s, %s)", (hoje, len(televendas), len(linhas)))
            w.executemany(
                """INSERT INTO foto_lista (data, lista, codvend, codparc, codvend_ext, liberado)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                [(hoje, *l) for l in linhas],
            )
        cart = sum(1 for l in linhas if l[0] == "CARTEIRA")
        print(
            f"{datetime.now():%Y-%m-%d %H:%M} foto de {hoje}: {len(televendas)} carteira(s), {cart} clientes de carteira "
            f"({sum(1 for l in linhas if l[0] == 'CARTEIRA' and l[4])} liberados), {len(internos)} na fila interna "
            f"— {(datetime.now() - inicio).seconds}s"
        )
    finally:
        ora.close()
        pg.close()


if __name__ == "__main__":
    main()
