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


def _erro(err, codigo=500):
    print("Erro:", err)
    return jsonify({"erro": str(err)}), codigo


def _txt(valor):
    """Texto do banco: espaços em branco viram None.

    Campos como TGFPAR.EMAIL vêm preenchidos com espaços ("  ") em vez de NULL.
    Uma string dessas é "verdadeira" em JS e o front acabaria exibindo um campo
    vazio em vez de "sem e-mail".
    """
    if valor is None:
        return None
    limpo = str(valor).strip()
    return limpo or None


# ---------------------------------------------------------------------------
# RECDESP: por que = 1, e NUNCA 0
#
# RECDESP não é "receita vs. despesa" como o nome sugere:
#    1 → título ATIVO de receita. É o que a cobrança persegue.
#    0 → título NEUTRALIZADO: a origem de uma renegociação. Já foi substituído
#        por outro(s) título(s) e NÃO é mais dívida. Nunca tem baixa, por isso
#        parece "vencido para sempre".
#   -1 → despesa (compra, folha, tributos).
#
# Confirmado com o responsável pela cobrança em 2026-07-13. Incluir o 0 conta a
# mesma dívida DUAS VEZES (o título velho renegociado + o novo que o substituiu):
# chegamos a inflar a carteira em R$ 4,1 milhões de "VENDA GERENCIAL - A VISTA"
# que já estavam renegociados. NÃO reabrir o filtro para 0.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Regra de cheques (pendentes e devolvidos)
#
# Cheque não segue a regra dos demais títulos: um cheque pendente pode estar
# BAIXADO (na conta 16) e um cheque devolvido entra pela TOP 1657. Por isso a
# regra vive nestas CTEs, reaproveitadas pela consulta de títulos vencidos e
# pelo extrato da Visão 360°.
#
# Fonte: relatório oficial de cheques pendentes/devolvidos do Sankhya.
# Para cheque, o vencimento que vale é o "bom para" (TGFCHQ.DATACHEQUE) — e não
# o DTVENC do financeiro. O cálculo de atraso segue essa data.
# ---------------------------------------------------------------------------
CTE_CHEQUES = """
WITH ULT_EVENTO AS (
    SELECT
        ECQ.NUCHQ,
        ECQ.NUEVENTO,
        ECQ.DHEVENTO,
        ECQ.TIPO,
        ROW_NUMBER() OVER (
            PARTITION BY ECQ.NUCHQ
            ORDER BY ECQ.NUEVENTO DESC, ECQ.DHEVENTO DESC
        ) AS RN
    FROM TGFECQ ECQ
),
CHQ_NORMAL AS (
    SELECT
        'P' AS STATUS_REGRA,
        'CHQ_NORMAL' AS ORIGEM_REGRA,
        FIN.NUFIN,
        TO_CHAR(CHQ.NUMCHEQUE) AS CHEQUE,
        ROUND(NVL(NULLIF(CHQ.VLRCHEQUE, 0), NVL(FIN.VLRDESDOB, 0)), 2) AS VLRCHEQUE_REGRA,
        NVL(CHQ.DATACHEQUE, FIN.DTVENC) AS DATACHEQUE_REGRA,
        'Pendente' AS ULTIMO_EVENTO_REGRA
    FROM TGFFIN FIN
    JOIN TGFCHQ CHQ ON CHQ.NUFIN = FIN.NUFIN
    JOIN ULT_EVENTO ULT ON ULT.NUCHQ = CHQ.NUCHQ AND ULT.RN = 1
    WHERE FIN.CODTIPTIT = 3
      AND FIN.RECDESP = 1
      AND NVL(FIN.PROVISAO, 'N') = 'N'
      AND FIN.AD_ACERTADO = 'N'
      AND NVL(FIN.CODTIPOPER, 0) <> 1657
      AND FIN.DHBAIXA IS NOT NULL
      AND NVL(FIN.VLRBAIXA, 0) > 0
      AND NVL(FIN.CODCTABCOINT, -1) = 16
      AND ULT.TIPO = 'B'
      AND NVL(CHQ.STATUS, 'X') <> 'T'
      AND NVL(FIN.NUCOMPENS, 0) = 0
      /* Exclui só o título de ORIGEM da renegociação; o de destino continua entrando. */
      AND NOT EXISTS (
          SELECT 1 FROM TGFREN REN_ORIG WHERE REN_ORIG.NUFIN = FIN.NUFIN
      )
      AND NVL(CHQ.DATACHEQUE, FIN.DTVENC) IS NOT NULL
      AND REGEXP_LIKE(TRIM(TO_CHAR(CHQ.NUMCHEQUE)), '[1-9]')
      AND REGEXP_LIKE(
          REGEXP_REPLACE(TRIM(TO_CHAR(NVL(FIN.CONTA_CMC7, CHQ.CMC7))), '[^0-9]', ''),
          '[1-9]'
      )
      AND NOT EXISTS (
          SELECT 1 FROM TGFFRE FRE
          WHERE FRE.NUFIN = FIN.NUFIN AND FRE.TIPACERTO = 'C'
      )
      AND NOT EXISTS (
          SELECT 1 FROM TGFECQ EVTPAG
          WHERE EVTPAG.NUCHQ = CHQ.NUCHQ
            AND EVTPAG.TIPO IN ('P', 'T')
            AND NOT EXISTS (
                SELECT 1 FROM TGFECQ EVTDEV
                WHERE EVTDEV.NUCHQ = EVTPAG.NUCHQ
                  AND EVTDEV.TIPO = 'D'
                  AND NVL(EVTDEV.NUEVENTO, 0) > NVL(EVTPAG.NUEVENTO, 0)
            )
      )
      AND NOT EXISTS (
          SELECT 1 FROM TGFFIN DEV
          WHERE DEV.CODPARC = FIN.CODPARC
            AND DEV.RECDESP = 1
            AND DEV.CODTIPOPER = 1657
            AND NVL(DEV.PROVISAO, 'N') = 'N'
            AND (
                  REGEXP_LIKE(
                      UPPER(NVL(DEV.HISTORICO, ' ')),
                      '(^|[^0-9])0*' ||
                      NVL(NULLIF(LTRIM(TO_CHAR(CHQ.NUMCHEQUE), '0'), ''), '0') ||
                      '([^0-9]|$)'
                  )
                  OR
                  NVL(NULLIF(LTRIM(TO_CHAR(DEV.NUMNOTA), '0'), ''), '0') =
                  NVL(NULLIF(LTRIM(TO_CHAR(CHQ.NUMCHEQUE), '0'), ''), '0')
            )
      )
),
DEV_1657 AS (
    SELECT
        'D' AS STATUS_REGRA,
        'TOP_1657' AS ORIGEM_REGRA,
        FIN.NUFIN,
        COALESCE(
            REGEXP_REPLACE(
                REGEXP_SUBSTR(
                    UPPER(NVL(FIN.HISTORICO, '')),
                    '(CH|CHQ|CHEQ|CHEQUE)[^0-9]{0,30}[0-9]{1,10}'
                ), '[^0-9]', ''
            ),
            REGEXP_REPLACE(
                REGEXP_SUBSTR(
                    UPPER(NVL(FIN.HISTORICO, '')),
                    '[0-9]{1,10}[[:space:]-]*(CH|CHQ|CHEQ|CHEQUE)'
                ), '[^0-9]', ''
            ),
            TO_CHAR(FIN.NUMNOTA)
        ) AS CHEQUE,
        ROUND(NVL(FIN.VLRDESDOB, 0), 2) AS VLRCHEQUE_REGRA,
        FIN.DTVENC AS DATACHEQUE_REGRA,
        'Devolução' AS ULTIMO_EVENTO_REGRA
    FROM TGFFIN FIN
    WHERE FIN.CODTIPTIT = 3
      AND FIN.RECDESP = 1
      AND NVL(FIN.PROVISAO, 'N') = 'N'
      AND FIN.AD_ACERTADO = 'N'
      AND FIN.CODTIPOPER = 1657
      AND FIN.DHBAIXA IS NULL
      AND NVL(FIN.VLRBAIXA, 0) = 0
      AND NVL(FIN.NUCOMPENS, 0) = 0
      AND NOT EXISTS (
          SELECT 1 FROM TGFREN REN_ORIG WHERE REN_ORIG.NUFIN = FIN.NUFIN
      )
      AND FIN.DTVENC IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM TGFFRE FRE
          WHERE FRE.NUFIN = FIN.NUFIN AND FRE.TIPACERTO = 'C'
      )
),
CHEQUES_REGRA AS (
    SELECT STATUS_REGRA, ORIGEM_REGRA, NUFIN, CHEQUE,
           VLRCHEQUE_REGRA, DATACHEQUE_REGRA, ULTIMO_EVENTO_REGRA
    FROM CHQ_NORMAL
    UNION ALL
    SELECT STATUS_REGRA, ORIGEM_REGRA, NUFIN, CHEQUE,
           VLRCHEQUE_REGRA, DATACHEQUE_REGRA, ULTIMO_EVENTO_REGRA
    FROM DEV_1657
)
"""

