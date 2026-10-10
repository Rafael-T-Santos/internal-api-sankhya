"""Gerência do televendas (Ao vivo e indicadores) — SEM banco.
Rodar da raiz do repositório:  python tests/test_gerencia.py
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import televendas_gerencia as G  # noqa: E402
from televendas_config import Invalido  # noqa: E402

TZ = timezone(timedelta(hours=-3))
AGORA = datetime(2026, 10, 10, 14, 30, tzinfo=TZ)


class Pg:
    """Responde cada consulta pelo primeiro trecho do SQL que casar em `respostas`."""

    def __init__(self, respostas):
        self.respostas, self.sqls, self.r = respostas, [], []

    def execute(self, sql, vals=None):
        self.sqls.append((sql, vals))
        self.r = next((v for k, v in self.respostas if k in sql), [])

    def fetchone(self):
        return self.r[0] if self.r else None

    def fetchall(self):
        return self.r


class Ora:
    def __init__(self, perfis, usuarios, parceiros):
        self.perfis, self.usuarios, self.parceiros, self.r = perfis, usuarios, parceiros, []

    def execute(self, sql, binds=None):
        ids = list((binds or {}).values())
        if "AD_PERFILTVL" in sql:
            self.r = self.perfis
        elif "TSIUSU" in sql:
            self.r = [(i, self.usuarios[i]) for i in ids if i in self.usuarios]
        elif "TGFPAR" in sql:
            self.r = [(i, *self.parceiros[i]) for i in ids if i in self.parceiros]

    def fetchall(self):
        return self.r


ok = True


def chk(nome, cond):
    global ok
    ok &= bool(cond)
    print(("OK  " if cond else "FALHOU ") + nome)


def recusa(fn):
    try:
        fn()
    except Invalido:
        return True
    return False


# ---------------------------------------------------------------- filtros
chk("filtros: datas obrigatórias", recusa(lambda: G.filtros({})))
chk("filtros: de depois de ate", recusa(lambda: G.filtros({"de": "2026-10-10", "ate": "2026-10-01"})))
chk("filtros: período longo demais", recusa(lambda: G.filtros({"de": "2025-01-01", "ate": "2026-10-01"})))
chk("filtros: lista inválida", recusa(lambda: G.filtros({"de": "2026-10-01", "ate": "2026-10-10", "lista": "X"})))
chk("filtros: codUsu não numérico", recusa(lambda: G.filtros({"de": "2026-10-01", "ate": "2026-10-10", "codUsu": "a"})))
chk(
    "filtros: válidos",
    G.filtros({"de": "2026-10-01", "ate": "2026-10-10", "codUsu": "25", "lista": "INTERNA"})
    == (date(2026, 10, 1), date(2026, 10, 10), 25, "INTERNA"),
)
chk("filtros: vazios viram None", G.filtros({"de": "2026-10-01", "ate": "2026-10-01", "codUsu": "", "lista": ""})[2:] == (None, None))

# ---------------------------------------------------------------- ao vivo
pg = Pg(
    [
        ("GROUP BY codusu", [(25, "ANA", 12, 8, 2, 1, AGORA - timedelta(minutes=5)), (99, "SEM PERFIL", 3, 1, 0, 0, AGORA)]),
        ("DISTINCT ON (codusu)", [(25, 11842, AGORA - timedelta(minutes=2), "INTERNA")]),
        ("SELECT now()", [(AGORA,)]),
    ]
)
ora = Ora([(25, "OPERADOR"), (30, "GERENTE")], {25: "Ana Souza", 30: "Bruno"}, {11842: (" MERCADINHO ", "MERCADO LTDA")})
r = G.ao_vivo(pg, ora)
ops = {o["codUsu"]: o for o in r["operadores"]}
chk("ao vivo: perfil ativo sem ligação aparece zerado", ops[30]["hoje"]["ligacoes"] == 0 and ops[30]["emChamada"] is None)
chk("ao vivo: quem ligou hoje sem perfil continua na tela", 99 in ops and ops[99]["nomeUsu"] == "SEM PERFIL" and ops[99]["perfil"] is None)
chk("ao vivo: números de hoje", ops[25]["hoje"] == {"ligacoes": 12, "atendidas": 8, "vendas": 2, "orcamentos": 1})
chk(
    "ao vivo: ligação aberta com nome do cliente",
    ops[25]["emChamada"]["codParc"] == 11842 and ops[25]["emChamada"]["fantasia"] == "MERCADINHO",
)
chk("ao vivo: nome do Oracle vence o copiado na ligação", ops[25]["nomeUsu"] == "Ana Souza")
chk("ao vivo: ordem por nome", [o["codUsu"] for o in r["operadores"]] == [25, 30, 99])
chk("ao vivo: hora do servidor", r["agora"] == AGORA.isoformat())
chk("ao vivo: só reservas que não expiraram", any("expira > now()" in s for s, _ in pg.sqls))

# ---------------------------------------------------------------- indicadores
pg = Pg(
    [
        ("GROUP BY m.id", [(1, "Estoque abastecido", 25, "ANA", 4), (1, "Estoque abastecido", 26, "BIA", 6), (2, "Preço", 25, "ANA", 3)]),
        ("GROUP BY c.codusu", [(25, "ANA", 20, 12, 3, 2, 18, 5, 245.6, 1), (26, "BIA", 30, 20, 5, 1, 25, 5, None, 0)]),
        ("GROUP BY 1 ORDER BY 1", [(date(2026, 10, 6), 22, 14), (date(2026, 10, 7), 28, 18)]),
        ("c.desfecho IS NOT NULL", [("SEM_COMPRA", 13), ("VENDA", 8)]),
        ("SELECT c.resultado", [("ATENDEU", 32), ("NAO_ATENDEU", 18)]),
        ("SELECT count(*),", [(50, 32, 8, 3, 43, 210.4, 13, 1)]),
    ]
)
r = G.indicadores(pg, date(2026, 10, 6), date(2026, 10, 10), None, "CARTEIRA")
chk("indicadores: só finalizadas e no período", all("c.situacao = 'FINALIZADA'" in s for s, _ in pg.sqls))
chk("indicadores: filtro de lista vai como parâmetro", all(v == [date(2026, 10, 6), date(2026, 10, 10), "CARTEIRA"] for _, v in pg.sqls))
chk("indicadores: operador com mais ligações primeiro", [o["codUsu"] for o in r["porOperador"]] == [26, 25])
chk("indicadores: duração arredondada e nula sem atendidas", r["porOperador"][1]["duracaoMediaSeg"] == 246 and r["porOperador"][0]["duracaoMediaSeg"] is None)
chk("indicadores: totais", r["totais"]["ligacoes"] == 50 and r["totais"]["semCompraSemMotivo"] == 1 and r["totais"]["duracaoMediaSeg"] == 210)
chk("indicadores: por dia em ISO", r["porDia"][0] == {"dia": "2026-10-06", "ligacoes": 22, "atendidas": 14})
chk("indicadores: motivo soma os operadores", r["motivos"][0]["descricao"] == "Estoque abastecido" and r["motivos"][0]["n"] == 10)
chk("indicadores: operador que mais marcou o motivo primeiro", [o["codUsu"] for o in r["motivos"][0]["porOperador"]] == [26, 25])
chk("indicadores: ranking de motivos", [m["n"] for m in r["motivos"]] == [10, 3])

pg = Pg([("SELECT count(*),", [(0, 0, 0, 0, 0, None, 0, 0)])])
G.indicadores(pg, date(2026, 10, 1), date(2026, 10, 1), 25, None)
chk("indicadores: filtro de operador", all("c.codusu = %s" in s and v == [date(2026, 10, 1), date(2026, 10, 1), 25] for s, v in pg.sqls))

print("\nTUDO OK" if ok else "\nHÁ FALHAS")
sys.exit(0 if ok else 1)
