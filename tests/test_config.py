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
        if "SQL_OPERADORES_CARTEIRA" in estado and "'OPERADOR'" in sql:
            self.r = estado["SQL_OPERADORES_CARTEIRA"]
        elif "AD_PERFILTVL" in sql:
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
            # (codvend, codigo do local) -> dia já ativo
            dia = estado.get("dup", {}).get((vals[0], vals[2]))
            self.r = [(dia,)] if dia else []
        elif "FOR UPDATE" in sql:
            self.r = [r for r in estado.get("escala", []) if r[0] in vals[0]]
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

# --- escala em lote -------------------------------------------------------
# (id, codvend, dia, tipo, codcid, codbai, ativo)
estado["escala"] = [
    (1, 23, 1, "C", 100, None, True),   # MESSIAS, segunda
    (2, 23, 2, "C", 200, None, True),   # MESSIAS, terça — destino já tem a cidade 200
    (3, 23, 3, "C", 300, None, False),  # desativada
    (4, 58, 4, "B", None, 9, True),     # já é da CLAUDIA
]
estado["dup"] = {(58, 200): 3}
lote = lambda corpo: c.post("/api/televendas/config/escala/lote", headers=H, json=corpo)  # noqa: E731

estado["sqls"] = []
r = lote({"acao": "transferir", "codVend": 58, "ids": [1, 2, 3, 4]})
chk("transferir: só a linha que pode muda", r.status_code == 200 and r.json["alteradas"] == 1)
motivos = {i["id"]: i["motivo"] for i in r.json["ignoradas"]}
chk(
    "transferir: pula repetida, desativada e a que já é do destino",
    set(motivos) == {2, 3, 4} and "quarta" in motivos[2] and "desativada" in motivos[3],
)
ins = [v for s, v in estado["sqls"] if "INSERT INTO escala" in s]
upd = [v for s, v in estado["sqls"] if "SET ativo = false" in s]
chk("transferir: desativa a antiga e cria a nova no mesmo dia", upd == [("RAFAEL (25)", 1)] and ins == [(58, 1, "C", 100, None, "RAFAEL (25)")])

chk("transferir para vendedor inexistente -> 400", lote({"acao": "transferir", "codVend": 999, "ids": [1]}).status_code == 400)
chk("linha que não existe -> 400", lote({"acao": "desativar", "ids": [1, 50]}).status_code == 400)
chk("sem ids -> 400", lote({"acao": "desativar", "ids": []}).status_code == 400)
chk("ação desconhecida -> 400", lote({"acao": "apagar", "ids": [1]}).status_code == 400)
chk("dia fora de seg–sex -> 400", lote({"acao": "dia", "diaVisita": 7, "ids": [1]}).status_code == 400)

r = lote({"acao": "dia", "diaVisita": 2, "ids": [1, 2, 3]})
chk("dia: muda só a ativa com dia diferente", r.status_code == 200 and r.json["alteradas"] == 1 and len(r.json["ignoradas"]) == 2)
r = lote({"acao": "desativar", "ids": [1, 2, 3]})
chk("desativar: ignora a já desativada", r.json["alteradas"] == 2 and r.json["ignoradas"][0]["id"] == 3)
estado["dup"] = {(23, 300): 1}
r = lote({"acao": "reativar", "ids": [1, 3]})
chk("reativar: não cria local repetido", r.json["alteradas"] == 0 and {i["id"] for i in r.json["ignoradas"]} == {1, 3})
estado["perfil"] = "OPERADOR"
chk("operador não mexe em lote (403)", lote({"acao": "desativar", "ids": [1]}).status_code == 403)
estado["perfil"] = "GERENTE"

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

estado["SQL_OPERADORES_CARTEIRA"] = [(31, "MARIA SILVA ", 41, "MARIA"), (32, "JOAO", 52, None)]
r = c.get("/api/televendas/config/operadores-carteira", headers=H)
chk(
    "lista operadores com carteira (código do vendedor + nome)",
    r.status_code == 200
    and r.json["dados"][0] == {"codUsu": 31, "nomeUsu": "MARIA SILVA", "codVend": 41, "apelidoVend": "MARIA"}
    and r.json["dados"][1]["apelidoVend"] is None,
)
estado["perfil"] = "OPERADOR"
chk("operador não lista as carteiras (403)", c.get("/api/televendas/config/operadores-carteira", headers=H).status_code == 403)
estado["perfil"] = "GERENTE"

print("TUDO OK" if ok else "HOUVE FALHA")
sys.exit(0 if ok else 1)