# Data de vencimento "que vale": bom-para do cheque, ou DTVENC dos demais.
DT_EFETIVA = """
    CASE WHEN FIN.CODTIPTIT = 3 THEN CHR.DATACHEQUE_REGRA ELSE FIN.DTVENC END
"""

# JOINs comuns às consultas de títulos.
JOINS_TITULO = """
    FROM TGFFIN FIN
        INNER JOIN TGFPAR PAR  ON PAR.CODPARC      = FIN.CODPARC
        LEFT JOIN TSICID CID   ON CID.CODCID       = PAR.CODCID
        LEFT JOIN TSIUFS UFS   ON UFS.CODUF        = CID.UF
        LEFT JOIN TGFTIT TIT   ON TIT.CODTIPTIT    = FIN.CODTIPTIT
        LEFT JOIN TGFVEN VEN   ON VEN.CODVEND      = FIN.CODVEND
        LEFT JOIN TSICTA CTA   ON CTA.CODCTABCOINT = FIN.CODCTABCOINT
        LEFT JOIN TGFOBS OBS   ON OBS.CODOBSPADRAO = FIN.CODOBSPADRAO
        LEFT JOIN VGFFIN VFIN  ON VFIN.NUFIN       = FIN.NUFIN
        LEFT JOIN CHEQUES_REGRA CHR ON CHR.NUFIN   = FIN.NUFIN
        LEFT JOIN TGFTOP TOP   ON TOP.CODTIPOPER   = FIN.CODTIPOPER
                              AND TOP.DHALTER      = FIN.DHTIPOPER
"""


