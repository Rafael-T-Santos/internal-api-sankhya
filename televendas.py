"""Rotas do módulo de Televendas (workspace de ligações).

Plano completo no repositório do front: televendas/docs/PLANO-TELEVENDAS.md.

O login é o mesmo da cobrança (auth.py). Estar autenticado, porém, não dá
acesso ao televendas: o usuário precisa estar ATIVO na AD_PERFILTVL, que a
gerência mantém. Tabelas e regras deste módulo são isoladas das da cobrança.
"""

from functools import wraps

import cx_Oracle
import psycopg2
from flask import Blueprint, jsonify, request

import televendas_config as tvconfig
import televendas_ficha as ficha
import televendas_listas as listas
from auth import exige_operador
from db import conectar_oracle
from pg import PostgresNaoConfigurado, conectar_postgres

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


# ---------------------------------------------------------------------------
# Listas (Fase 2) — regra em televendas_listas.py
# ---------------------------------------------------------------------------


def _montar_lista(lista, codtelevend=None, completa=False):
    """Oracle (clientes) + Postgres (escala, configuração e contatos do televendas)."""
    ora = pg = None
    try:
        pg = conectar_postgres()
        ora = conectar_oracle()
        if not ora:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
        cur_pg = pg.cursor()
        cur_ora = ora.cursor()

        config = listas.ler_configuracao(cur_pg)
        hoje, dia_semana, clientes = listas.buscar_clientes(cur_ora, lista, config, codtelevend)
        listas.anexar_contatos(cur_ora, clientes)

        total, aguardando = len(clientes), 0
        if lista == "CARTEIRA":
            escala = listas.ler_escala(cur_pg, {c["codVendExterno"] for c in clientes})
            listas.anexar_rota(clientes, escala, dia_semana)
            aguardando = sum(1 for c in clientes if not c["rota"]["liberado"])
            if not completa:
                clientes = [c for c in clientes if c["rota"]["liberado"]]

        listas.anexar_contatos_televendas(cur_pg, clientes)
        return jsonify(
            {
                "sucesso": True,
                "lista": lista,
                "hoje": hoje.isoformat(),
                "diaSemana": dia_semana,
                "totalCarteira": total,
                "aguardandoRota": aguardando,
                "totalRegistros": len(clientes),
                "dados": clientes,
            }
        )
    except PostgresNaoConfigurado as err:
        return _erro(err, 503)
    except listas.ConfiguracaoIncompleta as err:
        return _erro(err, 503)
    except psycopg2.Error as err:
        return _erro(f"Erro no banco do televendas: {err}")
    except cx_Oracle.Error as err:
        return _erro(f"Erro de Banco de Dados: {err}")
    finally:
        if ora:
            ora.close()
        if pg:
            pg.close()


@bp.route("/api/televendas/listas/carteira", methods=["GET"])
@exige_televendas()
def lista_carteira():
    """Minha carteira: clientes dos representantes externos ligados ao televendas.

    Query: completa=1 (também os que a escala ainda não liberou — só para ver;
    o /iniciar recusa ligar para eles), codTelevend=<n> (só GERENTE: a carteira
    de outro televendas). Cada cliente traz `rota` {diaVisita, liberado, liberaEm}.
    """
    ctx = request.televendas
    codtelevend = ctx["codVend"]
    pedido = request.args.get("codTelevend")
    if pedido:
        if ctx["perfil"] != "GERENTE":
            return jsonify({"erro": "Só a gerência abre a carteira de outro televendas."}), 403
        try:
            codtelevend = int(pedido)
        except ValueError:
            return jsonify({"erro": "codTelevend inválido."}), 400
    if not codtelevend:
        return jsonify({"erro": "Seu usuário não tem vendedor vinculado, então não tem carteira própria."}), 400
    return _montar_lista("CARTEIRA", codtelevend, completa=request.args.get("completa") == "1")


