"""Confere a aba Sugeridos contra a consulta original do admin, para um cliente.

Roda as duas versões da recomendação comercial no Oracle:
  - a ORIGINAL, inteira como veio (inclusive o ramo da empresa 2 e o SELECT final
    do admin, que formata o texto);
  - a NOSSA (televendas_sugestoes.SQL_SUGESTOES, números crus e o motivo).
E compara produto, posição, potencial, preço, variação e saldo, linha a linha.
Mede o tempo de cada uma. Só lê. Rodar no servidor:

    docker compose exec api-sankhya python scripts/conferir_sugestoes.py <CODPARC> [<CODPARC> ...]
"""

import os
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import televendas_sugestoes as S  # noqa: E402
from db import conectar_oracle  # noqa: E402

# A consulta do admin INTEIRA, como veio (televendas/docs/recomendação_comercial.txt):
# os CTEs com o ramo da empresa 2 que a API tirou (nunca roda) e o SELECT final dele.
_FILTRO_API = """           AND C.CODTIPOPER IN (1960, 1961, 1999, 2000, 2073)
"""
_FILTRO_ORIGINAL = """           AND (
                   (C.CODEMP = 3 AND C.CODTIPOPER IN (1960, 1961, 1999, 2000, 2073))
                OR (C.CODEMP = 2 AND C.CODTIPOPER IN (1102, 1121))
               )
"""
assert S.SQL_CTES.count(_FILTRO_API) == 1, "o filtro de TOPs mudou em televendas_sugestoes.py"
SQL_CTES_ORIGINAL = S.SQL_CTES.replace(_FILTRO_API, _FILTRO_ORIGINAL)

SQL_ORIGINAL = SQL_CTES_ORIGINAL + """
SELECT
       R.POSICAO_PAINEL AS POSICAO,
       R.CODPROD,
       R.DESCRPROD,
       R.CODVOL,

       TO_CHAR(R.SCORE_PRIORIDADE, 'FM9990D00',
               'NLS_NUMERIC_CHARACTERS='',.''') || '%' AS POTENCIAL_COMPRA,
       P.PRECO_ATUAL,
       CASE WHEN P.VARIACAO_PCT > 0 THEN '+' ||
                    TO_CHAR(P.VARIACAO_PCT, 'FM9999990D00',
                            'NLS_NUMERIC_CHARACTERS='',.''') || '%'
            WHEN P.VARIACAO_PCT < 0 THEN
                    TO_CHAR(P.VARIACAO_PCT, 'FM9999990D00',
                            'NLS_NUMERIC_CHARACTERS='',.''') || '%'
            WHEN P.VARIACAO_PCT = 0 THEN '0,00%'
            ELSE 'SEM HISTORICO'
       END AS VARIACAO_PRECO,
       R.SALDO_APOS_RESERVA AS SALDO_DISPONIVEL
  FROM TOP15 R
  LEFT JOIN PRECOS_FINAIS P ON P.CODPROD = R.CODPROD
 ORDER BY R.POSICAO_PAINEL
"""


def pct(v):
    """Mesmo texto que a original gera: 87,50% / +3,20% / -1,00% / 0,00%."""
    return f"{v:.2f}".replace(".", ",") + "%"


def variacao_txt(v):
    if v is None:
        return "SEM HISTORICO"
    if v > 0:
        return "+" + pct(v)
    return pct(v)


def conferir(cur, codparc):
    t = time.perf_counter()
    cur.execute(SQL_ORIGINAL, {"P_CODPARC": codparc})
    original = cur.fetchall()
    t_orig = time.perf_counter() - t

    t = time.perf_counter()
    nossa = S.sugestoes(cur, codparc, None)["produtos"]
    t_nossa = time.perf_counter() - t

    print(f"\nCLIENTE {codparc}: original {len(original)} produto(s) em {t_orig:.1f} s,"
          f" nossa {len(nossa)} em {t_nossa:.1f} s")
    diferencas = 0
    for i in range(max(len(original), len(nossa))):
        o = original[i] if i < len(original) else None
        n = nossa[i] if i < len(nossa) else None
        if o and n:
            iguais = (
                int(o[0]) == n["posicao"]
                and int(o[1]) == n["codProd"]
                and o[4] == pct(n["potencial"])
                and o[5] == n["precoAtual"]
                and o[6] == variacao_txt(n["variacaoPreco"])
                and o[7] == n["saldo"]
            )
        else:
            iguais = False
        diferencas += not iguais
        marca = "  " if iguais else "!!"
        if n:
            base = f"  base {n['codProdBase']}" if n["codProdBase"] else ""
            print(f"{marca} {n['posicao']:>2} {n['codProd']:>7} {(n['descricao'] or '')[:38]:38} {pct(n['potencial']):>7}"
                  f"  {n['contexto']}{base}")
        if o and not iguais:
            print(f"   original: {tuple(o)}")
    print("   BATEU com a original." if not diferencas else f"   {diferencas} linha(s) diferente(s).")
    return diferencas


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ora = conectar_oracle()
    if not ora:
        sys.exit("Sem conexão com o Oracle.")
    try:
        cur = ora.cursor()
        total = sum(conferir(cur, int(cod)) for cod in sys.argv[1:])
    finally:
        ora.close()
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
