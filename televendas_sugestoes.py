"""Produtos sugeridos para o cliente: consulta do admin (recomendação comercial).

Original em televendas/docs/recomendação_comercial.txt. Os CTEs estão aqui como
o admin escreveu (TOPs 1960/1961/1999/2000/2073, empresa 3 e locais de estoque
fixos no SQL): são outras TOPs que as da configuração do televendas, e trocar
mudaria a recomendação.

Única mudança nos CTEs: saiu o ramo "C.CODEMP = 2 AND CODTIPOPER IN (1102, 1121)"
do filtro de COMPRAS, que nunca rodava (o WHERE já fixa C.CODEMP = 3). Combinado
com o usuário em 14/10/2026. O conferidor recoloca o ramo para comparar com a
original inteira.

Só o SELECT final é nosso: devolve números crus (a tela formata) e acrescenta o
motivo, o contexto e o produto-base, que a consulta calcula mas não mostrava.
Ranking, filtros e o limite de 15 ficam iguais; scripts/conferir_sugestoes.py
compara com a original.
"""

SQL_CTES = """
WITH

    COMPRAS AS (
        SELECT DISTINCT
               C.NUNOTA,
               I.CODPROD,
               TRUNC(C.DTNEG) AS DTNEG,
               C.DTNEG AS DTNEG_EXATA
          FROM TGFCAB C
          JOIN TGFITE I ON I.NUNOTA = C.NUNOTA
         WHERE C.CODEMP = 3
           AND C.CODPARC = :P_CODPARC
           AND :P_CODPARC <> 103
           AND C.STATUSNOTA = 'L'
           AND C.TIPMOV = 'V'
           AND C.DTNEG >= ADD_MONTHS(TRUNC(SYSDATE), -24)
           AND NVL(I.QTDNEG, 0) > 0
           AND C.CODTIPOPER IN (1960, 1961, 1999, 2000, 2073)
    ),
    COMPRA_FREQ AS (
        SELECT CODPROD,
               COUNT(DISTINCT NUNOTA) AS QTD_NOTAS,
               MAX(DTNEG) AS ULT_COMPRA
          FROM COMPRAS
         WHERE DTNEG >= ADD_MONTHS(TRUNC(SYSDATE), -12)
         GROUP BY CODPROD
    ),

    ITEM_HIST AS (
        SELECT CODPROD,
               COUNT(DISTINCT NUNOTA) AS QTD_NOTAS_24M,
               MAX(DTNEG) AS ULT_COMPRA
          FROM COMPRAS
         GROUP BY CODPROD
    ),

    NOTAS AS (
        SELECT NUNOTA, MAX(DTNEG_EXATA) AS DTNEG
          FROM COMPRAS
         GROUP BY NUNOTA
    ),
    NOTAS_RANK AS (
        SELECT N.NUNOTA, N.DTNEG,
               ROW_NUMBER() OVER (ORDER BY N.DTNEG DESC, N.NUNOTA DESC) AS RN
          FROM NOTAS N
    ),
    ULTIMA_NOTA AS (
        SELECT NUNOTA, DTNEG FROM NOTAS_RANK WHERE RN = 1
    ),

    CESTA_RECENTE AS (
        SELECT C.CODPROD,
               MIN(N.RN) AS ORDEM_RECENTE,
               MAX(CASE WHEN N.RN = 1 THEN 1 ELSE 0 END) AS FLAG_ULTIMA,
               MIN(TRUNC(SYSDATE) - TRUNC(N.DTNEG)) AS IDADE_NOTA,
               MAX(CASE WHEN N.RN = 1 THEN N.NUNOTA END) AS NUNOTA_ULTIMA
          FROM COMPRAS C
          JOIN NOTAS_RANK N ON N.NUNOTA = C.NUNOTA
          CROSS JOIN ULTIMA_NOTA U
         WHERE (
                N.RN = 1
                OR (N.DTNEG >= U.DTNEG - 21
                    AND N.DTNEG >= TRUNC(SYSDATE) - 45)
           )
         GROUP BY C.CODPROD
    ),

    CRUZADA_BRUTA AS (
        SELECT
            G.CODPROD_BASE,
            G.CODPROD_COMP AS CODPROD,
            F.QTD_NOTAS AS QTD_NOTAS_BASE,
            F.ULT_COMPRA AS ULT_COMPRA_BASE,
            NVL(J.QTD_NOTAS_24M, 0) AS QTD_NOTAS_ITEM_24M,
            J.ULT_COMPRA AS ULT_COMPRA_ITEM,
            G.QTDTRANSACOES_JUNTOS AS QTD_JUNTOS_GERAL,
            G.CONFIANCA AS CONFIANCA_GERAL,
            G.LIFT AS LIFT_GERAL,
            G.TIPO_RELACAO_COMERCIAL,
            R.NIVEL_REGRA,
            R.TIPO_REGRA,
            NVL(B.FLAG_ULTIMA, 0) AS FLAG_ULTIMA,
            B.ORDEM_RECENTE,
            B.IDADE_NOTA,
            B.NUNOTA_ULTIMA,
            ROUND(
                  17 * LEAST(1, NVL(G.CONFIANCA, 0) / 0.40)
                +  7 * LEAST(1, NVL(G.LIFT, 0) / 4)
                + 12 * LEAST(1, NVL(G.QTDTRANSACOES_JUNTOS, 0) / 15)
                + CASE WHEN G.TIPO_RELACAO_COMERCIAL = 'COMPLEMENTO' THEN 10 ELSE 5 END
                + 10 * LEAST(1, F.QTD_NOTAS / 5)
                + CASE
                    WHEN TRUNC(SYSDATE) - F.ULT_COMPRA <= 30 THEN 8
                    WHEN TRUNC(SYSDATE) - F.ULT_COMPRA <= 90 THEN 5
                    ELSE 2
                  END
                + CASE
                    WHEN B.FLAG_ULTIMA = 1 THEN
                        CASE WHEN B.IDADE_NOTA <= 7 THEN 25
                             WHEN B.IDADE_NOTA <= 15 THEN 20
                             WHEN B.IDADE_NOTA <= 30 THEN 13
                             WHEN B.IDADE_NOTA <= 60 THEN 6
                             ELSE 0 END
                    WHEN B.ORDEM_RECENTE > 1 THEN
                        CASE WHEN B.IDADE_NOTA <= 7 THEN 14
                             WHEN B.IDADE_NOTA <= 15 THEN 11
                             WHEN B.IDADE_NOTA <= 30 THEN 7
                             WHEN B.IDADE_NOTA <= 45 THEN 3
                             ELSE 0 END
                    ELSE 0
                  END
                + CASE

                    WHEN F.QTD_NOTAS = 1 AND NVL(B.FLAG_ULTIMA,0) = 1
                         AND NVL(B.IDADE_NOTA,999) <= 30 THEN 0
                    WHEN F.QTD_NOTAS = 1 THEN -10
                    WHEN F.QTD_NOTAS = 2 THEN -3
                    ELSE 0
                  END
                + CASE WHEN J.CODPROD IS NULL THEN 3 ELSE -4 END
            , 2) AS SCORE_BASE
          FROM COMPRA_FREQ F
          JOIN AD_ADPARVENDASCESTA G
            ON G.CODEMP = 3
           AND G.CODPROD_BASE = F.CODPROD
          JOIN AD_REGRASASSOCPROD R
            ON R.CODEMP = G.CODEMP
           AND R.CODPROD_ANT = G.CODPROD_BASE
           AND R.CODPROD_CONS = G.CODPROD_COMP
          LEFT JOIN CESTA_RECENTE B ON B.CODPROD = F.CODPROD
          LEFT JOIN ITEM_HIST J ON J.CODPROD = G.CODPROD_COMP
         WHERE NVL(G.FLAG_RELEVANTE, 'N') = 'S'
           AND NVL(G.FLAG_SUBSTITUTO, 'N') = 'N'
           AND G.TIPO_RELACAO_COMERCIAL IN ('COMPLEMENTO', 'CORRELATO')
           AND NVL(G.QTDTRANSACOES_JUNTOS, 0) >= 3
           AND NVL(G.CONFIANCA, 0) >= 0.10
           AND NVL(G.LIFT, 0) >= 1.05
           AND R.ATIVO = 'S'
           AND R.TIPO_REGRA IN ('COMPLEMENTO','CORRELATO','PROMISSORA')

           AND NOT EXISTS (
               SELECT 1
                 FROM AD_MRC_PAR X
                WHERE X.CODEMP = G.CODEMP
                  AND X.CODPROD_BASE = G.CODPROD_BASE
                  AND X.CODPROD_COMP = G.CODPROD_COMP
                  AND X.ATIVO = 'S'
                  AND X.SITUACAO IN ('BLOQUEAR','REVISAR')
           )

           AND (J.CODPROD IS NULL
                OR J.ULT_COMPRA < TRUNC(SYSDATE) - 15)
    ),
    CRUZADA_QTD_BASES AS (
        SELECT CODPROD, COUNT(DISTINCT CODPROD_BASE) AS QTD_BASES
          FROM CRUZADA_BRUTA
         GROUP BY CODPROD
    ),

    CRUZADA_COM_MAX AS (
        SELECT B.*,
               MAX(B.SCORE_BASE) OVER (PARTITION BY B.CODPROD) AS SCORE_MAX_BASE
          FROM CRUZADA_BRUTA B
    ),
    CRUZADA_ORD AS (
        SELECT B.*,
               ROW_NUMBER() OVER (
                   PARTITION BY B.CODPROD
                   ORDER BY
                       CASE WHEN B.SCORE_BASE >= B.SCORE_MAX_BASE - 7
                                 AND NVL(B.FLAG_ULTIMA,0) = 1
                                 AND NVL(B.IDADE_NOTA,999) <= 45 THEN 0
                            WHEN B.SCORE_BASE >= B.SCORE_MAX_BASE - 7
                                 AND B.ORDEM_RECENTE > 1
                                 AND NVL(B.IDADE_NOTA,999) <= 45 THEN 1
                            ELSE 2 END,
                       B.SCORE_BASE DESC,
                       B.QTD_NOTAS_BASE DESC,
                       B.QTD_JUNTOS_GERAL DESC,
                       B.CODPROD_BASE
               ) AS RN
          FROM CRUZADA_COM_MAX B
    ),
    CRUZADA_FINAL AS (
        SELECT 3 AS CODEMP,
               :P_CODPARC AS CODPARC,
               B.CODPROD,
               'VENDA_CRUZADA' AS TIPO_RECOMENDACAO,
               B.CODPROD_BASE,
               ROUND(GREATEST(0, LEAST(99.99,
                    B.SCORE_BASE + LEAST(6, (Q.QTD_BASES - 1) * 2))), 2)
                    AS SCORE_ADERENCIA,
               Q.QTD_BASES AS QTD_BASES_SUPORTE,
               B.QTD_NOTAS_BASE AS QTD_NOTAS_BASE_CLI,
               B.QTD_NOTAS_ITEM_24M AS QTD_NOTAS_ITEM_CLI,
               B.QTD_JUNTOS_GERAL,
               B.CONFIANCA_GERAL,
               B.LIFT_GERAL,
               B.ULT_COMPRA_BASE,
               B.ULT_COMPRA_ITEM,
               CAST(NULL AS NUMBER) AS INTERVALO_MEDIO_DIAS,
               CASE WHEN B.ULT_COMPRA_ITEM IS NOT NULL
                    THEN TRUNC(SYSDATE) - B.ULT_COMPRA_ITEM
                    ELSE CAST(NULL AS NUMBER) END AS DIAS_SEM_COMPRA,
               SUBSTR(
                   CASE
                     WHEN B.FLAG_ULTIMA = 1 AND B.IDADE_NOTA <= 30
                       THEN 'Complemento potencial da ultima nota '
                            || TO_CHAR(B.NUNOTA_ULTIMA)
                            || ' (ha ' || TO_CHAR(B.IDADE_NOTA) || ' d): '
                     WHEN B.ORDEM_RECENTE > 1 AND B.IDADE_NOTA <= 45
                       THEN 'Complemento potencial das compras recentes: '
                     ELSE 'Associacao com o historico do parceiro: '
                   END
                   || 'produto-base ' || TO_CHAR(B.CODPROD_BASE)
                   || '; relacao ' || B.TIPO_RELACAO_COMERCIAL
                   || '; base em ' || TO_CHAR(B.QTD_NOTAS_BASE) || ' nota(s) do cliente'
                   || '; associacao geral em ' || TO_CHAR(B.QTD_JUNTOS_GERAL) || ' nota(s)'
                   || '; ' || TO_CHAR(Q.QTD_BASES) || ' base(s) de suporte'
                   || CASE WHEN B.QTD_NOTAS_ITEM_24M = 0
                           THEN '; sugerido ainda nao comprado em 24 meses.'
                           ELSE '; sugerido comprado anteriormente em '
                                || TO_CHAR(B.QTD_NOTAS_ITEM_24M) || ' nota(s).'
                      END
               , 1, 500) AS MOTIVO
          FROM CRUZADA_ORD B
          JOIN CRUZADA_QTD_BASES Q ON Q.CODPROD = B.CODPROD
         WHERE B.RN = 1
    ),

    DIAS_COMPRA AS (
        SELECT DISTINCT CODPROD, DTNEG FROM COMPRAS
    ),
    HIST_GAPS AS (
        SELECT D.CODPROD,
               D.DTNEG,
               D.DTNEG - LAG(D.DTNEG) OVER (
                   PARTITION BY D.CODPROD ORDER BY D.DTNEG
               ) AS GAP_DIAS
          FROM DIAS_COMPRA D
    ),
    RECOMPRA_FREQ AS (
        SELECT CODPROD,
               COUNT(*) AS QTD_DIAS_COMPRA,
               MAX(DTNEG) AS ULT_COMPRA,
               ROUND(AVG(GAP_DIAS), 2) AS INTERVALO_MEDIO
          FROM HIST_GAPS
         GROUP BY CODPROD
        HAVING COUNT(*) >= 2
           AND AVG(GAP_DIAS) BETWEEN 7 AND 180
           AND MAX(DTNEG) >= ADD_MONTHS(TRUNC(SYSDATE), -12)
           AND TRUNC(SYSDATE) - MAX(DTNEG) >=
               GREATEST(7, AVG(GAP_DIAS) * 0.85)
           AND TRUNC(SYSDATE) - MAX(DTNEG) <=
               AVG(GAP_DIAS) * 2.5
    ),
    RECOMPRA_FINAL AS (
        SELECT 3 AS CODEMP,
               :P_CODPARC AS CODPARC,
               R.CODPROD,
               'RECOMPRA' AS TIPO_RECOMENDACAO,
               CAST(NULL AS NUMBER) AS CODPROD_BASE,
               ROUND(GREATEST(0, LEAST(99.99,
                    18
                  + LEAST(24, (R.QTD_DIAS_COMPRA - 1) * 8)
                  + LEAST(20, 12 * (TRUNC(SYSDATE) - R.ULT_COMPRA)
                                   / NULLIF(R.INTERVALO_MEDIO, 0))
                  + CASE
                      WHEN (TRUNC(SYSDATE) - R.ULT_COMPRA)
                           BETWEEN R.INTERVALO_MEDIO * 0.90
                               AND R.INTERVALO_MEDIO * 1.80 THEN 12
                      ELSE 0
                    END
                  + CASE
                      WHEN TRUNC(SYSDATE) - R.ULT_COMPRA <= 90 THEN 8
                      WHEN TRUNC(SYSDATE) - R.ULT_COMPRA <= 180 THEN 4
                      ELSE 0
                    END
                  - CASE WHEN R.QTD_DIAS_COMPRA = 2 THEN 10 ELSE 0 END
               )), 2) AS SCORE_ADERENCIA,
               0 AS QTD_BASES_SUPORTE,
               CAST(NULL AS NUMBER) AS QTD_NOTAS_BASE_CLI,
               F.QTD_NOTAS AS QTD_NOTAS_ITEM_CLI,
               CAST(NULL AS NUMBER) AS QTD_JUNTOS_GERAL,
               CAST(NULL AS NUMBER) AS CONFIANCA_GERAL,
               CAST(NULL AS NUMBER) AS LIFT_GERAL,
               CAST(NULL AS DATE) AS ULT_COMPRA_BASE,
               R.ULT_COMPRA AS ULT_COMPRA_ITEM,
               R.INTERVALO_MEDIO AS INTERVALO_MEDIO_DIAS,
               TRUNC(SYSDATE) - R.ULT_COMPRA AS DIAS_SEM_COMPRA,
               SUBSTR(
                   'Recompra: ' || F.QTD_NOTAS || ' nota(s) do item; intervalo medio '
                   || TO_CHAR(R.INTERVALO_MEDIO) || ' dia(s); ultima compra ha '
                   || TO_CHAR(TRUNC(SYSDATE)-R.ULT_COMPRA) || ' dia(s); '
                   || R.QTD_DIAS_COMPRA || ' dia(s) distintos observados em 24 meses.', 1, 500
               ) AS MOTIVO
          FROM RECOMPRA_FREQ R
          JOIN COMPRA_FREQ F ON F.CODPROD = R.CODPROD
    ),
    CANDIDATOS AS (
        SELECT * FROM CRUZADA_FINAL
        UNION ALL
        SELECT * FROM RECOMPRA_FINAL
    ),

    CANDIDATOS_UNICOS AS (
        SELECT C.*,
               ROW_NUMBER() OVER (
                   PARTITION BY CODEMP, CODPARC, CODPROD
                   ORDER BY SCORE_ADERENCIA DESC,
                            CASE WHEN TIPO_RECOMENDACAO = 'VENDA_CRUZADA'
                                 THEN 1 ELSE 2 END,
                            NVL(QTD_BASES_SUPORTE, 0) DESC,
                            CODPROD_BASE NULLS LAST
               ) AS RN_PROD
          FROM CANDIDATOS C
    ),
    CANDIDATOS_POR_TIPO AS (
        SELECT C.*,
               ROW_NUMBER() OVER (
                   PARTITION BY CODEMP, CODPARC, TIPO_RECOMENDACAO
                   ORDER BY SCORE_ADERENCIA DESC,
                            NVL(QTD_BASES_SUPORTE, 0) DESC,
                            CODPROD
               ) AS RN_TIPO,
               ROW_NUMBER() OVER (
                   PARTITION BY CODEMP, CODPARC,
                       CASE WHEN TIPO_RECOMENDACAO = 'VENDA_CRUZADA'
                                 AND (MOTIVO LIKE 'Complemento potencial da ultima nota %'
                                   OR MOTIVO LIKE 'Complemento potencial das compras recentes%')
                            THEN 'CESTA' ELSE 'OUTROS' END
                   ORDER BY SCORE_ADERENCIA DESC,
                            NVL(QTD_BASES_SUPORTE, 0) DESC,
                            CODPROD
               ) AS RN_CONTEXTO
          FROM CANDIDATOS_UNICOS C
         WHERE RN_PROD = 1
    ),
    RANKEADOS AS (
        SELECT C.*,
               ROW_NUMBER() OVER (
                   PARTITION BY CODEMP, CODPARC
                   ORDER BY SCORE_ADERENCIA DESC,
                            CASE WHEN TIPO_RECOMENDACAO = 'VENDA_CRUZADA' THEN 1 ELSE 2 END,
                            NVL(QTD_BASES_SUPORTE, 0) DESC,
                            CODPROD
               ) AS POSICAO
          FROM CANDIDATOS_POR_TIPO C
         WHERE (TIPO_RECOMENDACAO = 'VENDA_CRUZADA' AND RN_TIPO <= 40)
            OR (TIPO_RECOMENDACAO = 'VENDA_CRUZADA'
                AND RN_CONTEXTO <= 10
                AND (MOTIVO LIKE 'Complemento potencial da ultima nota %'
                  OR MOTIVO LIKE 'Complemento potencial das compras recentes%'))
            OR (TIPO_RECOMENDACAO = 'RECOMPRA' AND RN_TIPO <= 20)
    ),
    SUG AS (
        SELECT CODEMP, CODPARC, CODPROD, POSICAO, TIPO_RECOMENDACAO,
               CODPROD_BASE, SCORE_ADERENCIA, QTD_BASES_SUPORTE,
               QTD_NOTAS_BASE_CLI, QTD_NOTAS_ITEM_CLI, QTD_JUNTOS_GERAL,
               CONFIANCA_GERAL, LIFT_GERAL, ULT_COMPRA_BASE, ULT_COMPRA_ITEM,
               INTERVALO_MEDIO_DIAS, DIAS_SEM_COMPRA, MOTIVO
          FROM RANKEADOS
         WHERE POSICAO <= 70
    ),
    COM_DADOS AS (
        SELECT S.*,
               P.DESCRPROD,
               P.CODVOL,
               P.CODGRUPOPROD,
               PB.DESCRPROD AS DESCRPROD_BASE,
               CASE
                 WHEN S.MOTIVO LIKE 'Complemento potencial da ultima nota %' THEN 'ULTIMA_NOTA'
                 WHEN S.MOTIVO LIKE 'Complemento potencial das compras recentes%' THEN 'CESTA_RECENTE'
                 WHEN S.TIPO_RECOMENDACAO = 'RECOMPRA' THEN 'RECOMPRA'
                 ELSE 'HISTORICO_GERAL'
               END AS CONTEXTO
          FROM SUG S
          JOIN TGFPRO P ON P.CODPROD = S.CODPROD AND P.ATIVO = 'S'
          LEFT JOIN TGFPRO PB ON PB.CODPROD = S.CODPROD_BASE
    ),
    SALDO_EMP3 AS (
        SELECT E.CODPROD,
               SUM(NVL(E.ESTOQUE, 0)) AS SALDO_BRUTO,
               SUM(NVL(E.RESERVADO, 0)) AS QTD_RESERVADA,
               SUM(NVL(E.ESTOQUE, 0) - NVL(E.RESERVADO, 0)) AS SALDO_APOS_RESERVA
          FROM TGFEST E
          JOIN (SELECT DISTINCT CODPROD FROM SUG) S
            ON S.CODPROD = E.CODPROD
         WHERE E.CODEMP = 3
           AND E.CODLOCAL IN (30100, 30300, 30400, 30500, 30600, 30700, 30800)
         GROUP BY E.CODPROD
    ),
    ELEGIVEIS AS (
        SELECT D.*,
               E.SALDO_BRUTO,
               E.QTD_RESERVADA,
               E.SALDO_APOS_RESERVA,
               F.ID_FAMILIA,
               CASE
                 WHEN D.CONTEXTO IN ('ULTIMA_NOTA', 'CESTA_RECENTE')
                  AND D.TIPO_RECOMENDACAO = 'VENDA_CRUZADA'
                  AND NVL(D.QTD_JUNTOS_GERAL, 0) >= 10
                  AND (NVL(D.QTD_NOTAS_BASE_CLI, 0) >= 2
                       OR NVL(D.QTD_BASES_SUPORTE, 0) >= 2)
                  AND D.ULT_COMPRA_BASE IS NOT NULL
                 THEN CASE WHEN D.CONTEXTO = 'ULTIMA_NOTA' THEN
                      CASE WHEN TRUNC(SYSDATE)-TRUNC(D.ULT_COMPRA_BASE)<=7 THEN 10
                           WHEN TRUNC(SYSDATE)-TRUNC(D.ULT_COMPRA_BASE)<=15 THEN 6
                           WHEN TRUNC(SYSDATE)-TRUNC(D.ULT_COMPRA_BASE)<=30 THEN 3
                           ELSE 0 END
                    ELSE CASE WHEN TRUNC(SYSDATE)-TRUNC(D.ULT_COMPRA_BASE)<=7 THEN 6
                              WHEN TRUNC(SYSDATE)-TRUNC(D.ULT_COMPRA_BASE)<=15 THEN 4
                              WHEN TRUNC(SYSDATE)-TRUNC(D.ULT_COMPRA_BASE)<=45 THEN 2
                              ELSE 0 END END
                 ELSE 0
               END AS BONUS_CONTEXTO,
               CASE
                 WHEN D.CONTEXTO='HISTORICO_GERAL'
                  AND NVL(D.QTD_NOTAS_BASE_CLI, 0)<=1
                  AND D.ULT_COMPRA_BASE < TRUNC(SYSDATE)-180 THEN 12
                 WHEN D.CONTEXTO='HISTORICO_GERAL'
                  AND NVL(D.QTD_JUNTOS_GERAL, 0)<8
                  AND NVL(D.QTD_BASES_SUPORTE, 0)<=1 THEN 10
                 WHEN D.CONTEXTO='HISTORICO_GERAL'
                  AND NVL(D.QTD_NOTAS_BASE_CLI, 0)<=1
                  AND NVL(D.QTD_BASES_SUPORTE, 0)<=3 THEN 8
                 ELSE 0
               END AS PENALIDADE_EVIDENCIA
          FROM COM_DADOS D
          JOIN SALDO_EMP3 E ON E.CODPROD = D.CODPROD
          LEFT JOIN AD_MRC_FAMILIA F
            ON F.CODEMP=D.CODEMP AND F.CODPROD=D.CODPROD AND F.ATIVO='S'
         WHERE E.SALDO_APOS_RESERVA > 0
    ),
    COM_SCORE AS (
        SELECT E.*,
               ROUND(GREATEST(0, LEAST(99.99,
                     E.SCORE_ADERENCIA + E.BONUS_CONTEXTO - E.PENALIDADE_EVIDENCIA)),2)
                    AS SCORE_PRIORIDADE,
               CASE WHEN E.ID_FAMILIA IS NULL THEN 'PROD_' || TO_CHAR(E.CODPROD)
                    ELSE 'FAM_' || E.ID_FAMILIA END AS CHAVE_OFERTA
          FROM ELEGIVEIS E
    ),
    UMA_APRESENTACAO AS (
        SELECT C.*,
               ROW_NUMBER() OVER (
                   PARTITION BY C.CODEMP,C.CODPARC,C.CHAVE_OFERTA
                   ORDER BY CASE WHEN C.ID_FAMILIA IS NOT NULL THEN NVL(C.QTD_NOTAS_ITEM_CLI,0) ELSE 0 END DESC,
                            CASE WHEN C.ID_FAMILIA IS NOT NULL THEN C.ULT_COMPRA_ITEM END DESC NULLS LAST,
                            C.SCORE_PRIORIDADE DESC, C.CODPROD
               ) AS RN_FAMILIA
          FROM COM_SCORE C
         WHERE NVL(C.SALDO_APOS_RESERVA, 0) > 0
    ),
    DIVERSIDADE AS (
        SELECT F.*,
               ROW_NUMBER() OVER (
                   PARTITION BY F.CODEMP, F.CODPARC, F.CODGRUPOPROD
                   ORDER BY F.SCORE_PRIORIDADE DESC,F.CODPROD
               ) AS RN_GRUPO
          FROM UMA_APRESENTACAO F
         WHERE F.RN_FAMILIA = 1
    ),
    RANK_FINAL AS (
        SELECT D.*,
               ROW_NUMBER() OVER (
                   PARTITION BY D.CODEMP, D.CODPARC
                   ORDER BY D.SCORE_PRIORIDADE DESC,D.CODPROD
               ) AS POSICAO_PAINEL
          FROM DIVERSIDADE D
         WHERE D.RN_GRUPO <= 4
    ),

    TOP15 AS (
        SELECT  R.*
          FROM RANK_FINAL R
         WHERE R.POSICAO_PAINEL <= 15
           AND R.SALDO_APOS_RESERVA > 0
    ),

    HIST_PRECO_VERSAO AS (
        SELECT X.CODPROD,
               TAB.NUTAB,
               TAB.DTVIGOR,
               MIN(X.VLRVENDA) AS VLR_VERSAO,
               COUNT(DISTINCT X.VLRVENDA) AS QTD_PRECOS_VERSAO
          FROM (SELECT DISTINCT CODPROD FROM TOP15) T
          JOIN TGFEXC X ON X.CODPROD = T.CODPROD
          JOIN TGFTAB TAB ON TAB.NUTAB = X.NUTAB
         WHERE TAB.CODTAB = 0
           AND TAB.DTVIGOR < TRUNC(SYSDATE) + 1
         GROUP BY X.CODPROD, TAB.NUTAB, TAB.DTVIGOR
    ),
    HIST_PRECO_RANK AS (
        SELECT H.*,
               ROW_NUMBER() OVER (
                   PARTITION BY H.CODPROD
                   ORDER BY H.DTVIGOR DESC, H.NUTAB DESC
               ) AS RN
          FROM HIST_PRECO_VERSAO H
    ),
    PRECOS_COMPARADOS AS (
        SELECT H.CODPROD,
               MAX(CASE WHEN RN = 1 AND QTD_PRECOS_VERSAO = 1
                              AND VLR_VERSAO > 0 THEN VLR_VERSAO END) AS PRECO_ATUAL,
               MAX(CASE WHEN RN = 2 AND QTD_PRECOS_VERSAO = 1
                              AND VLR_VERSAO > 0 THEN VLR_VERSAO END) AS PRECO_ANTERIOR,
               MAX(CASE WHEN RN = 1 THEN DTVIGOR END) AS DT_PRECO_ATUAL,
               MAX(CASE WHEN RN = 2 THEN DTVIGOR END) AS DT_PRECO_ANTERIOR
          FROM HIST_PRECO_RANK H
         WHERE RN <= 2
         GROUP BY H.CODPROD
    ),
    PRECOS_FINAIS AS (
        SELECT P.CODPROD, P.PRECO_ATUAL, P.PRECO_ANTERIOR,
               CASE WHEN P.PRECO_ATUAL > 0 AND P.PRECO_ANTERIOR > 0
                    THEN ROUND((P.PRECO_ATUAL / P.PRECO_ANTERIOR - 1) * 100, 2)
               END AS VARIACAO_PCT
          FROM PRECOS_COMPARADOS P
    )
"""

