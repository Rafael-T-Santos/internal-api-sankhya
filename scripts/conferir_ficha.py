"""Mostra a ficha de um cliente (compras, mix, último pedido) para conferir no Sankhya.

Imprime também quais notas foram DESCARTADAS como duplicadas (pedido que virou
nota, ligados pela TGFVAR), para conferir se a eliminação está certa.
Só lê. Rodar no servidor:

    docker compose exec api-sankhya python scripts/conferir_ficha.py <CODPARC>
"""

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import televendas_ficha as F  # noqa: E402
import televendas_listas as L  # noqa: E402
from db import conectar_oracle  # noqa: E402
from pg import conectar_postgres  # noqa: E402

SQL_TODAS = "WITH" + F._NOTAS_EFETIVAS + """
SELECT N.NUNOTA, N.DTNEG, N.CODTIPOPER, N.VLRNOTA,
       CASE WHEN N.NUNOTA IN (SELECT NUNOTA FROM NOTAS_EFETIVAS) THEN 'conta' ELSE 'DESCARTADA' END
  FROM NOTAS N
 ORDER BY N.DTNEG DESC, N.NUNOTA DESC
"""


def brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if v is not None else "—"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    codparc = int(sys.argv[1])
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
        tops, binds = F._binds_tops(config)
        binds.update({"CODEMP": config["codemp"], "CODPARC": codparc, "DIAS": 12 * 31})
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
