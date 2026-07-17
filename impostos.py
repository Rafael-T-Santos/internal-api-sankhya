"""Recálculo de impostos via API do Sankhya (Gateway).

Motivação: `/api/cadastrar-produto` grava a tributação com INSERT direto no Oracle
(clonando o produto-modelo 11783). Isso NÃO dispara o motor de cálculo tributário do
Sankhya, então o produto nasce com impostos inconsistentes. Reprocessar as entidades
`EmpresaProdutoImpostos` e `Produto` pelo serviço `DatasetSP.save` força o ERP a
recalcular.

Este módulo é a FASE 1: uma rota de teste isolada (`/api/teste-recalcular-impostos`)
que dispara o recálculo num produto já existente, sem tocar na rota de cadastro. As
funções `autenticar_sankhya()` e `_salvar_dataset()` são reaproveitáveis na fase 2
(integração na rota real).

Credenciais (via .env, ver README):
- SANKHYA_X_TOKEN      -> tela "Configurações de Gateway" do Sankhya Om
- SANKHYA_CLIENT_ID    -> Portal do Desenvolvedor
- SANKHYA_CLIENT_SECRET-> Portal do Desenvolvedor
- SANKHYA_API_BASE     -> opcional, default produção (use o sandbox p/ testar)
"""
import os

import cx_Oracle
import requests
from flask import Blueprint, jsonify, request

from db import conectar_oracle

bp = Blueprint("impostos", __name__)

# Produto-modelo de onde a tributação-base é lida (mesmo clonado em app.py:cadastrar_produto).
CODPROD_MODELO = 11783

# Timeout (segundos) para as chamadas HTTP ao Gateway.
_TIMEOUT = 30


def _api_base():
    return os.environ.get("SANKHYA_API_BASE", "https://api.sankhya.com.br").rstrip("/")


