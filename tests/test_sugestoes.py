"""Aba Sugeridos (recomendação comercial do admin) — SEM banco.

O ranking só se valida no servidor, contra a consulta original, com
scripts/conferir_sugestoes.py. Aqui o Oracle é simulado.
Rodar da raiz do repositório:  python tests/test_sugestoes.py
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import televendas_sugestoes as S  # noqa: E402
import scripts.conferir_sugestoes as C  # noqa: E402


class Cur:
    def __init__(self, linhas):
        self.linhas, self.sqls = linhas, []

    def execute(self, sql, binds=None):
        self.sqls.append((sql, binds))

    def fetchall(self):
        return self.linhas


ok = True


def chk(nome, cond):
    global ok
    ok &= bool(cond)
    print(("OK  " if cond else "FALHOU ") + nome)


cruzada = (1, 4521, " PARAFUSO X ", "CX", Decimal("87.5"), Decimal("54.9"), Decimal("3.2"), Decimal("120"),
           "VENDA_CRUZADA", "ULTIMA_NOTA", 789, "BUCHA Y", "Complemento potencial da ultima nota 1 ...", None, None, 0)
recompra = (2, 300, "FITA", "UN", Decimal("61"), None, None, Decimal("8"),
            "RECOMPRA", "RECOMPRA", None, None, "Recompra: ...", 25, Decimal("21.5"), 5)
cur = Cur([cruzada, recompra])
r = S.sugestoes(cur, 11842, {"tops": [1001], "codemp": 3})
p1, p2 = r["produtos"]

chk("só o parceiro vai de bind", cur.sqls[0][1] == {"P_CODPARC": 11842})
chk("na ordem da consulta", [p["posicao"] for p in r["produtos"]] == [1, 2])
chk("texto aparado e números crus", p1["descricao"] == "PARAFUSO X" and p1["potencial"] == 87.5 and p1["precoAtual"] == 54.9)
chk("venda cruzada traz o produto-base", p1["codProdBase"] == 789 and p1["descricaoBase"] == "BUCHA Y" and p1["contexto"] == "ULTIMA_NOTA")
chk("item nunca comprado = 0 notas", p1["notasDoItem"] == 0)
chk("sem histórico de preço = null", p2["variacaoPreco"] is None and p2["precoAtual"] is None)
chk("recompra: intervalo e dias sem comprar", p2["intervaloMedioDias"] == 21.5 and p2["diasSemComprar"] == 25 and p2["codProdBase"] is None)
chk("cliente sem sugestão", S.sugestoes(Cur([]), 103, None) == {"produtos": []})

sql = S.SQL_SUGESTOES
chk("lógica do admin presente", all(t in sql for t in (
    "AD_ADPARVENDASCESTA", "AD_REGRASASSOCPROD", "AD_MRC_PAR", "AD_MRC_FAMILIA",
    ":P_CODPARC <> 103", "POSICAO_PAINEL <= 15", "SALDO_APOS_RESERVA > 0")))
chk("ramo da empresa 2 fora da API", "CODEMP = 2" not in sql and "1102" not in sql)
chk("conferidor compara com a original inteira (ramo da empresa 2 de volta)",
    "C.CODEMP = 2 AND C.CODTIPOPER IN (1102, 1121)" in C.SQL_ORIGINAL and "POTENCIAL_COMPRA" in C.SQL_ORIGINAL)
chk("nosso SELECT parte dos CTEs da API", sql.startswith(S.SQL_CTES))
chk("SQL não termina em ponto e vírgula (cx_Oracle recusa)", not sql.rstrip().endswith(";") and not C.SQL_ORIGINAL.rstrip().endswith(";"))

# O conferidor reproduz o texto da original: TO_CHAR(..., 'FM9990D00') || '%'.
chk("texto do potencial", C.pct(87.5) == "87,50%" and C.pct(7.0) == "7,00%")
chk("texto da variação", C.variacao_txt(3.2) == "+3,20%" and C.variacao_txt(-1) == "-1,00%"
    and C.variacao_txt(0) == "0,00%" and C.variacao_txt(None) == "SEM HISTORICO")

print("TUDO OK" if ok else "HOUVE FALHA")
sys.exit(0 if ok else 1)
