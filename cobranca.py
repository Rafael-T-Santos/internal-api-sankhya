"""Rotas do módulo de Cobrança (Dashboard Operacional).

Separado do app.py porque o monolito já serve 5 domínios; a cobrança vai crescer
(régua de chamadas, jurídico, negativação) e não cabe lá dentro.

Os paths das 4 rotas antigas foram preservados (/api/cidades, /api/vendedores,
/api/parceiros, /api/receitas-vencidas) para não quebrar o app que já as consome.
As rotas novas ficam sob /api/cobranca/*.
"""
import cx_Oracle
from flask import Blueprint, jsonify, request

from db import conectar_oracle

bp = Blueprint("cobranca", __name__)


def _erro(err, código=500):
    print("Erro:", err)
    return jsonify({"erro": str(err)}), código


# --- Listas de apoio (filtros) ---

@bp.route("/api/cidades", methods=["GET"])
def cidades():
    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500

        cursor = conexao.cursor()
        cursor.execute("SELECT CODCID, UF, NOMECID FROM TSICID ORDER BY NOMECID")
        dados = [
            {"codCid": r[0], "uf": r[1], "nomeCid": r[2]} for r in cursor.fetchall()
        ]
        return jsonify({"sucesso": True, "totalRegistros": len(dados), "dados": dados})
    except Exception as e:
        return _erro(e)
    finally:
        if conexao:
            conexao.close()


@bp.route("/api/vendedores", methods=["GET"])
def vendedores():
    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500

        cursor = conexao.cursor()
        cursor.execute("SELECT CODVEND, APELIDO FROM TGFVEN ORDER BY APELIDO")
        dados = [{"codVend": r[0], "apelido": r[1]} for r in cursor.fetchall()]
        return jsonify({"sucesso": True, "totalRegistros": len(dados), "dados": dados})
    except Exception as e:
        return _erro(e)
    finally:
        if conexao:
            conexao.close()


@bp.route("/api/parceiros", methods=["GET"])
def parceiros():
    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500

        cursor = conexao.cursor()
        cursor.execute(
            "SELECT CODPARC, NOMEPARC, RAZAOSOCIAL, CGC_CPF FROM TGFPAR ORDER BY NOMEPARC"
        )
        dados = [
            {"codParc": r[0], "nomeParc": r[1], "razaoSocial": r[2], "cgcCpf": r[3]}
            for r in cursor.fetchall()
        ]
        return jsonify({"sucesso": True, "totalRegistros": len(dados), "dados": dados})
    except Exception as e:
        return _erro(e)
    finally:
        if conexao:
            conexao.close()


# --- Títulos vencidos (tela de cobrança) ---