@bp.route("/api/televendas/listas/interna", methods=["GET"])
@exige_televendas()
def lista_interna():
    """Fila interna: clientes PJ de AL sem vendedor, compartilhada por todos."""
    return _montar_lista("INTERNA")


# ---------------------------------------------------------------------------
# Ficha do cliente (Fase 2) — regra em televendas_ficha.py
# ---------------------------------------------------------------------------


def _ficha(codparc, montar):
    """Lê a configuração no Postgres e roda `montar(cur_oracle, codparc, config)`."""
    ora = pg = None
    try:
        pg = conectar_postgres()
        config = listas.ler_configuracao(pg.cursor())
        ora = conectar_oracle()
        if not ora:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
        return jsonify({"sucesso": True, "codParc": codparc, **montar(ora.cursor(), codparc, config)})
    except (PostgresNaoConfigurado, listas.ConfiguracaoIncompleta) as err:
        return _erro(err, 503)
    except psycopg2.Error as err:
        return _erro(f"Erro no banco do televendas: {err}")
    except cx_Oracle.Error as err:
        return _erro(f"Erro de Banco de Dados: {err}")
    finally:
        if ora:
            ora.close()
        if pg:
            pg.close()


@bp.route("/api/televendas/clientes/<int:codparc>/compras", methods=["GET"])
@exige_televendas()
def cliente_compras(codparc):
    """Notas dos últimos `meses` (padrão 12, máx. 36), série mensal, ticket médio e frequência."""
    try:
        meses = min(max(int(request.args.get("meses", 12)), 1), 36)
    except ValueError:
        return jsonify({"erro": "meses inválido."}), 400
    return _ficha(codparc, lambda cur, cod, cfg: ficha.compras(cur, cod, cfg, meses))


@bp.route("/api/televendas/clientes/<int:codparc>/mix", methods=["GET"])
@exige_televendas()
def cliente_mix(codparc):
    """Produtos dos últimos 6 meses e os que o cliente parou de comprar."""
    return _ficha(codparc, ficha.mix)


@bp.route("/api/televendas/clientes/<int:codparc>/ultimo-pedido", methods=["GET"])
@exige_televendas()
def cliente_ultimo_pedido(codparc):
    """Itens da nota mais recente do cliente entre as TOPs de compra (consulta do admin)."""
    return _ficha(codparc, ficha.ultimo_pedido)


# ---------------------------------------------------------------------------
# Configuração da gerência (Fase 2) — regra em televendas_config.py
# ---------------------------------------------------------------------------


def _config(fazer, codigo_ok=200):
    """Abre Postgres (numa transação) e Oracle, roda `fazer(cur_pg, cur_ora, quem)`.

    Erro de validação vira 400 e desfaz tudo o que a transação já tinha feito.
    """
    ora = pg = None
    try:
        pg = conectar_postgres()
        ora = conectar_oracle()
        if not ora:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
        quem = f"{request.operador['nomeUsu']} ({request.operador['codUsu']})"
        with pg:  # commit no fim; rollback se qualquer coisa levantar
            resultado = fazer(pg.cursor(), ora.cursor(), quem)
        return jsonify({"sucesso": True, **resultado}), codigo_ok
    except tvconfig.Invalido as err:
        return jsonify({"erro": str(err)}), 400
    except PostgresNaoConfigurado as err:
        return _erro(err, 503)
    except psycopg2.Error as err:
        return _erro(f"Erro no banco do televendas: {err}")
    except cx_Oracle.Error as err:
        return _erro(f"Erro de Banco de Dados: {err}")
    finally:
        if ora:
            ora.close()
        if pg:
            pg.close()


def _corpo():
    return request.get_json(silent=True) or {}


@bp.route("/api/televendas/config/escala", methods=["GET"])
@exige_televendas(gerente=True)
def config_escala():
    """Linhas da escala com nomes de representante, cidade e bairro."""
    return _config(lambda pg, ora, quem: {"dados": tvconfig.listar_escala(pg, ora)})


