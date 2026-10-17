"""Regras do registro de ligação do televendas — SEM banco.

A trava e a corrida só se provam contra o banco real (tests/smoke-televendas.ps1).
Aqui ficam as regras que dá para conferir com cursores simulados.
Rodar da raiz do repositório:  python tests/test_chamadas.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import televendas_chamadas as C  # noqa: E402

OP = {"codUsu": 25, "nomeUsu": "RAFAEL"}


class Pg:
    """Simula a chamada `situacao`, do operador `dono`, e uma trava aberta opcional."""

    def __init__(self, situacao="ABERTA", dono=25, aberta=None, lista="CARTEIRA", codparc=11842, abertas=None):
        self.situacao, self.dono, self.aberta, self.r, self.sqls = situacao, dono, aberta, None, []
        self.lista, self.codparc, self.abertas = lista, codparc, abertas or []
        self.vals = []

    def execute(self, sql, vals=None):
        self.sqls.append(sql)
        self.vals.append(vals)
        if "FROM chamada WHERE id = %s FOR UPDATE" in sql:
            contato = 8 if self.codparc is None else None
            self.r = [(1, self.codparc, self.dono, self.situacao, None, self.lista, contato)]
        elif "ORDER BY inicio" in sql and "situacao = 'ABERTA'" in sql:  # recebida: todas as abertas
            self.r = self.abertas
        elif "FROM assunto WHERE id" in sql:
            self.r = [(1,)] if vals[0] == 3 else []
        elif "RETURNING expira" in sql:
            self.r = [(None,)]
        elif "situacao = 'ABERTA' AND expira > now()" in sql:
            self.r = [self.aberta] if self.aberta else []
        elif "FROM parametro" in sql:
            self.r = [("20",)]
        elif "RETURNING id, inicio, expira" in sql:
            self.r = [(99, None, None)]
        elif "RETURNING inicio, fim" in sql:
            self.r = [(None, None)]
        elif "FROM motivo WHERE id" in sql:
            self.r = []
        else:
            self.r = []

    def fetchone(self):
        return self.r[0] if self.r else None

    def fetchall(self):
        return self.r


class Ora:
    def __init__(self, notas):
        self.notas, self.r = notas, []

    def execute(self, sql, binds=None):
        self.r = [(n, self.notas[n]) for n in binds.values() if n in self.notas]

    def fetchall(self):
        return self.r


ok = True


def chk(nome, cond):
    global ok
    ok &= bool(cond)
    print(("OK  " if cond else "FALHOU ") + nome)


def erro(fn, classe):
    try:
        fn()
    except classe as e:
        return str(e) or True
    except Exception:  # noqa: BLE001 - exceção de outro tipo conta como falha do teste
        return False
    return False


ora = Ora({500: 11842, 600: 99999})
chk("resultado é obrigatório", erro(lambda: C.finalizar(Pg(), ora, 1, OP, {"obs": "x"}), C.Invalido))
chk("desfecho só com ATENDEU", erro(lambda: C.finalizar(Pg(), ora, 1, OP, {"resultado": "NAO_ATENDEU", "desfecho": "VENDA"}), C.Invalido))
chk("motivo inexistente", erro(lambda: C.finalizar(Pg(), ora, 1, OP, {"resultado": "ATENDEU", "motivoId": 7}), C.Invalido))
chk("NUNOTA inexistente", erro(lambda: C.finalizar(Pg(), ora, 1, OP, {"resultado": "ATENDEU", "notas": [{"nunota": 1, "tipo": "PEDIDO"}]}), C.Invalido))
chk("NUNOTA de outro cliente", "outro cliente" in str(erro(lambda: C.finalizar(Pg(), ora, 1, OP, {"resultado": "ATENDEU", "notas": [{"nunota": 600, "tipo": "PEDIDO"}]}), C.Invalido)))
chk("tipo de nota inválido", erro(lambda: C.finalizar(Pg(), ora, 1, OP, {"resultado": "ATENDEU", "notas": [{"nunota": 500, "tipo": "VENDA"}]}), C.Invalido))
chk("obs acima de 4000", erro(lambda: C.finalizar(Pg(), ora, 1, OP, {"resultado": "ATENDEU", "obs": "x" * 4001}), C.Invalido))
chk("retorno com data inválida", erro(lambda: C.finalizar(Pg(), ora, 1, OP, {"resultado": "ATENDEU", "retornoEm": "amanhã"}), C.Invalido))
pg = Pg()
r = C.finalizar(pg, ora, 1, OP, {"resultado": "ATENDEU", "desfecho": "VENDA", "notas": [{"nunota": 500, "tipo": "PEDIDO"}]})
chk("registro válido grava a nota vinculada", r["id"] == 1 and any("INSERT INTO chamada_nota" in s for s in pg.sqls))
chk("ligação de outro operador = 403", erro(lambda: C.finalizar(Pg(dono=7), ora, 1, OP, {"resultado": "ATENDEU"}), C.Proibido))
chk("registrar duas vezes = 409", erro(lambda: C.finalizar(Pg("FINALIZADA"), ora, 1, OP, {"resultado": "ATENDEU"}), C.Conflito))
chk("registrar descartada = 409", erro(lambda: C.finalizar(Pg("CANCELADA"), ora, 1, OP, {"resultado": "ATENDEU"}), C.Conflito))

chk("cancelar é idempotente (já cancelada = sem erro)", C.cancelar(Pg("CANCELADA"), 1, OP)["situacao"] == "CANCELADA")
chk("cancelar registrada não desfaz", C.cancelar(Pg("FINALIZADA"), 1, OP)["situacao"] == "FINALIZADA")

pg = Pg(aberta=(5, 7, "PEDRO", None, None))
e = None
try:
    C.iniciar(pg, 11842, "INTERNA", OP, None, None)
except C.Conflito as x:
    e = x
chk("cliente aberto por outro = 409 com quem está", e and e.corpo["emChamada"]["nomeUsu"] == "PEDRO")
chk("trava pega o advisory lock ANTES de olhar", "pg_advisory_xact_lock" in pg.sqls[0])
pg = Pg(aberta=(5, 25, "RAFAEL", None, None))
chk("aberto por mim = retoma a mesma ligação", C.iniciar(pg, 11842, "INTERNA", OP, None, None) == {"id": 5, "inicio": None, "expiraEm": None, "retomada": True})
chk("cliente livre = nova ligação", C.iniciar(Pg(), 11842, "INTERNA", OP, None, None)["id"] == 99)

# ---- Recebidas ----
r = C.iniciar_recebida(Pg(abertas=[(5, 7, "PEDRO", None, None, "CARTEIRA")]), 11842, None, OP, 41, 30)
chk("recebida com cliente aberto por outro abre mesmo assim, com aviso", r["id"] == 99 and r["emChamada"]["nomeUsu"] == "PEDRO")
r = C.iniciar_recebida(Pg(abertas=[(5, 25, "RAFAEL", None, None, "RECEBIDA")]), 11842, None, OP, 41, 30)
chk("recebida aberta por mim = retoma", r["id"] == 5 and r["retomada"])
pg = Pg()
C.iniciar_recebida(pg, None, 8, OP, None, None)
chk("recebida de contato não pega advisory lock (não tem codparc)", not any("pg_advisory" in x for x in pg.sqls))
ins = [v for x, v in zip(pg.sqls, pg.vals) if "INSERT INTO chamada" in x][0]
chk("recebida de contato grava contato_id e lista RECEBIDA", ins[0] is None and ins[1] == 8)

pg = Pg(lista="RECEBIDA")
C.renovar(pg, 1, OP)
chk("renovar recebida só estica, sem conferir trava de outro", not any("pg_advisory" in x for x in pg.sqls))

chk("finalizar recebida sem assunto = 400", erro(lambda: C.finalizar(Pg(lista="RECEBIDA"), ora, 1, OP, {}), C.Invalido))
chk("finalizar recebida com assunto inativo = 400", erro(lambda: C.finalizar(Pg(lista="RECEBIDA"), ora, 1, OP, {"assuntoId": 4}), C.Invalido))
pg = Pg(lista="RECEBIDA")
C.finalizar(pg, ora, 1, OP, {"assuntoId": 3, "resultado": "NAO_ATENDEU", "desfecho": "VENDA"})
upd = [v for x, v in zip(pg.sqls, pg.vals) if "SET situacao = 'FINALIZADA'" in x][0]
chk("finalizar recebida força ATENDEU e grava o assunto", upd[0] == "ATENDEU" and upd[1] == "VENDA" and upd[-2] == 3)
pg = Pg(lista="CARTEIRA")
C.finalizar(pg, ora, 1, OP, {"resultado": "ATENDEU", "assuntoId": 3})
upd = [v for x, v in zip(pg.sqls, pg.vals) if "SET situacao = 'FINALIZADA'" in x][0]
chk("ligação feita ignora assunto", upd[-2] is None)
chk(
    "contato sem cadastro não aceita nota",
    erro(lambda: C.finalizar(Pg(lista="RECEBIDA", codparc=None), ora, 1, OP, {"assuntoId": 3, "notas": [{"nunota": 500, "tipo": "PEDIDO"}]}), C.Invalido),
)

print("TUDO OK" if ok else "HOUVE FALHA")
sys.exit(0 if ok else 1)