@bp.route("/api/receitas-vencidas", methods=["POST"])
def receitas_vencidas():
    data = request.get_json() or {}

    cod_emp    = data.get("codEmp")
    cod_parc   = data.get("codParc")
    cod_vend   = data.get("codVend")
    cod_cid    = data.get("codCid")
    dt_inicial = data.get("dtInicial")
    dt_final   = data.get("dtFinal")

    sql_base = """
    SELECT
        FIN.NOSSONUM,
        FIN.NUCOMPENS,
        FIN.NURENEG,
        FIN.NUNOTA,
        FIN.DTNEG,
        FIN.DTVENC,
        FIN.NUFIN,
        FIN.NUMNOTA,
        FIN.VLRDESDOB,
        VFIN.VLRLIQUIDO,
        FIN.VLRCHEQUE,
        FIN.HISTORICO,
        CTA.DESCRICAO,
        PAR.RAZAOSOCIAL,
        FIN.CODPARC,
        PAR.NOMEPARC,
        PAR.TELEFONE,
        CID.CODCID,
        CID.NOMECID,
        CID.UF,
        FIN.CGC_CPF_CMC7,
        TIT.DESCRTIPTIT,
        CASE
            WHEN FIN.CODTIPTIT = 3 AND FIN.CODTIPOPER = 1657 AND FIN.DHBAIXA IS NULL
                THEN 'CHEQUE DEVOLVIDO PENDENTE'
            WHEN FIN.CODTIPTIT = 3 AND FIN.DHBAIXA IS NOT NULL AND FIN.CODCTABCOINT = 16 AND NVL(FIN.AD_ACERTADO, 'N') = 'N'
                THEN 'CHEQUE VENCIDO PENDENTE - BAIXADO NA CONTA 16'
            WHEN FIN.CODTIPTIT = 3 AND FIN.DHBAIXA IS NULL AND NVL(FIN.AD_ACERTADO, 'N') = 'N'
                THEN 'CHEQUE VENCIDO SEM BAIXA'
            WHEN FIN.CODTIPTIT IN (4, 5, 39) AND FIN.DHBAIXA IS NULL
                THEN 'TITULO VENCIDO SEM PAGAMENTO'
        END AS SITUACAO,
        NVL(FIN.VLRDESC, 0),
        CASE
            WHEN LENGTH(REGEXP_REPLACE(PAR.CGC_CPF, '[^0-9]', '')) = 14 THEN
                REGEXP_REPLACE(REGEXP_REPLACE(PAR.CGC_CPF, '[^0-9]', ''), '([0-9]{2})([0-9]{3})([0-9]{3})([0-9]{4})([0-9]{2})', '\\1.\\2.\\3/\\4-\\5')
            WHEN LENGTH(REGEXP_REPLACE(PAR.CGC_CPF, '[^0-9]', '')) = 11 THEN
                REGEXP_REPLACE(REGEXP_REPLACE(PAR.CGC_CPF, '[^0-9]', ''), '([0-9]{3})([0-9]{3})([0-9]{3})([0-9]{2})', '\\1.\\2.\\3-\\4')
            ELSE PAR.CGC_CPF
        END AS CNPJ_CPF,
        NVL(FIN.VLRJURO, 0),
        GREATEST(TRUNC(SYSDATE) - TRUNC(FIN.DTVENC), 0) AS ATRASO_DIAS,
        NVL(VEN.APELIDO, 'SEM VENDEDOR'),
        FIN.CODOBSPADRAO,
        OBS.OBSERVACAO,
        FIN.DESDOBRAMENTO,
        FIN.NOMEEMITENTE_CMC7
    FROM TGFFIN FIN
        INNER JOIN TGFPAR PAR ON PAR.CODPARC = FIN.CODPARC
        LEFT JOIN TSICID CID ON CID.CODCID = PAR.CODCID
        LEFT JOIN TGFTIT TIT ON TIT.CODTIPTIT = FIN.CODTIPTIT
        LEFT JOIN TGFVEN VEN ON VEN.CODVEND = FIN.CODVEND
        LEFT JOIN TSICTA CTA ON CTA.CODCTABCOINT = FIN.CODCTABCOINT
        LEFT JOIN TGFOBS OBS ON OBS.CODOBSPADRAO = FIN.CODOBSPADRAO
        LEFT JOIN VGFFIN VFIN ON VFIN.NUFIN = FIN.NUFIN
    WHERE FIN.RECDESP = 1
      AND FIN.CODTIPTIT IN (3, 4, 5, 39)
      AND NVL(FIN.PROVISAO, 'N') = 'N'
      AND FIN.NURENEG IS NULL
      AND (
            (FIN.CODTIPTIT IN (4, 5, 39) AND FIN.DHBAIXA IS NULL AND FIN.DTVENC < TRUNC(SYSDATE))
            OR
            (FIN.CODTIPTIT = 3 AND FIN.CODTIPOPER = 1657 AND FIN.DHBAIXA IS NULL AND NVL(FIN.AD_ACERTADO, 'N') = 'N')
            OR
            (
                FIN.CODTIPTIT = 3
                AND NVL(FIN.CODTIPOPER, 0) <> 1657
                AND FIN.DTVENC < TRUNC(SYSDATE)
                AND NVL(FIN.AD_ACERTADO, 'N') = 'N'
                AND FIN.NUCOMPENS IS NULL
                AND (FIN.DHBAIXA IS NULL OR FIN.CODCTABCOINT = 16)
                AND (
                    FIN.NUCHQ IS NULL
                    OR NOT EXISTS (
                        SELECT 1 FROM TGFFIN DEV
                        WHERE DEV.NUCHQ = FIN.NUCHQ AND DEV.RECDESP = 1
                          AND DEV.CODTIPTIT = 3 AND DEV.CODTIPOPER = 1657 AND DEV.DHBAIXA IS NULL
                    )
                )
            )
          )
    """

    filtros_extras = []
    params = {}

    if cod_emp:
        filtros_extras.append("AND FIN.CODEMP = :CODEMP")
        params["CODEMP"] = cod_emp
    if cod_parc:
        filtros_extras.append("AND FIN.CODPARC = :CODPARC")
        params["CODPARC"] = cod_parc
    if cod_vend:
        filtros_extras.append("AND FIN.CODVEND = :CODVEND")
        params["CODVEND"] = cod_vend
    if cod_cid:
        filtros_extras.append("AND PAR.CODCID = :CODCID")
        params["CODCID"] = cod_cid
    if dt_inicial and dt_final:
        filtros_extras.append(
            "AND FIN.DTVENC BETWEEN TO_DATE(:DT_INICIAL, 'YYYY-MM-DD') AND TO_DATE(:DT_FINAL, 'YYYY-MM-DD')"
        )
        params["DT_INICIAL"] = dt_inicial
        params["DT_FINAL"] = dt_final

    sql_final = (
        sql_base
        + "\n".join(filtros_extras)
        + "\nORDER BY FIN.DTVENC, PAR.NOMEPARC, FIN.NUFIN"
    )

    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500

        cursor = conexao.cursor()
        cursor.execute(sql_final, params)

        dados = []
        for row in cursor.fetchall():
            dados.append({
                "nossoNum":        row[0],
                "nuCompens":       row[1],
                "nuReneg":         row[2],
                "nuNota":          row[3],
                "dtNeg":           row[4].strftime('%Y-%m-%d') if row[4] else None,
                "dtVenc":          row[5].strftime('%Y-%m-%d') if row[5] else None,
                "nuFin":           row[6],
                "numNota":         row[7],
                "vlrDesdob":       float(row[8]) if row[8] is not None else None,
                "vlrLiquido":      float(row[9]) if row[9] is not None else None,
                "vlrCheque":       float(row[10]) if row[10] is not None else None,
                "historico":       row[11],
                "contaBancaria":   row[12],
                "razaoSocial":     row[13],
                "codParc":         row[14],
                "nomeParc":        row[15],
                "telefone":        row[16],
                "codCid":          row[17],
                "nomeCid":         row[18],
                "uf":              row[19],
                "cgcCpfCmc7":      row[20],
                "tipoTitulo":      row[21],
                "situacao":        row[22],
                "vlrDesconto":     float(row[23]) if row[23] is not None else 0.0,
                "cnpjCpf":         row[24],
                "vlrJuros":        float(row[25]) if row[25] is not None else 0.0,
                "atrasoDias":      int(row[26]) if row[26] is not None else 0,
                "vendedor":        row[27],
                "codObsPadrao":    row[28],
                "observacao":      row[29],
                "desdobramento":   row[30],
                "nomeEmitente":    row[31],
            })

        return jsonify({"sucesso": True, "totalRegistros": len(dados), "dados": dados})

    except cx_Oracle.Error as err:
        return _erro(f"Erro de Banco de Dados: {err}")
    except Exception as e:
        return _erro(e)
    finally:
        if conexao:
            conexao.close()