def autenticar_sankhya():
    """Autentica no Gateway (OAuth 2.0 client_credentials) e devolve o access_token.

    Levanta RuntimeError com mensagem clara se faltar credencial ou o Sankhya recusar.
    O token vale ~300s (5 min) — suficiente para o fluxo de um request.
    """
    x_token = os.environ.get("SANKHYA_X_TOKEN")
    client_id = os.environ.get("SANKHYA_CLIENT_ID")
    client_secret = os.environ.get("SANKHYA_CLIENT_SECRET")

    if not all([x_token, client_id, client_secret]):
        raise RuntimeError(
            "Credenciais do Sankhya (SANKHYA_X_TOKEN, SANKHYA_CLIENT_ID, "
            "SANKHYA_CLIENT_SECRET) não configuradas nas variáveis de ambiente."
        )

    resp = requests.post(
        f"{_api_base()}/authenticate",
        headers={
            "X-Token": x_token,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=_TIMEOUT,
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Falha na autenticação do Sankhya (HTTP {resp.status_code}): {resp.text}"
        )

    try:
        corpo = resp.json()
    except ValueError:
        raise RuntimeError(
            f"Autenticação retornou corpo não-JSON (HTTP {resp.status_code}): {resp.text[:500]}"
        )

    token = corpo.get("access_token")
    if not token:
        raise RuntimeError(f"Autenticação sem access_token na resposta: {resp.text[:500]}")
    return token


def _montar_record(fields, pk_names, dados):
    """Monta um record no formato do DatasetSP.save.

    As chaves de `values` são o ÍNDICE do campo dentro de `fields`; os campos que estão
    no `pk` ficam de fora de `values`. Reproduz exatamente o formato dos exemplos.
    """
    pk = {}
    values = {}
    for idx, campo in enumerate(fields):
        valor = dados[campo]
        if campo in pk_names:
            pk[campo] = str(valor)
        else:
            values[str(idx)] = str(valor)
    return {"pk": pk, "values": values}


def _salvar_dataset(token, entity_name, fields, record):
    """Chama DatasetSP.save para uma entidade e devolve o responseBody.

    Levanta RuntimeError se o Sankhya devolver status != "1" (o statusMessage traz o
    motivo). `standAlone: false` conforme os exemplos.
    """
    payload = {
        "serviceName": "DatasetSP.save",
        "requestBody": {
            "entityName": entity_name,
            "standAlone": False,
            "fields": fields,
            "records": [record],
        },
    }

    resp = requests.post(
        f"{_api_base()}/gateway/v1/mge/service.sbr",
        # serviceName + outputType=json vão na query string: sem outputType=json o
        # service.sbr responde XML e o resp.json() estoura.
        params={"serviceName": "DatasetSP.save", "outputType": "json"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=_TIMEOUT,
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"DatasetSP.save ({entity_name}) HTTP {resp.status_code}: {resp.text}"
        )

    try:
        corpo = resp.json()
    except ValueError:
        raise RuntimeError(
            f"DatasetSP.save ({entity_name}) retornou corpo não-JSON: {resp.text[:500]}"
        )
    # Envelope do Sankhya: status "1" = sucesso, "0" = erro (com statusMessage).
    if str(corpo.get("status")) != "1":
        msg = corpo.get("statusMessage") or corpo
        raise RuntimeError(f"DatasetSP.save ({entity_name}) recusado pelo Sankhya: {msg}")

    return corpo.get("responseBody", corpo)


def _ler_base_modelo():
    """Lê a tributação-base do produto-modelo (11783) do Oracle.

    Devolve (empresas, produto):
    - empresas: lista de dicts, uma linha por empresa em TGFPEM.
    - produto:  dict com os campos do TGFPRO.
    """
    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            raise RuntimeError("Falha na conexão com o banco")

        cursor = conexao.cursor()

        cursor.execute(
            """
            SELECT CODEMP, GRUPOICMS, TEMICMS, TIPSUBST, USOPROD, CODESPECST
            FROM TGFPEM
            WHERE CODPROD = :BASE
            """,
            {"BASE": CODPROD_MODELO},
        )
        empresas = [
            {
                "CODEMP": row[0],
                "GRUPOICMS": row[1],
                "TEMICMS": row[2],
                "TIPSUBST": row[3],
                "USOPROD": row[4],
                "CODESPECST": row[5],
            }
            for row in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT TEMICMS, TIPSUBST, USOPROD, CODESPECST
            FROM TGFPRO
            WHERE CODPROD = :BASE
            """,
            {"BASE": CODPROD_MODELO},
        )
        linha = cursor.fetchone()
        if not linha:
            raise RuntimeError(f"Produto-modelo {CODPROD_MODELO} não encontrado em TGFPRO.")
        produto = {
            "TEMICMS": linha[0],
            "TIPSUBST": linha[1],
            "USOPROD": linha[2],
            "CODESPECST": linha[3],
        }

        return empresas, produto
    finally:
        if conexao:
            conexao.close()


def limpar_impostos(cod_prod):
    """Zera os campos tributários-chave de `cod_prod` direto no Oracle.

    Isso força o DatasetSP.save seguinte a ser uma mudança REAL (vazio → valor), que é o
    que dispara o recálculo tributário do Sankhya. O save posterior restaura os
    valores-base, então o estado final é o mesmo — mas com a diferença de ter passado por
    uma alteração de verdade. Usado tanto na rota de teste quanto no cadastro real.

    Só mexe nos campos que a gente reenvia; GRUPOICMS/CODESPECST vão a NULL e TEMICMS
    a 'N' (todos diferentes dos valores-base 35/60, 2400100, 'S').
    """
    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            raise RuntimeError("Falha na conexão com o banco")

        cursor = conexao.cursor()
        cursor.execute(
            """
            UPDATE TGFPEM
            SET GRUPOICMS = NULL, TEMICMS = 'N', CODESPECST = NULL
            WHERE CODPROD = :COD
            """,
            {"COD": cod_prod},
        )
        linhas_pem = cursor.rowcount
        cursor.execute(
            """
            UPDATE TGFPRO
            SET TEMICMS = 'N', CODESPECST = NULL
            WHERE CODPROD = :COD
            """,
            {"COD": cod_prod},
        )
        linhas_pro = cursor.rowcount
        conexao.commit()
        return {"tgfpem": linhas_pem, "tgfpro": linhas_pro}
    except Exception:
        if conexao:
            conexao.rollback()
        raise
    finally:
        if conexao:
            conexao.close()


def restaurar_impostos(cod_prod):
    """Restaura os campos tributários de `cod_prod` para os valores-base do modelo 11783.

    Fallback usado quando o recálculo via Sankhya falha DEPOIS de `limpar_impostos`: sem
    isso o produto ficaria com imposto em branco. Restaura o mesmo que a clonagem do
    INSERT gravaria (por empresa, já que GRUPOICMS varia entre elas) — o produto volta ao
    estado "clonado, sem recálculo".
    """
    empresas_base, produto_base = _ler_base_modelo()
    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            raise RuntimeError("Falha na conexão com o banco")

        cursor = conexao.cursor()
        for emp in empresas_base:
            cursor.execute(
                """
                UPDATE TGFPEM
                SET GRUPOICMS = :G, TEMICMS = :T, CODESPECST = :C
                WHERE CODPROD = :COD AND CODEMP = :E
                """,
                {
                    "G": emp["GRUPOICMS"],
                    "T": emp["TEMICMS"],
                    "C": emp["CODESPECST"],
                    "COD": cod_prod,
                    "E": emp["CODEMP"],
                },
            )
        cursor.execute(
            """
            UPDATE TGFPRO
            SET TEMICMS = :T, CODESPECST = :C
            WHERE CODPROD = :COD
            """,
            {"T": produto_base["TEMICMS"], "C": produto_base["CODESPECST"], "COD": cod_prod},
        )
        conexao.commit()
    except Exception:
        if conexao:
            conexao.rollback()
        raise
    finally:
        if conexao:
            conexao.close()


def recalcular_impostos(cod_prod):
    """Reprocessa a tributação de `cod_prod` via Sankhya, usando o modelo 11783 como base.

    1) EmpresaProdutoImpostos: uma chamada por empresa do modelo em TGFPEM.
    2) Produto: uma chamada.

    Devolve um dict com as respostas cruas do Sankhya. Levanta RuntimeError em qualquer
    falha (credencial, autenticação ou save recusado).
    """
    empresas_base, produto_base = _ler_base_modelo()
    if not empresas_base:
        raise RuntimeError(
            f"Produto-modelo {CODPROD_MODELO} não tem linhas em TGFPEM (nenhuma empresa)."
        )

    token = autenticar_sankhya()

    campos_emp = ["CODPROD", "CODEMP", "GRUPOICMS", "TEMICMS", "TIPSUBST", "USOPROD", "CODESPECST"]
    resultados_empresas = []
    for emp in empresas_base:
        dados = {
            "CODPROD": cod_prod,
            "CODEMP": emp["CODEMP"],
            "GRUPOICMS": emp["GRUPOICMS"],
            "TEMICMS": emp["TEMICMS"],
            "TIPSUBST": emp["TIPSUBST"],
            "USOPROD": emp["USOPROD"],
            "CODESPECST": emp["CODESPECST"],
        }
        record = _montar_record(campos_emp, {"CODPROD", "CODEMP"}, dados)
        resposta = _salvar_dataset(token, "EmpresaProdutoImpostos", campos_emp, record)
        resultados_empresas.append({"codEmp": emp["CODEMP"], "resposta": resposta})

    campos_prod = ["CODPROD", "TEMICMS", "TIPSUBST", "USOPROD", "CODESPECST"]
    dados_prod = {
        "CODPROD": cod_prod,
        "TEMICMS": produto_base["TEMICMS"],
        "TIPSUBST": produto_base["TIPSUBST"],
        "USOPROD": produto_base["USOPROD"],
        "CODESPECST": produto_base["CODESPECST"],
    }
    record_prod = _montar_record(campos_prod, {"CODPROD"}, dados_prod)
    resposta_produto = _salvar_dataset(token, "Produto", campos_prod, record_prod)

    return {"empresas": resultados_empresas, "produto": resposta_produto}


@bp.route("/api/teste-recalcular-impostos", methods=["POST"])
def teste_recalcular_impostos():
    """[PROVISÓRIA] Testa o recálculo de impostos via Sankhya num produto já existente.

    Body: { "codProd": <int>, "limparAntes": <bool, opcional> }

    Com "limparAntes": true, zera os campos tributários no banco ANTES do save (teste da
    hipótese 1 — forçar que o DatasetSP.save seja uma mudança real).
    """
    data = request.get_json(silent=True)
    if not data or data.get("codProd") is None:
        return jsonify({"erro": "Parâmetro 'codProd' é obrigatório."}), 400

    cod_prod = data.get("codProd")
    limpar_antes = bool(data.get("limparAntes", False))

    try:
        limpeza = limpar_impostos(cod_prod) if limpar_antes else None
        resultado = recalcular_impostos(cod_prod)
        resposta = {
            "sucesso": True,
            "codProd": cod_prod,
            "empresas": resultado["empresas"],
            "produto": resultado["produto"],
        }
        if limpeza is not None:
            resposta["limpezaAntes"] = limpeza
        return jsonify(resposta)
    except cx_Oracle.Error as err:
        print("Erro Oracle:", err)
        return jsonify({"erro": f"Erro de Banco de Dados: {err}"}), 500
    except requests.RequestException as err:
        print("Erro HTTP Sankhya:", err)
        return jsonify({"erro": f"Falha de comunicação com o Sankhya: {err}"}), 502
    except RuntimeError as err:
        print("Erro Sankhya:", err)
        return jsonify({"erro": str(err)}), 500
    except Exception as err:
        print("Erro Geral:", err)
        return jsonify({"erro": str(err)}), 500
