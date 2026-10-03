"""Confere a lista adaptada contra a consulta ORIGINAL do administrador do Sankhya.

A consulta original entra pela entrada padrão (o arquivo está no repositório
televendas, que fica clonado no servidor). Rodar no servidor:

    cd ~/internal-api-sankhya
    docker compose exec -T api-sankhya python scripts/conferir_listas.py carteira 41 \
        < ~/televendas/docs/carteira_televendas_representantes.txt
    docker compose exec -T api-sankhya python scripts/conferir_listas.py interna \
        < ~/televendas/docs/clientes_televendas_geral.txt

Na carteira compara, para o televendas informado:
  1. escala DESLIGADA (P_APLICA_ESCALA_SN = 'N') x nossa lista completa;
  2. escala LIGADA x nossos liberados hoje (de segunda e domingo os dois ficam
     vazios por regra — rode num dia útil);
  3. o dia de visita de cada cliente;
  4. à parte, quem SAI por causa do PAR.CLIENTE = 'S' pedido pelo admin.
As comparações 1 a 3 rodam SEM o filtro novo: diferença ali é erro de adaptação.

Só lê. Não grava nada em lugar nenhum.
"""

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import televendas_listas as listas  # noqa: E402
from db import conectar_oracle  # noqa: E402
from pg import conectar_postgres  # noqa: E402

TROCAS_CARTEIRA = [
    ("NVL($P{P_APLICA_ESCALA_SN}, 'S')", ":APLICA"),
    ("$P{P_CODTELEVEND}", ":P_CODTELEVEND"),
    ("$P{P_CODCID}", "CAST(NULL AS NUMBER)"),
    ("$P{P_CODVEND}", "CAST(NULL AS NUMBER)"),
    ("$P{P_FILTRO_NOME}", "CAST(NULL AS VARCHAR2(100))"),
    ("STP_GET_CODUSULOGADO", "CAST(NULL AS NUMBER)"),
]
TROCAS_INTERNA = [
    ("NVL($P{P_SOMENTE_COM_VENDA_SN}, 'N')", "'N'"),
    ("$P{P_CODCID}", "CAST(NULL AS NUMBER)"),
    ("$P{P_DTULTINI}", "CAST(NULL AS DATE)"),
    ("$P{P_DTULTFIM}", "CAST(NULL AS DATE)"),
    ("$P{P_FILTRO_NOME}", "CAST(NULL AS VARCHAR2(100))"),
]


def adaptar(sql, trocas):
    for de, para in trocas:
        if de not in sql:
            sys.exit(f"A consulta recebida não tem '{de}'. É o arquivo certo?")
        sql = sql.replace(de, para)
    if "$P{" in sql:
        sys.exit("Sobrou parâmetro $P{...} sem tradução na consulta original.")
    return sql.strip().rstrip(";")


def original(cur, sql, binds):
    cur.execute(sql, binds)
    nomes = [d[0] for d in cur.description]
    return {int(r[nomes.index("CODPARC")]): dict(zip(nomes, r)) for r in cur.fetchall()}


def comparar(titulo, deles, nossos):
    so_deles = sorted(set(deles) - set(nossos))
    so_nossos = sorted(set(nossos) - set(deles))
    ok = not so_deles and not so_nossos
    print(f"\n[{'OK' if ok else 'DIFERENTE'}] {titulo}: original {len(deles)} · nossa {len(nossos)}")
    if so_deles:
        print(f"  só na original ({len(so_deles)}): {so_deles[:30]}{' …' if len(so_deles) > 30 else ''}")
    if so_nossos:
        print(f"  só na nossa ({len(so_nossos)}): {so_nossos[:30]}{' …' if len(so_nossos) > 30 else ''}")
    return ok


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("carteira", "interna"):
        sys.exit("Uso: conferir_listas.py carteira <CODTELEVEND> | interna   (consulta original pela entrada padrão)")
    lista = sys.argv[1].upper()
    sql_original = sys.stdin.read()

    ora, pg = conectar_oracle(), conectar_postgres()
    if not ora:
        sys.exit("Sem conexão com o Oracle.")
    tudo_ok = True
    try:
        cur, cur_pg = ora.cursor(), pg.cursor()
        config = listas.ler_configuracao(cur_pg)

        if lista == "INTERNA":
            deles = original(cur, adaptar(sql_original, TROCAS_INTERNA), {})
            _, _, nossos = listas.buscar_clientes(cur, "INTERNA", config)
            tudo_ok &= comparar("Fila interna", deles, {c["codParc"]: c for c in nossos})
        else:
            if len(sys.argv) < 3:
                sys.exit("Informe o CODVEND do televendas: conferir_listas.py carteira 41")
            codtelevend = int(sys.argv[2])
            sql = adaptar(sql_original, TROCAS_CARTEIRA)
            _, dia_semana, nossos = listas.buscar_clientes(cur, "CARTEIRA", config, codtelevend, so_clientes=False)
            escala = listas.ler_escala(cur_pg, {c["codVendExterno"] for c in nossos})
            listas.anexar_rota(nossos, escala, dia_semana)
            todos = {c["codParc"]: c for c in nossos}
            liberados = {k: c for k, c in todos.items() if c["rota"]["liberado"]}

            sem_escala = original(cur, sql, {"P_CODTELEVEND": codtelevend, "APLICA": "N"})
            com_escala = original(cur, sql, {"P_CODTELEVEND": codtelevend, "APLICA": "S"})
            tudo_ok &= comparar("Carteira completa (escala desligada)", sem_escala, todos)
            tudo_ok &= comparar(f"Liberados hoje (dia da semana {dia_semana})", com_escala, liberados)

            difs = [
                (k, sem_escala[k]["DIA_VISITA_DESC"], todos[k]["rota"]["diaVisitaDesc"])
                for k in set(sem_escala) & set(todos)
                if sem_escala[k]["DIA_VISITA_DESC"] != todos[k]["rota"]["diaVisitaDesc"]
            ]
            tudo_ok &= not difs
            print(f"\n[{'OK' if not difs else 'DIFERENTE'}] Dia de visita por cliente: {len(difs)} diferença(s)")
            for k, d, n in sorted(difs)[:30]:
                print(f"  {k}: original {d} · nossa {n}")

            _, _, filtrados = listas.buscar_clientes(cur, "CARTEIRA", config, codtelevend, so_clientes=True)
            saem = sorted(set(todos) - {c["codParc"] for c in filtrados})
            print(f"\n[INFO] Saem pelo CLIENTE = 'S' (decisão do admin, não é erro): {len(saem)}")
            for k in saem[:30]:
                print(f"  {k} {todos[k]['fantasia']}")
    finally:
        ora.close()
        pg.close()

    print("\nRESULTADO:", "tudo bate." if tudo_ok else "HÁ DIFERENÇAS — não publicar antes de explicar cada uma.")
    sys.exit(0 if tudo_ok else 1)


if __name__ == "__main__":
    main()
