"""Login com usuário e senha do Sankhya + sessão por token assinado.

Compartilhado entre os apps internos (cobrança e televendas). Nasceu dentro do
cobranca.py e saiu de lá quando o televendas passou a precisar do mesmo login:
duas cópias do mesmo mecanismo de sessão acabariam divergindo.

Quem usa:
    from auth import exige_operador
    @bp.route(...)
    @exige_operador
    def rota(): request.operador["codUsu"]

Este módulo NÃO sabe de perfis nem de vendedor: cada app decide, na própria
rota de sessão, se o usuário autenticado tem acesso a ele.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from functools import wraps
from urllib.parse import unquote

import cx_Oracle
import requests
from flask import Blueprint, jsonify, request

from db import conectar_oracle
# Reaproveita a autenticação de aplicação (OAuth client_credentials) e a base do
# Gateway já usadas no recálculo de impostos. O README marca essas funções como
# reaproveitáveis de propósito.
from impostos import autenticar_sankhya, _api_base

bp = Blueprint("auth", __name__)

# Timeout (segundos) das chamadas HTTP ao Gateway do Sankhya.
_TIMEOUT = 30


# ===========================================================================
# Operador / autenticação
#
# A cobrança e o televendas gravam ações auditáveis ("quem ligou", "quem
# mandou pro jurídico", "quem vendeu"). Por isso o operador é autenticado de
# verdade, não só declarado.
#
# A senha NÃO é conferida no Oracle: o hash da TSIUSU é proprietário do Sankhya
# e reproduzi-lo aqui seria frágil. Quem valida a senha é o próprio Sankhya,
# pelo serviço MobileLoginSP.login, chamado via Gateway (mesmo caminho de
# impostos._salvar_dataset). Validada a senha, o CODUSU é resolvido na TSIUSU
# pelo nome de usuário.
#
# Validado contra a instância (Om 4.35) em 18/07/2026: o Gateway aceita
# MobileLoginSP.login com o token de aplicação, e o serviço aceita a senha em
# texto puro no campo INTERNO. Se um dia o Gateway bloquear, a alternativa é
# chamar o mge on-premise direto (http://<host>:<porta>/mge/service.sbr) —
# trocar só o servico_sankhya.
# ===========================================================================


# ---------------------------------------------------------------------------
# Sessão do operador
#
# Validar a senha no login não bastava: o CODUSU seguia viajando no CORPO das
# requisições de escrita, então qualquer um na rede podia registrar chamada em
# nome de outra pessoa. Como essa trilha justifica negativação — que tem efeito
# jurídico para o cliente —, "quem ligou" precisa ser provado, não declarado.
#
# O token é assinado (HMAC-SHA256), não guardado: não há tabela de sessão nem
# dicionário em memória para sincronizar entre workers. Ele carrega o CODUSU e
# a expiração, e a assinatura impede que sejam alterados.
#
# Um token só, válido nos dois apps: quem entra na cobrança está autenticado
# no televendas também. O ACESSO a cada app é decidido por ele, não aqui.
# ---------------------------------------------------------------------------

SESSAO_HORAS = 12  # um turno de trabalho: entra uma vez por dia

# Sem AUTH_SECRET (ou o nome antigo, COBRANCA_SECRET) no ambiente cada processo
# gera o seu — funciona, mas todo mundo é deslogado a cada restart do container.
# Defina no .env do servidor.
_SEGREDO_VOLATIL = secrets.token_bytes(32)


def _segredo():
    # COBRANCA_SECRET é o nome de antes da extração: o .env do servidor já o
    # tem, e trocar o valor deslogaria todo mundo no meio do expediente.
    do_ambiente = os.environ.get("AUTH_SECRET") or os.environ.get("COBRANCA_SECRET")
    return do_ambiente.encode() if do_ambiente else _SEGREDO_VOLATIL


def _b64(dados):
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


def _de_b64(texto):
    # Repõe o padding que tiramos na hora de gerar.
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def emitir_token(cod_usu, nome_usu):
    corpo = {
        "codUsu": cod_usu,
        "nomeUsu": nome_usu,
        "exp": int(time.time()) + SESSAO_HORAS * 3600,
    }
    dados = _b64(json.dumps(corpo, separators=(",", ":")).encode())
    assinatura = _b64(hmac.new(_segredo(), dados.encode(), hashlib.sha256).digest())
    return f"{dados}.{assinatura}"


def ler_token(token):
    """Devolve o conteúdo do token, ou None se for inválido/adulterado/vencido."""
    if not token or "." not in token:
        return None
    dados, _, assinatura = token.partition(".")
    esperada = _b64(hmac.new(_segredo(), dados.encode(), hashlib.sha256).digest())
    # compare_digest em vez de == : comparação de tempo constante.
    if not hmac.compare_digest(esperada, assinatura):
        return None
    try:
        corpo = json.loads(_de_b64(dados))
    except (ValueError, json.JSONDecodeError):
        return None
    if float(corpo.get("exp") or 0) < time.time():
        return None
    return corpo


def exige_operador(f):
    """Só passa com sessão válida; publica o operador em `request.operador`."""

    @wraps(f)
    def interno(*args, **kwargs):
        cabecalho = request.headers.get("Authorization") or ""
        if cabecalho[:7].lower() == "bearer ":
            token = cabecalho[7:].strip()
        else:
            # navigator.sendBeacon não manda cabeçalho — é assim que o app
            # cancela a chamada quando o operador fecha a aba no meio dela.
            token = request.args.get("token") or ""
        operador = ler_token(token)
        if not operador:
            return jsonify({"erro": "Sessão expirada ou inválida. Entre de novo."}), 401
        request.operador = operador
        return f(*args, **kwargs)

    return interno


def servico_sankhya(service_name, request_body):
    """Chama um serviço do Sankhya via Gateway e devolve o envelope JSON cru.

    Não valida o status do envelope — quem chama decide (o login trata status
    "0" como credencial inválida, e não como erro de servidor).
    """
    token = autenticar_sankhya()
    resp = requests.post(
        f"{_api_base()}/gateway/v1/mge/service.sbr",
        # serviceName + outputType=json vão na query string, senão o service.sbr
        # responde XML e o resp.json() estoura (mesma pegadinha do impostos.py).
        params={"serviceName": service_name, "outputType": "json"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"serviceName": service_name, "requestBody": request_body},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"{service_name} HTTP {resp.status_code}: {resp.text}")
    try:
        return resp.json()
    except ValueError:
        raise RuntimeError(
            f"{service_name} retornou corpo não-JSON: {resp.text[:500]}"
        )


def _erro(err, codigo=500):
    print("Erro:", err)
    return jsonify({"erro": str(err)}), codigo


def _txt(valor):
    if valor is None:
        return None
    limpo = str(valor).strip()
    return limpo or None


@bp.route("/api/auth/login", methods=["POST"])
def login():
    """Valida usuário + senha do Sankhya e devolve o operador.

    Body: { "usuario": "<NOMEUSU>", "senha": "<senha>" }
    200:  { "sucesso": true, "codUsu": <int>, "nomeUsu": "<...>", "token": "<...>" }
    401:  credencial inválida.

    O token vale 12 h. /api/cobranca/login é um alias desta rota.
    """
    data = request.get_json(silent=True) or {}
    usuario = (data.get("usuario") or "").strip()
    senha = data.get("senha") or ""
    if not usuario or not senha:
        return jsonify({"erro": "Parâmetros 'usuario' e 'senha' são obrigatórios."}), 400

    # 1) O Sankhya valida a senha.
    try:
        envelope = servico_sankhya(
            "MobileLoginSP.login",
            {
                "NOMUSU": {"$": usuario},
                "INTERNO": {"$": senha},
                "KEEPCONNECTED": {"$": "false"},
            },
        )
    except requests.RequestException as err:
        return jsonify({"erro": f"Falha de comunicação com o Sankhya: {err}"}), 502
    except RuntimeError as err:
        return _erro(err)

    if str(envelope.get("status")) != "1":
        # status "0" = login recusado (senha errada, usuário bloqueado, etc.).
        # O statusMessage do Sankhya vem percent-encoded.
        msg = unquote(envelope.get("statusMessage") or "") or "Usuário ou senha inválidos."
        return jsonify({"erro": msg}), 401

    # 2) Resolve o CODUSU pelo nome (a senha já foi validada pelo Sankhya).
    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500

        cursor = conexao.cursor()
        cursor.execute(
            """
            SELECT CODUSU, NOMEUSU
            FROM TSIUSU
            WHERE UPPER(NOMEUSU) = UPPER(:NOMUSU)
            """,
            {"NOMUSU": usuario},
        )
        row = cursor.fetchone()
        if not row:
            # Sankhya validou mas não achamos o CODUSU: não deveria acontecer.
            return (
                jsonify({"erro": f"Usuário '{usuario}' validado, mas sem CODUSU na TSIUSU."}),
                500,
            )
        cod_usu, nome_usu = int(row[0]), _txt(row[1])
        return jsonify(
            {
                "sucesso": True,
                "codUsu": cod_usu,
                "nomeUsu": nome_usu,
                "token": emitir_token(cod_usu, nome_usu),
                "expiraEmHoras": SESSAO_HORAS,
            }
        )

    except cx_Oracle.Error as err:
        return _erro(f"Erro de Banco de Dados: {err}")
    except Exception as e:
        return _erro(e)
    finally:
        if conexao:
            conexao.close()
