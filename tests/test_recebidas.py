"""Ligações recebidas do televendas — SEM banco.

Confere: a busca entende CNPJ/CPF, código, telefone e nome; "de quem é" o cliente;
contato com documento já cadastrado vira 409; a ligação automática contato ->
cadastro só liga quando acha UM parceiro; e as rotas validam a entrada.
A SQL de verdade se confere no servidor com scripts/conferir_busca.py.
Rodar da raiz do repositório:  python tests/test_recebidas.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AUTH_SECRET"] = "x" * 40
import app as appmod  # noqa: E402
import auth  # noqa: E402
import televendas  # noqa: E402
import televendas_recebidas as R  # noqa: E402

ok = True


def chk(nome, cond):
    global ok
    ok &= bool(cond)
    print(("OK  " if cond else "FALHOU ") + nome)


def erro(fn, classe):
    try:
        fn()
    except classe as e:
        return e
    except Exception:  # noqa: BLE001
        return False
    return False


# ---- O que foi digitado ----
e = R.entender("12.345.678/0001-95")
chk("CNPJ com pontuação busca por documento, não por telefone", e["doc"] == "12345678000195" and e["fone"] is None)
e = R.entender("82 99954-0512")
chk("celular com DDD (11 dígitos) busca CPF e telefone pelos últimos 8", e["doc"] == "82999540512" and e["fone"] == "99540512")
e = R.entender("558235301722")
chk("telefone com 55 busca pelos últimos 8", e["fone"] == "35301722" and e["doc"] is None and e["codparc"] is None)
chk("número curto é código do parceiro", R.entender("11842")["codparc"] == 11842)
e = R.entender("  construção são josé ")
chk("nome vira texto em maiúsculas e sem acento", e["texto"] == "CONSTRUCAO SAO JOSE" and e["doc"] is None)
chk("uma letra só = 400", erro(lambda: R.entender("a"), R.Invalido))
chk("vazio = 400", erro(lambda: R.entender(""), R.Invalido))

sql, binds = R.sql_busca(R.entender("82999540512"))
chk("busca de 11 dígitos junta documento e telefone com OR", ":DOC" in sql and ":FONE" in sql and " OR " in sql)
chk("telefone também procura nos contatos (TGFCTT)", "TGFCTT" in sql and binds["FONE"] == "99540512")
chk("busca limita a 30", "ROWNUM <= 30" in sql)
import re as _re  # noqa: E402
_sel = sql.split("SELECT", 2)[2].split("FROM TGFPAR")[0]
_nomes = _re.findall(r"AS (\w+)", _sel)
chk("colunas da busca têm nomes únicos (senão ORA-00918 no SELECT *)", len(_nomes) == 14 and len(set(_nomes)) == 14)
chk("vendedor 0 não entra como representante", "NULLIF(P.CODVEND, 0)" in sql)

# ---- Documento ----
chk("CNPJ válido", R.cnpj_valido("11222333000181"))
chk("CNPJ com dígito errado", not R.cnpj_valido("11222333000182"))
chk("CPF válido", R.cpf_valido("52998224725"))
chk("CPF repetido é inválido", not R.cpf_valido("11111111111"))

# ---- De quem é ----
d = R.dono("J", "S", "S", "AL", 30, "JOAO REP", 41, "MARIA")
chk("representante ligado a televendas = carteira dele", d["tipo"] == "CARTEIRA" and d["codVend"] == 41 and d["apelido"] == "MARIA")
chk("PJ de AL sem vendedor = fila interna", R.dono("J", "S", "S", "AL", 0, None, 0, None)["tipo"] == "INTERNA")
chk("pessoa física sem vendedor = nenhuma lista", R.dono("F", "S", "S", "AL", 0, None, 0, None)["tipo"] == "NENHUMA")
chk("vendedor 0 (\"<SEM VENDEDOR>\") não vira representante", R.dono("F", "S", "S", "AL", 0, "<SEM VENDEDOR>", 0, None)["representante"] is None)
d = R.dono("J", "S", "S", "PE", 30, "JOAO REP", 0, None)
chk("representante sem televendas = nenhuma lista, com o representante", d["tipo"] == "NENHUMA" and d["representante"] == "JOAO REP")


# ---- Contatos (cursores simulados) ----
class Ora:
    def __init__(self, parceiros):
        self.parceiros, self.r = parceiros, []

    def execute(self, sql, binds=None):
        self.r = [(c,) for c in self.parceiros.get(binds.get("DOC"), [])] if "CGC_CPF" in sql else []

    def fetchall(self):
        return self.r

    def fetchone(self):
        return self.r[0] if self.r else None


class Pg:
    def __init__(self, contato=None, existente=None):
        self.contato, self.existente, self.r, self.sqls, self.rowcount = contato, existente, [], [], 1

    def execute(self, sql, vals=None):
        self.sqls.append((sql, vals))
        if "FROM contato k WHERE k.id" in sql:
            self.r = [self.contato] if self.contato else []
        elif "SELECT id FROM contato WHERE documento" in sql:
            self.r = [(self.existente,)] if self.existente else []
        elif "INSERT INTO contato" in sql:
            self.r = [(55,)]
        else:
            self.r = []

    def fetchone(self):
        return self.r[0] if self.r else None

    def fetchall(self):
        return self.r


def contato(id_=8, documento="11222333000181", codparc=None):
    return (id_, documento, "LOJA NOVA", None, "82999540512", "MACEIO", "AL", None, codparc, None, None, None, 0, None, None)


e = erro(lambda: R.criar_contato(Pg(), Ora({"11222333000181": [900]}), {"documento": "11.222.333/0001-81", "nome": "X"}, 25), R.JaCadastrado)
chk("contato com documento já no Sankhya = 409 com o codParc", e and e.codparc == 900)
chk("contato sem nome = 400", erro(lambda: R.criar_contato(Pg(), Ora({}), {"nome": " "}, 25), R.Invalido))
chk("contato com CNPJ inválido = 400", erro(lambda: R.criar_contato(Pg(), Ora({}), {"documento": "123", "nome": "X"}, 25), R.Invalido))
chk("documento já nos contatos devolve o mesmo", R.criar_contato(Pg(existente=7), Ora({}), {"documento": "11222333000181", "nome": "X"}, 25) == {"id": 7, "existente": True})
pg = Pg()
r = R.criar_contato(pg, Ora({}), {"nome": "SEM DOC", "uf": "al"}, 25)
ins = [v for s, v in pg.sqls if "INSERT INTO contato" in s][0]
chk("contato sem documento é criado (UF em maiúsculas)", r["id"] == 55 and ins[0] is None and ins[5] == "AL")

pg = Pg(contato=contato())
r = R.conferir_contato(pg, Ora({"11222333000181": [900]}), 8)
chk("conferir: um cadastro com o documento = liga", r["ligado"] and r["codParc"] == 900)
chk("ligar passa as ligações do contato para o cliente", any("UPDATE chamada SET codparc" in s and v == (900, 8) for s, v in pg.sqls))
r = R.conferir_contato(Pg(contato=contato()), Ora({"11222333000181": [900, 901]}), 8)
chk("conferir: dois cadastros com o documento = não liga sozinho", not r["ligado"] and "900" in r["motivo"])
r = R.conferir_contato(Pg(contato=contato()), Ora({}), 8)
chk("conferir: sem cadastro ainda = não liga", not r["ligado"])
r = R.conferir_contato(Pg(contato=contato(documento=None)), Ora({}), 8)
chk("conferir: contato sem documento = só à mão", not r["ligado"] and "mão" in r["motivo"])

# ---- Receita ----
d = R.dados_receita(
    {
        "razao_social": "LOJA NOVA LTDA",
        "estabelecimento": {"nome_fantasia": "LOJA NOVA", "cidade": {"nome": "Maceió"}, "estado": {"sigla": "AL"},
                            "ddd1": "82", "telefone1": "35301722", "situacao_cadastral": "Ativa"},
    }
)
chk("Receita preenche nome, cidade, UF e telefone", d["nome"] == "LOJA NOVA LTDA" and d["uf"] == "AL" and d["telefone"] == "8235301722")
chk("Receita sem estabelecimento não quebra", R.dados_receita({"razao_social": "X"})["cidade"] is None)


# ---- Rotas ----
class CurOraSessao:
    def execute(self, sql, binds=None):
        self.r = [("OPERADOR", "S")] if "AD_PERFILTVL" in sql else [(41, "ANA")] if "TGFVEN T" in sql else []

    def fetchone(self):
        return self.r[0] if self.r else None

    def fetchall(self):
        return self.r


class OraSessao:
    def cursor(self):
        return CurOraSessao()

    def close(self):
        pass


televendas.conectar_oracle = lambda: OraSessao()
c = appmod.app.test_client()
H = {"Authorization": "Bearer " + auth.emitir_token(25, "RAFAEL")}
chk("busca com uma letra = 400", c.get("/api/televendas/busca?q=a", headers=H).status_code == 400)
r = c.post("/api/televendas/chamadas/iniciar", headers=H, json={"lista": "RECEBIDA"})
chk("recebida sem cliente nem contato = 400", r.status_code == 400)
r = c.post("/api/televendas/chamadas/iniciar", headers=H, json={"lista": "RECEBIDA", "codParc": 1, "contatoId": 2})
chk("recebida com cliente E contato = 400", r.status_code == 400)
chk("receita com CNPJ inválido = 400", c.get("/api/televendas/receita/123", headers=H).status_code == 400)
chk("operador não liga contato à mão (403)", c.put("/api/televendas/contatos/8/ligar", headers=H, json={"codParc": 1}).status_code == 403)

print("TUDO OK" if ok else "HOUVE FALHA")
sys.exit(0 if ok else 1)