# --- Visão 360° do cliente ---

SQL_CLIENTE = """
SELECT
    PAR.CODPARC,
    PAR.NOMEPARC,
    PAR.RAZAOSOCIAL,
    PAR.CGC_CPF,
    PAR.TELEFONE,
    PAR.EMAIL,
    NVL(PAR.LIMCRED, 0)          AS LIMCRED,
    PAR.ATIVO,
    CID.NOMECID,
    CID.UF,
    NVL(VEN.APELIDO, 'SEM VENDEDOR') AS VENDEDOR
FROM TGFPAR PAR
    LEFT JOIN TSICID CID ON CID.CODCID  = PAR.CODCID
    LEFT JOIN TGFVEN VEN ON VEN.CODVEND = PAR.CODVEND
WHERE PAR.CODPARC = :CODPARC
"""

# Pontualidade: dos títulos QUITADOS nos últimos 12 meses, quantos foram pagos
# até a data de vencimento. É um número objetivo, extraído do próprio histórico —
# não é o "score de risco" do protótipo, que ainda depende de política da gerência.
SQL_PONTUALIDADE = """
SELECT
    COUNT(*)                                                              AS QUITADOS,
    SUM(CASE WHEN TRUNC(FIN.DHBAIXA) <= TRUNC(FIN.DTVENC) THEN 1 ELSE 0 END) AS EM_DIA
FROM TGFFIN FIN
WHERE FIN.CODPARC = :CODPARC
  AND FIN.RECDESP = 1
  AND FIN.DHBAIXA IS NOT NULL
  AND NVL(FIN.PROVISAO, 'N') = 'N'
  AND FIN.DHBAIXA >= ADD_MONTHS(TRUNC(SYSDATE), -12)
"""

