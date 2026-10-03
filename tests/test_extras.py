"""Validações da Fase 5 do televendas (metas, campanhas, roteiros) — SEM banco.
Rodar da raiz do repositório:  python tests/test_extras.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import televendas_extras as E  # noqa: E402
from televendas_config import Invalido  # noqa: E402


class Pg:
    def __init__(self):
        self.sqls, self.r, self.rowcount = [], [], 1

    def execute(self, sql, vals=None):
        self.sqls.append((sql, vals))
        self.r = [(42,)] if "RETURNING id" in sql else [(1,)] if "FROM campanha WHERE id" in sql else []

    def fetchone(self):
        return self.r[0] if self.r else None

    def fetchall(self):
        return self.r


class Ora:
    """Produtos existentes: 100 e 200."""

    def __init__(self):
        self.r = []

    def execute(self, sql, binds=None):
        self.r = [(v, f"PROD {v}", "UN") for v in (binds or {}).values() if v in (100, 200)]

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


chk("competência inválida", recusa(lambda: E.salvar_meta(Pg(), {"codUsu": 25, "competencia": "202613"})))
chk("meta negativa", recusa(lambda: E.salvar_meta(Pg(), {"codUsu": 25, "competencia": "202610", "ligacoesDia": -1})))
pg = Pg()
E.salvar_meta(pg, {"codUsu": 25, "competencia": "202610", "ligacoesDia": "40", "valorVenda": "", "positivacao": 30})
chk("meta grava com upsert e campo vazio = sem meta", "ON CONFLICT" in pg.sqls[0][0] and pg.sqls[0][1] == (25, "202610", 40, None, 30))

base = {"titulo": "Semana do selador", "inicio": "2026-10-05", "fim": "2026-10-10", "lista": "AMBAS"}
chk("campanha com fim antes do início", recusa(lambda: E.salvar_campanha(Pg(), Ora(), {**base, "fim": "2026-10-01"})))
chk("campanha sem título", recusa(lambda: E.salvar_campanha(Pg(), Ora(), {**base, "titulo": " "})))
chk("campanha com lista inválida", recusa(lambda: E.salvar_campanha(Pg(), Ora(), {**base, "lista": "TODAS"})))
chk("produto que não existe no Sankhya", recusa(lambda: E.salvar_campanha(Pg(), Ora(), {**base, "produtos": [100, 999]})))
pg = Pg()
chk("campanha válida", E.salvar_campanha(pg, Ora(), {**base, "produtos": [200, 100, 100]}) == 42)
chk("produtos sem repetição", sum(1 for s, _ in pg.sqls if "INSERT INTO campanha_produto" in s) == 2)
pg = Pg()
E.salvar_campanha(pg, Ora(), {**base, "produtos": [100]}, id_=7)
chk("alterar campanha substitui os produtos", any("DELETE FROM campanha_produto" in s for s, _ in pg.sqls))

chk("roteiro sem texto", recusa(lambda: E.salvar_roteiro(Pg(), {"titulo": "Abertura", "texto": ""})))
chk("roteiro válido", E.salvar_roteiro(Pg(), {"titulo": "Abertura", "texto": "Bom dia...", "lista": "CARTEIRA"}) == 42)

print("TUDO OK" if ok else "HOUVE FALHA")
sys.exit(0 if ok else 1)
