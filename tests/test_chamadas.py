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

    def __init__(self, situacao="ABERTA", dono=25, aberta=None):
        self.situacao, self.dono, self.aberta, self.r, self.sqls = situacao, dono, aberta, None, []

    def execute(self, sql, vals=None):
        self.sqls.append(sql)
        if "FROM chamada WHERE id = %s FOR UPDATE" in sql:
            self.r = [(1, 11842, self.dono, self.situacao, None)]
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

print("TUDO OK" if ok else "HOUVE FALHA")
sys.exit(0 if ok else 1)