SQL_SUGESTOES = SQL_CTES + """
SELECT R.POSICAO_PAINEL, R.CODPROD, R.DESCRPROD, R.CODVOL,
       R.SCORE_PRIORIDADE, P.PRECO_ATUAL, P.VARIACAO_PCT, R.SALDO_APOS_RESERVA,
       R.TIPO_RECOMENDACAO, R.CONTEXTO, R.CODPROD_BASE, R.DESCRPROD_BASE, R.MOTIVO,
       R.DIAS_SEM_COMPRA, R.INTERVALO_MEDIO_DIAS, R.QTD_NOTAS_ITEM_CLI
  FROM TOP15 R
  LEFT JOIN PRECOS_FINAIS P ON P.CODPROD = R.CODPROD
 ORDER BY R.POSICAO_PAINEL
"""


def _txt(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _num(v):
    return float(v) if v is not None else None


def _int(v):
    return int(v) if v is not None else None


def sugestoes(cur, codparc, config):
    """Até 15 produtos para oferecer, na ordem da consulta do admin.

    `config` não é usado (a consulta tem empresa e TOPs próprias); fica na
    assinatura para servir ao _ficha() das outras abas.
    """
    cur.execute(SQL_SUGESTOES, {"P_CODPARC": codparc})
    return {
        "produtos": [
            {
                "posicao": int(r[0]),
                "codProd": int(r[1]),
                "descricao": _txt(r[2]),
                "unidade": _txt(r[3]),
                "potencial": _num(r[4]),
                "precoAtual": _num(r[5]),
                # None = sem histórico de preço (a original mostrava "SEM HISTORICO").
                "variacaoPreco": _num(r[6]),
                "saldo": _num(r[7]),
                "tipo": _txt(r[8]),
                "contexto": _txt(r[9]),
                "codProdBase": _int(r[10]),
                "descricaoBase": _txt(r[11]),
                "motivo": _txt(r[12]),
                "diasSemComprar": _int(r[13]),
                "intervaloMedioDias": _num(r[14]),
                "notasDoItem": _int(r[15]),
            }
            for r in cur.fetchall()
        ]
    }