# --- Listas de apoio (filtros) ---

@bp.route("/api/cidades", methods=["GET"])
def cidades():
    # TSICID.UF guarda o CÓDIGO da UF; a sigla vem da TSIUFS.
    sql = """
    SELECT CID.CODCID, CID.NOMECID, UFS.UF
    FROM TSICID CID
        LEFT JOIN TSIUFS UFS ON UFS.CODUF = CID.UF
    ORDER BY CID.NOMECID
    """
    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500

        cursor = conexao.cursor()
        cursor.execute(sql)
        dados = [
            {"codCid": r[0], "nomeCid": r[1], "uf": r[2]} for r in cursor.fetchall()
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


# --- Títulos vencidos / cheques pendentes ---

SELECT_RECEITAS = f"""
SELECT
    FIN.NOSSONUM,
    FIN.NUCOMPENS,
    FIN.NUNOTA,
    FIN.DTNEG,
    {DT_EFETIVA} AS DT_VENCIMENTO,
    FIN.NUFIN,
    FIN.NUMNOTA,
    FIN.VLRDESDOB,
    VFIN.VLRLIQUIDO,
    CASE WHEN FIN.CODTIPTIT = 3 THEN CHR.VLRCHEQUE_REGRA ELSE FIN.VLRCHEQUE END AS VLR_CHEQUE,
    CASE WHEN FIN.CODTIPTIT = 3 THEN CHR.CHEQUE END AS NUMERO_CHEQUE,
    FIN.HISTORICO,
    CTA.DESCRICAO,
    PAR.RAZAOSOCIAL,
    FIN.CODPARC,
    PAR.NOMEPARC,
    PAR.TELEFONE,
    CID.CODCID,
    CID.NOMECID,
    UFS.UF,
    FIN.CGC_CPF_CMC7,
    FIN.CODTIPTIT,
    TIT.DESCRTIPTIT,
    CASE
        WHEN FIN.CODTIPTIT = 3 AND CHR.STATUS_REGRA = 'P' THEN 'CHEQUE PENDENTE'
        WHEN FIN.CODTIPTIT = 3 AND CHR.STATUS_REGRA = 'D' THEN 'CHEQUE DEVOLVIDO'
        WHEN FIN.CODTIPTIT <> 3 AND FIN.NURENEG IS NOT NULL
            THEN 'TÍTULO RENEGOCIADO VENCIDO SEM PAGAMENTO'
        WHEN FIN.CODTIPTIT IN (2, 4, 5, 39)               THEN 'TÍTULO VENCIDO SEM PAGAMENTO'
    END AS SITUACAO,
    CASE WHEN FIN.CODTIPTIT = 3 THEN CHR.ULTIMO_EVENTO_REGRA END AS ULTIMO_EVENTO,
    CASE WHEN FIN.CODTIPTIT = 3 THEN CHR.ORIGEM_REGRA END       AS ORIGEM_REGRA,
    FIN.NURENEG,
    NVL(FIN.VLRDESC, 0),
    CASE
        WHEN LENGTH(REGEXP_REPLACE(PAR.CGC_CPF, '[^0-9]', '')) = 14 THEN
            REGEXP_REPLACE(REGEXP_REPLACE(PAR.CGC_CPF, '[^0-9]', ''), '([0-9]{{2}})([0-9]{{3}})([0-9]{{3}})([0-9]{{4}})([0-9]{{2}})', '\\1.\\2.\\3/\\4-\\5')
        WHEN LENGTH(REGEXP_REPLACE(PAR.CGC_CPF, '[^0-9]', '')) = 11 THEN
            REGEXP_REPLACE(REGEXP_REPLACE(PAR.CGC_CPF, '[^0-9]', ''), '([0-9]{{3}})([0-9]{{3}})([0-9]{{3}})([0-9]{{2}})', '\\1.\\2.\\3-\\4')
        ELSE PAR.CGC_CPF
    END AS CNPJ_CPF,
    NVL(FIN.VLRJURO, 0),
    GREATEST(TRUNC(SYSDATE) - TRUNC({DT_EFETIVA}), 0) AS ATRASO_DIAS,
    NVL(VEN.APELIDO, 'SEM VENDEDOR'),
    FIN.CODOBSPADRAO,
    OBS.OBSERVACAO,
    FIN.DESDOBRAMENTO,
    FIN.NOMEEMITENTE_CMC7,
    FIN.RECDESP,
    FIN.CODTIPOPER,
    TOP.DESCROPER
{JOINS_TITULO}
WHERE FIN.RECDESP = 1
  AND NVL(FIN.PROVISAO, 'N') = 'N'
  AND (
        /* Títulos normais de cobrança. */
        (FIN.CODTIPTIT IN (2, 4, 5, 39) AND FIN.DHBAIXA IS NULL AND FIN.DTVENC < TRUNC(SYSDATE))
        OR
        /* Cheques: só os aprovados pela regra do relatório. */
        (FIN.CODTIPTIT = 3 AND CHR.NUFIN IS NOT NULL)
        OR
        /* Título ATIVO gerado por renegociação: pode ter tipo fora da lista
           (PIX, cartão...). O título de ORIGEM não entra — ele é RECDESP = 0. */
        (
            FIN.CODTIPTIT NOT IN (2, 3, 4, 5, 39)
            AND FIN.NURENEG IS NOT NULL
            AND FIN.DHBAIXA IS NULL
            AND FIN.DTVENC < TRUNC(SYSDATE)
        )
      )
"""


@bp.route("/api/receitas-vencidas", methods=["POST"])
def receitas_vencidas():
    data = request.get_json() or {}

    cod_emp    = data.get("codEmp")
    cod_parc   = data.get("codParc")
    cod_vend   = data.get("codVend")
    cod_cid    = data.get("codCid")
    dt_inicial = data.get("dtInicial")
    dt_final   = data.get("dtFinal")

    filtros = []
    params = {}

    if cod_emp:
        filtros.append("AND FIN.CODEMP = :CODEMP")
        params["CODEMP"] = cod_emp
    if cod_parc:
        filtros.append("AND FIN.CODPARC = :CODPARC")
        params["CODPARC"] = cod_parc
    if cod_vend:
        filtros.append("AND FIN.CODVEND = :CODVEND")
        params["CODVEND"] = cod_vend
    if cod_cid:
        filtros.append("AND PAR.CODCID = :CODCID")
        params["CODCID"] = cod_cid
    if dt_inicial and dt_final:
        # O período usa a data efetiva: bom-para do cheque, DTVENC dos demais.
        filtros.append(
            f"AND TRUNC({DT_EFETIVA}) BETWEEN TO_DATE(:DT_INICIAL, 'YYYY-MM-DD') "
            "AND TO_DATE(:DT_FINAL, 'YYYY-MM-DD')"
        )
        params["DT_INICIAL"] = dt_inicial
        params["DT_FINAL"] = dt_final

    sql = (
        CTE_CHEQUES
        + SELECT_RECEITAS
        + "\n".join(filtros)
        + f"\nORDER BY {DT_EFETIVA}, PAR.NOMEPARC, FIN.NUFIN"
    )

    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500

        cursor = conexao.cursor()
        cursor.execute(sql, params)

        dados = []
        for row in cursor.fetchall():
            dados.append({
                "nossoNum":      row[0],
                "nuCompens":     row[1],
                "nuNota":        row[2],
                "dtNeg":         row[3].strftime('%Y-%m-%d') if row[3] else None,
                "dtVenc":        row[4].strftime('%Y-%m-%d') if row[4] else None,
                "nuFin":         row[5],
                "numNota":       row[6],
                "vlrDesdob":     float(row[7]) if row[7] is not None else None,
                "vlrLiquido":    float(row[8]) if row[8] is not None else None,
                "vlrCheque":     float(row[9]) if row[9] is not None else None,
                "numeroCheque":  row[10],
                "historico":     row[11],
                "contaBancaria": row[12],
                "razaoSocial":   row[13],
                "codParc":       row[14],
                "nomeParc":      row[15],
                "telefone":      row[16],
                "codCid":        row[17],
                "nomeCid":       row[18],
                "uf":            row[19],
                "cgcCpfCmc7":    row[20],
                "codTipTit":     row[21],
                "tipoTitulo":    row[22],
                "situacao":      row[23],
                "ultimoEvento":  row[24],
                "origemRegra":   row[25],
                "nuReneg":       row[26],
                "vlrDesconto":   float(row[27]) if row[27] is not None else 0.0,
                "cnpjCpf":       row[28],
                "vlrJuros":      float(row[29]) if row[29] is not None else 0.0,
                "atrasoDias":    int(row[30]) if row[30] is not None else 0,
                "vendedor":      row[31],
                "codObsPadrao":  row[32],
                "observacao":    row[33],
                "desdobramento": row[34],
                "nomeEmitente":  row[35],
                "recDesp":       row[36],
                "codTipOper":    row[37],
                "operacao":      _txt(row[38]),
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
    NVL(PAR.LIMCRED, 0) AS LIMCRED,
    PAR.ATIVO,
    CID.NOMECID,
    UFS.UF,
    NVL(VEN.APELIDO, 'SEM VENDEDOR') AS VENDEDOR
FROM TGFPAR PAR
    LEFT JOIN TSICID CID ON CID.CODCID  = PAR.CODCID
    LEFT JOIN TSIUFS UFS ON UFS.CODUF   = CID.UF
    LEFT JOIN TGFVEN VEN ON VEN.CODVEND = PAR.CODVEND
WHERE PAR.CODPARC = :CODPARC
"""

# Pontualidade: dos títulos QUITADOS nos últimos 12 meses, quantos foram pagos
# até o vencimento. É um número objetivo, tirado do próprio histórico — não é o
# "score de risco" do protótipo, que ainda depende de política da gerência.
SQL_PONTUALIDADE = """
SELECT
    COUNT(*) AS QUITADOS,
    SUM(CASE WHEN TRUNC(FIN.DHBAIXA) <= TRUNC(FIN.DTVENC) THEN 1 ELSE 0 END) AS EM_DIA
FROM TGFFIN FIN
WHERE FIN.CODPARC = :CODPARC
  AND FIN.RECDESP = 1
  AND FIN.DHBAIXA IS NOT NULL
  AND NVL(FIN.PROVISAO, 'N') = 'N'
  AND FIN.DHBAIXA >= ADD_MONTHS(TRUNC(SYSDATE), -12)
"""

# Extrato da 360°: títulos em aberto do cliente, VENCIDOS e A VENCER.
# Não-cheques: sem baixa (independente do vencimento). Cheques: mesma regra da
# consulta de títulos (CHEQUES_REGRA) — ou seja, pendentes e devolvidos.
SELECT_EXTRATO = f"""
SELECT
    FIN.NUFIN,
    FIN.NUMNOTA,
    FIN.NUNOTA,
    FIN.DESDOBRAMENTO,
    FIN.DTNEG,
    {DT_EFETIVA} AS DT_VENCIMENTO,
    FIN.VLRDESDOB,
    CASE WHEN FIN.CODTIPTIT = 3 THEN CHR.VLRCHEQUE_REGRA ELSE FIN.VLRCHEQUE END AS VLR_CHEQUE,
    VFIN.VLRLIQUIDO,
    NVL(FIN.VLRJURO, 0),
    NVL(FIN.VLRDESC, 0),
    FIN.CODTIPTIT,
    TIT.DESCRTIPTIT,
    CASE WHEN FIN.CODTIPTIT = 3 THEN CHR.CHEQUE END AS NUMERO_CHEQUE,
    CASE WHEN FIN.CODTIPTIT = 3 THEN CHR.ULTIMO_EVENTO_REGRA END AS ULTIMO_EVENTO,
    FIN.HISTORICO,
    GREATEST(TRUNC(SYSDATE) - TRUNC({DT_EFETIVA}), 0) AS ATRASO_DIAS,
    CASE
        WHEN TRUNC({DT_EFETIVA}) < TRUNC(SYSDATE) THEN 'VENCIDO'
        ELSE 'A_VENCER'
    END AS SITUACAO,
    TOP.DESCROPER,
    FIN.NURENEG
{JOINS_TITULO}
WHERE FIN.CODPARC = :CODPARC
  AND FIN.RECDESP = 1
  AND NVL(FIN.PROVISAO, 'N') = 'N'
  AND (
        (FIN.CODTIPTIT IN (2, 4, 5, 39) AND FIN.DHBAIXA IS NULL)
        OR
        (FIN.CODTIPTIT = 3 AND CHR.NUFIN IS NOT NULL)
        OR
        /* Título ativo gerado por renegociação (PIX, cartão...). Sem o corte de
           vencimento: aqui a 360° também mostra o que ainda está por vencer. */
        (
            FIN.CODTIPTIT NOT IN (2, 3, 4, 5, 39)
            AND FIN.NURENEG IS NOT NULL
            AND FIN.DHBAIXA IS NULL
        )
      )
ORDER BY {DT_EFETIVA}, FIN.NUFIN
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
                "codParc":       row[0],
                "nomeParc":      _txt(row[1]),
                "razaoSocial":   _txt(row[2]),
                "cgcCpf":        _txt(row[3]),
                "telefone":      _txt(row[4]),
                "email":         _txt(row[5]),
                "limiteCredito": float(row[6]) if row[6] is not None else 0.0,
                "ativo":         row[7],
                "nomeCid":       _txt(row[8]),
                "uf":            _txt(row[9]),
                "vendedor":      _txt(row[10]),
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
        cursor.execute(CTE_CHEQUES + SELECT_EXTRATO, {"CODPARC": cod_parc})

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
                "codTipTit":     r[11],
                "tipoTitulo":    r[12],
                "numeroCheque":  r[13],
                "ultimoEvento":  r[14],
                "historico":     r[15],
                "atrasoDias":    int(r[16]) if r[16] is not None else 0,
                "situacao":      r[17],
                "operacao":      _txt(r[18]),
                "nuReneg":       r[19],
            })

        return jsonify({"sucesso": True, "totalRegistros": len(dados), "dados": dados})

    except cx_Oracle.Error as err:
        return _erro(f"Erro de Banco de Dados: {err}")
    except Exception as e:
        return _erro(e)
    finally:
        if conexao:
            conexao.close()
