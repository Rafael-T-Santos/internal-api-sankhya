"""Rotas de configuração da gerência do televendas — SEM banco.

Postgres e Oracle são simulados. Confere: só GERENTE entra, validações viram 400
e desfazem a transação, quem alterou é gravado, e a API não deixa as listas sem
TOP de última compra.
Rodar da raiz do repositório:  python tests/test_config.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AUTH_SECRET"] = "x" * 40
import app as appmod  # noqa: E402
import auth  # noqa: E402
import televendas  # noqa: E402

estado = {"perfil": "GERENTE", "tops_ativas": 1, "commits": 0, "rollbacks": 0, "sqls": []}


class CurOra:
    def __init__(self):
        self.r = []

    def execute(self, sql, binds=None):
        if "AD_PERFILTVL" in sql:
            self.r = [(estado["perfil"], "S")]
        elif "TGFVEN T" in sql:  # vendedor do usuário (sessão)
            self.r = [(41, "ANA")]
        elif "FROM TGFVEN V WHERE V.CODVEND IN" in sql:
            self.r = [(int(v), "REP", 41) for v in binds.values() if int(v) != 999]
        elif "FROM TSICID CID" in sql and "IN (" in sql:
            self.r = [(int(v), "CIDADE", "AL") for v in binds.values()]
        elif "FROM TGFTOP" in sql:
            self.r = [(int(v), "OPERACAO") for v in binds.values() if int(v) != 9999]
        else:
            self.r = []

    def fetchone(self):
        return self.r[0] if self.r else None

    def fetchall(self):
        return self.r


class Ora:
    def cursor(self):
        return CurOra()

    def close(self):
        pass


class CurPg:
    rowcount = 1

    def __init__(self):
        self.r = []

    def execute(self, sql, vals=None):
        estado["sqls"].append((sql, vals))
        if "SELECT dia_visita FROM escala" in sql:
            self.r = []
        elif "RETURNING id" in sql:
            self.r = [(77,)]
        elif "count(*) FROM top" in sql:
            self.r = [(estado["tops_ativas"],)]
        else:
            self.r = []

    def fetchone(self):
        return self.r[0] if self.r else None

    def fetchall(self):
        return self.r


class Pg:
    def cursor(self):
        return CurPg()

    def __enter__(self):
        return self

    def __exit__(self, tipo, *_):
        estado["rollbacks" if tipo else "commits"] += 1
        return False

    def close(self):
        pass


televendas.conectar_oracle = lambda: Ora()
televendas.conectar_postgres = lambda: Pg()

c = appmod.app.test_client()
H = {"Authorization": "Bearer " + auth.emitir_token(25, "RAFAEL")}
ok = True


def chk(nome, cond):
    global ok
    ok &= bool(cond)
    print(("OK  " if cond else "FALHOU ") + nome)


estado["perfil"] = "OPERADOR"
chk("operador não abre a configuração (403)", c.get("/api/televendas/config/tops", headers=H).status_code == 403)
estado["perfil"] = "GERENTE"

r = c.post("/api/televendas/config/escala", headers=H, json={"codVend": 34, "diaVisita": 3, "tipoLocal": "C", "codCid": 2900})
chk("cria linha da escala (201)", r.status_code == 201 and r.json["id"] == 77)
ins = [v for s, v in estado["sqls"] if "INSERT INTO escala" in s][-1]
chk("grava quem criou", ins[-1] == "RAFAEL (25)")

r = c.post("/api/televendas/config/escala", headers=H, json={"codVend": 999, "diaVisita": 3, "tipoLocal": "C", "codCid": 1})
chk("vendedor inexistente no Sankhya -> 400", r.status_code == 400 and "999" in r.json["erro"])
r = c.post("/api/televendas/config/escala", headers=H, json={"codVend": 34, "diaVisita": 6, "tipoLocal": "C", "codCid": 1})
chk("dia fora de seg–sex -> 400", r.status_code == 400)
r = c.put("/api/televendas/config/escala/5", headers=H, json={})
chk("alteração vazia -> 400", r.status_code == 400)

r = c.post("/api/televendas/config/tops", headers=H, json={"codTipOper": 9999})
chk("TOP inexistente no Sankhya -> 400", r.status_code == 400)
r = c.put("/api/televendas/config/tops/1001", headers=H, json={"conversao": "VENDA"})
chk("conversão inválida -> 400", r.status_code == 400)

antes = estado["rollbacks"]
estado["tops_ativas"] = 0
r = c.put("/api/televendas/config/tops/1988", headers=H, json={"ultimaCompra": False})
chk("tirar a última TOP de última compra -> 400 e rollback", r.status_code == 400 and estado["rollbacks"] == antes + 1)
estado["tops_ativas"] = 1
r = c.put("/api/televendas/config/tops/1988", headers=H, json={"ultimaCompra": False})
chk("com outra TOP restando -> 200", r.status_code == 200)

chk("parâmetro fora da faixa -> 400", c.put("/api/televendas/config/parametros/TRAVA_MINUTOS", headers=H, json={"valor": 1}).status_code == 400)
chk("parâmetro desconhecido -> 400", c.put("/api/televendas/config/parametros/OUTRO", headers=H, json={"valor": 1}).status_code == 400)
chk("parâmetro válido -> 200", c.put("/api/televendas/config/parametros/JANELA_ATRIBUICAO_DIAS", headers=H, json={"valor": 5}).status_code == 200)

print("TUDO OK" if ok else "HOUVE FALHA")
sys.exit(0 if ok else 1)
