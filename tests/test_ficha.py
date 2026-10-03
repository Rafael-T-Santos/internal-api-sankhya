"""Cálculos da ficha do televendas (série, ticket, frequência, "parou de comprar") — SEM banco.

O SQL (inclusive a eliminação de pedido duplicado pela TGFVAR) só se valida no
servidor, com scripts/conferir_ficha.py. Aqui o Oracle é simulado.
Rodar da raiz do repositório:  python tests/test_ficha.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import televendas_ficha as F  # noqa: E402

HOJE = datetime(2026, 10, 7)


class Cur:
    """Devolve `linhas` para a consulta principal e HOJE para a de data."""

    def __init__(self, linhas):
        self.linhas, self.r, self.sqls = linhas, None, []

    def execute(self, sql, binds=None):
        self.sqls.append((sql, binds))
        self.r = [(HOJE,)] if "TRUNC(SYSDATE) FROM DUAL" in sql else self.linhas

    def fetchone(self):
        return self.r[0]

    def fetchall(self):
        return self.r


ok = True


def chk(nome, cond):
    global ok
    ok &= bool(cond)
    print(("OK  " if cond else "FALHOU ") + nome)


CFG = {"tops": [1001, 1988], "codemp": 3, "atraso_max": 5}
nota = lambda nu, dia, valor: (nu, nu, datetime(2026, 9, dia) if dia > 0 else datetime(2026, 7, -dia), 1001, "PEDIDO", valor, "JOAO", "ANA")
cur = Cur([nota(3, 19, 1000.0), nota(2, 5, 500.0), nota(1, -20, 300.0), nota(4, 19, 200.0)])
r = F.compras(cur, 11842, CFG, 12)
chk("12 meses na série, do mais antigo ao atual", len(r["serie"]) == 12 and r["serie"][-1]["mes"] == "2026-10")
chk("soma por mês (set = 1000+500+200)", next(m for m in r["serie"] if m["mes"] == "2026-09")["valor"] == 1700.0)
chk("ticket médio = 2000/4", r["resumo"]["ticketMedio"] == 500.0)
# Datas distintas: 20/07, 05/09, 19/09 -> intervalos 47 e 14 -> média 30,5 -> 30 (round de Python arredonda o .5 para o par)
chk("frequência entre datas distintas", r["resumo"]["frequenciaDias"] in (30, 31))
chk("última compra", r["resumo"]["ultimaCompra"] == "2026-09-19")
chk("TOPs viram binds e a consulta pede a empresa", ":T0" in cur.sqls[0][0] and cur.sqls[0][1]["CODEMP"] == 3)
chk("dedup pela TGFVAR está no SQL", "TGFVAR" in F.SQL_COMPRAS and "TGFVAR" in F.SQL_MIX)

prod = lambda cod, compras, ultima: (cod, f"PROD {cod}", "UN", "M", 10, 100.0, compras, ultima)
cur = Cur([prod(1, 5, datetime(2026, 9, 30)), prod(2, 3, datetime(2026, 7, 1)), prod(3, 1, datetime(2026, 5, 1))])
m = F.mix(cur, 11842, CFG)
chk("data consultada ANTES do mix (senão descarta o resultado)", "SYSDATE" in cur.sqls[0][0] and "TGFITE" in cur.sqls[1][0])
chk("mais frequente primeiro", [p["codProd"] for p in m["produtos"]] == [1, 2, 3])
chk("parou: 3 compras e 98 dias sem comprar", [p["codProd"] for p in m["parouDeComprar"]] == [2])
chk("1 compra só não é hábito: não entra em 'parou'", not m["produtos"][2]["parou"])

cur = Cur([])
chk("cliente sem pedido", F.ultimo_pedido(cur, 1, CFG) == {"pedido": None})

print("TUDO OK" if ok else "HOUVE FALHA")
sys.exit(0 if ok else 1)