# Extrato: TODOS os títulos em aberto do cliente — vencidos E a vencer.
# (o /api/receitas-vencidas só traz os vencidos; aqui a Visão 360° precisa dos dois)
SQL_EXTRATO = """
SELECT
    FIN.NUFIN,
    FIN.NUMNOTA,
    FIN.NUNOTA,
    FIN.DESDOBRAMENTO,
    FIN.DTNEG,
    FIN.DTVENC,
    FIN.VLRDESDOB,
    FIN.VLRCHEQUE,
    VFIN.VLRLIQUIDO,
    NVL(FIN.VLRJURO, 0)  AS VLRJUROS,
    NVL(FIN.VLRDESC, 0)  AS VLRDESCONTO,
    TIT.DESCRTIPTIT,
    FIN.HISTORICO,
    GREATEST(TRUNC(SYSDATE) - TRUNC(FIN.DTVENC), 0) AS ATRASO_DIAS,
    CASE WHEN FIN.DTVENC < TRUNC(SYSDATE) THEN 'VENCIDO' ELSE 'A_VENCER' END AS SITUACAO
FROM TGFFIN FIN
    LEFT JOIN TGFTIT TIT  ON TIT.CODTIPTIT = FIN.CODTIPTIT
    LEFT JOIN VGFFIN VFIN ON VFIN.NUFIN    = FIN.NUFIN
WHERE FIN.CODPARC = :CODPARC
  AND FIN.RECDESP = 1
  AND FIN.DHBAIXA IS NULL
  AND NVL(FIN.PROVISAO, 'N') = 'N'
  AND FIN.NURENEG IS NULL
ORDER BY FIN.DTVENC, FIN.NUFIN
"""


@bp.route("/api/cobranca/cliente", methods=["POST"])
def cliente():
    """Identificação + KPIs do cliente para o topo da Visão 360°."""
    data = request.get_json() or {}
    cod_parc = data.get("codParc")
    if not cod_parc:
        return jsonify({"erro": "Parâmetro 'codParc' é obrigatório."}), 400

    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500

        cursor = conexao.cursor()

        cursor.execute(SQL_CLIENTE, {"CODPARC": cod_parc})
        row = cursor.fetchone()
        if not row:
            return jsonify({"erro": f"Cliente {cod_parc} não encontrado."}), 404

        cursor.execute(SQL_PONTUALIDADE, {"CODPARC": cod_parc})
        quitados, em_dia = cursor.fetchone()
        quitados = int(quitados or 0)
        em_dia = int(em_dia or 0)

        return jsonify({
            "sucesso": True,
            "dados": {
                "codParc":     row[0],
                "nomeParc":    row[1],
                "razaoSocial": row[2],
                "cgcCpf":      row[3],
                "telefone":    row[4],
                "email":       row[5],
                "limiteCredito": float(row[6]) if row[6] is not None else 0.0,
                "ativo":       row[7],
                "nomeCid":     row[8],
                "uf":          row[9],
                "vendedor":    row[10],
                # null quando não há histórico: o front mostra "sem histórico",
                # em vez de fingir que 0% de pontualidade é um fato.
                "pontualidade": round(em_dia * 100.0 / quitados, 1) if quitados else None,
                "titulosQuitados12m": quitados,
            },
        })

    except cx_Oracle.Error as err:
        return _erro(f"Erro de Banco de Dados: {err}")
    except Exception as e:
        return _erro(e)
    finally:
        if conexao:
            conexao.close()


@bp.route("/api/cobranca/extrato", methods=["POST"])
def extrato():
    """Extrato unificado: títulos em aberto do cliente (vencidos e a vencer)."""
    data = request.get_json() or {}
    cod_parc = data.get("codParc")
    if not cod_parc:
        return jsonify({"erro": "Parâmetro 'codParc' é obrigatório."}), 400

    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500

        cursor = conexao.cursor()
        cursor.execute(SQL_EXTRATO, {"CODPARC": cod_parc})

        dados = []
        for r in cursor.fetchall():
            dados.append({
                "nuFin":         r[0],
                "numNota":       r[1],
                "nuNota":        r[2],
                "desdobramento": r[3],
                "dtNeg":         r[4].strftime('%Y-%m-%d') if r[4] else None,
                "dtVenc":        r[5].strftime('%Y-%m-%d') if r[5] else None,
                "vlrDesdob":     float(r[6]) if r[6] is not None else None,
                "vlrCheque":     float(r[7]) if r[7] is not None else None,
                "vlrLiquido":    float(r[8]) if r[8] is not None else None,
                "vlrJuros":      float(r[9]) if r[9] is not None else 0.0,
                "vlrDesconto":   float(r[10]) if r[10] is not None else 0.0,
                "tipoTitulo":    r[11],
                "historico":     r[12],
                "atrasoDias":    int(r[13]) if r[13] is not None else 0,
                "situacao":      r[14],
            })

        return jsonify({"sucesso": True, "totalRegistros": len(dados), "dados": dados})

    except cx_Oracle.Error as err:
        return _erro(f"Erro de Banco de Dados: {err}")
    except Exception as e:
        return _erro(e)
    finally:
        if conexao:
            conexao.close()
