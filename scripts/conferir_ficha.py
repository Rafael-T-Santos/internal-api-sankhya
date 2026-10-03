"""Mostra a ficha de um cliente (compras, mix, último pedido) para conferir no Sankhya.

Imprime também quais notas foram DESCARTADAS como duplicadas (pedido que virou
nota, ligados pela TGFVAR), para conferir se a eliminação está certa.
Só lê. Rodar no servidor:

    docker compose exec api-sankhya python scripts/conferir_ficha.py <CODPARC>

Para achar um cliente que TENHA o caso pedido -> nota (e ver a eliminação de
duplicados funcionando), sem depender de alguém conhecer um:

    docker compose exec api-sankhya python scripts/conferir_ficha.py --achar
"""

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import televendas_ficha as F  # noqa: E402
import televendas_listas as L  # noqa: E402
from db import conectar_oracle  # noqa: E402
from pg import conectar_postgres  # noqa: E402

SQL_TODAS = "WITH" + F._NOTAS_EFETIVAS.replace("{inicio}", F.INICIO_MESES) + """
SELECT N.NUNOTA, N.DTNEG, N.CODTIPOPER, N.VLRNOTA,
       CASE WHEN N.NUNOTA IN (SELECT NUNOTA FROM NOTAS_EFETIVAS) THEN 'conta' ELSE 'DESCARTADA' END
  FROM NOTAS N
 ORDER BY N.DTNEG DESC, N.NUNOTA DESC
"""


# Clientes com uma nota (nas TOPs) que nasceu de outra nota/pedido (também nas TOPs)
# nos últimos 90 dias: exatamente o caso que a eliminação pela TGFVAR resolve.
SQL_ACHAR = """
SELECT * FROM (
    SELECT ORIG.CODPARC, PAR.NOMEPARC, COUNT(DISTINCT ORIG.NUNOTA) AS CASOS
      FROM TGFVAR VAR
      JOIN TGFCAB ORIG  ON ORIG.NUNOTA = VAR.NUNOTAORIG
      JOIN TGFCAB FILHA ON FILHA.NUNOTA = VAR.NUNOTA
      JOIN TGFPAR PAR   ON PAR.CODPARC = ORIG.CODPARC
     WHERE ORIG.CODEMP = :CODEMP
       AND ORIG.CODTIPOPER IN ({tops})
       AND FILHA.CODTIPOPER IN ({tops})
       AND ORIG.DTNEG >= TRUNC(SYSDATE) - 90
     GROUP BY ORIG.CODPARC, PAR.NOMEPARC
     ORDER BY CASOS DESC
) WHERE ROWNUM <= 10
"""


def brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if v is not None else "—"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    achar = sys.argv[1] == "--achar"
    codparc = None if achar else int(sys.argv[1])
    pg = conectar_postgres()
    try:
        config = L.ler_configuracao(pg.cursor())
    finally:
        pg.close()
    ora = conectar_oracle()
    if not ora:
        sys.exit("Sem conexão com o Oracle.")
    try:
        cur = ora.cursor()
        if achar:
            tops, binds = F._binds_tops(config)
            binds["CODEMP"] = config["codemp"]
            cur.execute(SQL_ACHAR.replace("{tops}", tops), binds)
            achados = cur.fetchall()
            if not achados:
                print("Nenhum cliente com pedido virando nota (dentro das TOPs) nos últimos 90 dias.")
                print("Ou seja: hoje não há duplicidade para eliminar, e a regra não muda nenhum total.")
            for cod, nome, casos in achados:
                print(f"  {cod:>7}  {(nome or '').strip()[:45]:45}  {casos} pedido(s) que viraram nota")
            if achados:
                print(f"\nConfira um deles: python scripts/conferir_ficha.py {achados[0][0]}")
            return
        tops, binds = F._binds_tops(config)
        binds.update({"CODEMP": config["codemp"], "CODPARC": codparc, "MESES": 12})
        cur.execute(SQL_TODAS.replace("{tops}", tops), binds)
        print(f"\nNOTAS DOS ÚLTIMOS 12 MESES (TOPs {config['tops']}):")
        notas = cur.fetchall()
        for nunota, dtneg, top, valor, situ in notas:
            extra = ""
            if situ == "DESCARTADA":
                # Consulta à parte (e não LISTAGG DISTINCT, que só existe do Oracle 19c em diante).
                cur.execute("SELECT DISTINCT NUNOTA FROM TGFVAR WHERE NUNOTAORIG = :N ORDER BY 1", {"N": nunota})
                extra = "  -> virou " + ", ".join(str(r[0]) for r in cur.fetchall())
            print(f"  {situ:10} NUNOTA {nunota}  {dtneg:%d/%m/%Y}  TOP {top}  {brl(valor)}{extra}")

        c = F.compras(cur, codparc, config, 12)
        r = c["resumo"]
        print(f"\nCOMPRAS (12 meses): {r['qtdNotas']} notas · total {brl(r['valorTotal'])} · ticket {brl(r['ticketMedio'])}"
              f" · compra a cada {r['frequenciaDias']} dias · última {r['ultimaCompra']}")
        print("  " + "  ".join(f"{m['mes']}:{brl(m['valor'])}" for m in c["serie"] if m["notas"]))

        m = F.mix(cur, codparc, config)
        print(f"\nMIX ({m['totalProdutos']} produtos em {m['janelaDias']} dias; mostrando 10):")
        for p in m["produtos"][:10]:
            print(f"  {p['codProd']:>7} {p['descricao'][:40]:40} {p['compras']}x  qtd {p['quantidade']}  última {p['ultimaCompra']}"
                  + ("  PAROU" if p["parou"] else ""))
        print(f"  parou de comprar ({m['regraParou']}): {len(m['parouDeComprar'])}")

        u = F.ultimo_pedido(cur, codparc, config)["pedido"]
        if u:
            print(f"\nÚLTIMO PEDIDO: NUNOTA {u['nunota']} · {u['dtNeg']} · TOP {u['codTipOper']} {u['descrOper']} · "
                  f"{brl(u['valor'])} · {len(u['itens'])} itens · OC {u['ordemCarga']}")
        else:
            print("\nÚLTIMO PEDIDO: nenhum")
    finally:
        ora.close()


if __name__ == "__main__":
    main()