@bp.route("/api/televendas/config/escala", methods=["POST"])
@exige_televendas(gerente=True)
def config_escala_criar():
    """{codVend, diaVisita 1-5, tipoLocal C|B, codCid | codBai} -> 201 {id}."""
    corpo = _corpo()
    return _config(lambda pg, ora, quem: {"id": tvconfig.criar_escala(pg, ora, corpo, quem)}, 201)


@bp.route("/api/televendas/config/escala/<int:id_>", methods=["PUT"])
@exige_televendas(gerente=True)
def config_escala_alterar(id_):
    """{diaVisita?, ativo?}. Desativar em vez de apagar: o histórico de quem mexeu fica."""
    corpo = _corpo()

    def fazer(pg, ora, quem):
        if not tvconfig.alterar_escala(pg, id_, corpo, quem):
            raise tvconfig.Invalido(f"Linha {id_} da escala não existe.")
        return {}

    return _config(fazer)


@bp.route("/api/televendas/config/representantes", methods=["GET"])
@exige_televendas(gerente=True)
def config_representantes():
    """Representantes externos ligados a algum televendas (TGFVEN.AD_CODVEND)."""
    return _config(lambda pg, ora, quem: {"dados": tvconfig.representantes(ora)})


@bp.route("/api/televendas/config/locais", methods=["GET"])
@exige_televendas(gerente=True)
def config_locais():
    """?tipo=C|B&q=texto -> até 30 cidades de AL (C) ou bairros (B) pelo nome."""
    tipo = request.args.get("tipo", "C")
    if tipo not in ("C", "B"):
        return jsonify({"erro": "tipo é C ou B."}), 400
    q = request.args.get("q")
    return _config(lambda pg, ora, quem: {"dados": tvconfig.buscar_local(ora, tipo, q)})


@bp.route("/api/televendas/config/tops", methods=["GET"])
@exige_televendas(gerente=True)
def config_tops():
    return _config(lambda pg, ora, quem: {"dados": tvconfig.listar_tops(pg, ora)})


@bp.route("/api/televendas/config/tops", methods=["POST"])
@exige_televendas(gerente=True)
def config_tops_criar():
    """{codTipOper, conversao?, ultimaCompra?}: só TOP que existe no Sankhya."""
    corpo = _corpo()

    def fazer(pg, ora, quem):
        cod = tvconfig._int(corpo, "codTipOper")
        tvconfig.salvar_top(pg, ora, cod, corpo, quem, criar=True)
        return {"codTipOper": cod}

    return _config(fazer, 201)


@bp.route("/api/televendas/config/tops/<int:cod>", methods=["PUT"])
@exige_televendas(gerente=True)
def config_tops_alterar(cod):
    """{conversao?, ultimaCompra?, ativo?}. Recusa deixar as listas sem TOP de última compra."""
    corpo = _corpo()

    def fazer(pg, ora, quem):
        if not tvconfig.salvar_top(pg, ora, cod, corpo, quem):
            raise tvconfig.Invalido(f"A TOP {cod} não está cadastrada.")
        tvconfig.checar_tops_minimas(pg)
        return {}

    return _config(fazer)


@bp.route("/api/televendas/config/parametros", methods=["GET"])
@exige_televendas(gerente=True)
def config_parametros():
    return _config(lambda pg, ora, quem: {"dados": tvconfig.listar_parametros(pg)})


@bp.route("/api/televendas/config/parametros/<chave>", methods=["PUT"])
@exige_televendas(gerente=True)
def config_parametros_alterar(chave):
    """{valor}: só as chaves e faixas de televendas_config.PARAMETROS."""
    corpo = _corpo()

    def fazer(pg, ora, quem):
        if not tvconfig.salvar_parametro(pg, chave, corpo, quem):
            raise tvconfig.Invalido(f"Parâmetro '{chave}' não existe.")
        return {}

    return _config(fazer)
