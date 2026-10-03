"""Rotas do módulo de Televendas (workspace de ligações).

Plano completo no repositório do front: televendas/docs/PLANO-TELEVENDAS.md.

O login é o mesmo da cobrança (auth.py). Estar autenticado, porém, não dá
acesso ao televendas: o usuário precisa estar ATIVO na AD_PERFILTVL, que a
gerência mantém. Tabelas e regras deste módulo são isoladas das da cobrança.
"""

from functools import wraps

import cx_Oracle
from flask import Blueprint, jsonify, request

from auth import exige_operador
from db import conectar_oracle

bp = Blueprint("televendas", __name__)

PERFIS = ("OPERADOR", "GERENTE")


def _erro(err, codigo=500):
    print("Erro:", err)
    return jsonify({"erro": str(err)}), codigo


def _txt(valor):
    if valor is None:
        return None
    limpo = str(valor).strip()
    return limpo or None


# ---------------------------------------------------------------------------
# Acesso
#
# Perfil e vendedor são lidos do banco A CADA requisição, não gravados no
# token: tirar o acesso de alguém (ou trocar o vendedor dele) tem de valer na
# hora, não só quando o token de 12 h vencer. São duas consultas por PK/índice.
# ---------------------------------------------------------------------------

# O televendas é um vendedor (TGFVEN) cujo CODUSU é o usuário logado — a mesma
# regra da consulta da carteira do admin (MIN(CODVEND) ativo). TSIUSU.CODVEND
# fica só de reserva, para não deixar sem carteira quem só tem o vínculo antigo.
SQL_VENDEDOR = """
    SELECT V.CODVEND, V.APELIDO
      FROM TGFVEN V
     WHERE V.CODVEND = (
               SELECT NVL(
                          (SELECT MIN(T.CODVEND)
                             FROM TGFVEN T
                            WHERE T.CODUSU = :CODUSU
                              AND T.ATIVO = 'S'),
                          (SELECT NULLIF(U.CODVEND, 0)
                             FROM TSIUSU U
                            WHERE U.CODUSU = :CODUSU)
                      )
                 FROM DUAL
           )
"""

SQL_PERFIL = """
    SELECT UPPER(TRIM(P.PERFIL)), NVL(P.ATIVO, 'N')
      FROM AD_PERFILTVL P
     WHERE P.CODUSU = :CODUSU
"""


class SemAcesso(Exception):
    """O usuário autenticado não tem perfil ativo no televendas."""


def _contexto(cursor, cod_usu):
    """Perfil + vendedor do usuário. Levanta SemAcesso se não tiver perfil ativo."""
    cursor.execute(SQL_PERFIL, {"CODUSU": cod_usu})
    linha = cursor.fetchone()
    if not linha or linha[1] != "S" or linha[0] not in PERFIS:
        raise SemAcesso()

    cursor.execute(SQL_VENDEDOR, {"CODUSU": cod_usu})
    vend = cursor.fetchone()
    return {
        "perfil": linha[0],
        "codVend": int(vend[0]) if vend else None,
        "apelidoVend": _txt(vend[1]) if vend else None,
    }


def _resposta_banco(err):
    """ORA-00942 aqui quase sempre é a AD_PERFILTVL que ainda não foi criada."""
    if "ORA-00942" in str(err):
        return _erro(
            "Tabela de perfis do televendas (AD_PERFILTVL) não encontrada no banco.", 503
        )
    return _erro(f"Erro de Banco de Dados: {err}")


def exige_televendas(gerente=False):
    """Sessão válida + perfil ativo (e GERENTE, se pedido).

    Publica em `request.televendas`: perfil, codVend, apelidoVend. A conexão é
    aberta e fechada aqui; a rota abre a dela. Um custo pequeno em troca de não
    amarrar a vida da conexão ao decorator.
    """

    def decorador(f):
        @exige_operador
        @wraps(f)
        def interno(*args, **kwargs):
            conexao = None
            try:
                conexao = conectar_oracle()
                if not conexao:
                    return jsonify({"erro": "Falha na conexão com o banco"}), 500
                ctx = _contexto(conexao.cursor(), request.operador["codUsu"])
            except SemAcesso:
                return jsonify({"erro": "Você não tem acesso ao televendas."}), 403
            except cx_Oracle.Error as err:
                return _resposta_banco(err)
            finally:
                if conexao:
                    conexao.close()

            if gerente and ctx["perfil"] != "GERENTE":
                return jsonify({"erro": "Acesso restrito à gerência."}), 403
            request.televendas = ctx
            return f(*args, **kwargs)

        return interno

    return decorador


@bp.route("/api/televendas/sessao", methods=["GET"])
@exige_televendas()
def sessao():
    """Quem é o usuário no televendas. O app chama logo depois do login (e no F5).

    200: { sucesso, codUsu, nomeUsu, perfil: "OPERADOR"|"GERENTE",
           codVend, apelidoVend }   codVend nulo = sem carteira própria.
    401: sem sessão.  403: sem perfil ativo.  503: AD_PERFILTVL não existe.
    """
    return jsonify(
        {
            "sucesso": True,
            "codUsu": request.operador["codUsu"],
            "nomeUsu": request.operador["nomeUsu"],
            **request.televendas,
        }
    )
