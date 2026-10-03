"""Ficha do cliente no televendas: compras, mix de produtos e último pedido.

TOPs: as mesmas de "última compra" das listas (televendas.top com
ultima_compra), na empresa do parâmetro CODEMP. Essa lista mistura PEDIDOS
(1001, 1010, 1997, 1998, 2130–2134) com TOPs de NOTA (1988, 1990, 2006, 2007).
Para a DATA da última compra isso não importa; para SOMAR valor e quantidade,
importa: um pedido faturado apareceria duas vezes (o pedido e a nota).

Por isso compras e mix usam NOTAS_EFETIVAS: descarta a nota que tem uma
DESCENDENTE dentro das mesmas TOPs (TGFVAR.NUNOTAORIG = ela). Fica o elo mais
novo da cadeia pedido -> nota, contado uma vez só, seja qual for a TOP de cada um.

Não abate devolução nem olha STATUSNOTA, igual às consultas do admin.
O último pedido segue a consulta do admin (docs/itens_ultimo_pedido.txt):
a nota mais recente entre as TOPs, por DTNEG e NUNOTA.
"""

from datetime import date, datetime

# Janela do mix e regra do "parou de comprar".
MIX_DIAS = 180            # olha 6 meses
PAROU_SEM_COMPRAR = 60    # ... e marca quem não volta a comprar o produto há 60 dias
PAROU_MIN_COMPRAS = 2     # ... desde que tenha sido comprado pelo menos 2 vezes (hábito, não acaso)
MIX_LIMITE = 20

_NOTAS_EFETIVAS = """
NOTAS AS
(
    SELECT CAB.NUNOTA, CAB.NUMNOTA, CAB.DTNEG, CAB.CODTIPOPER, CAB.DHTIPOPER, CAB.VLRNOTA,
           CAB.CODVEND, CAB.AD_CODVENDINT
      FROM TGFCAB CAB
     WHERE CAB.CODEMP = :CODEMP
       AND CAB.CODPARC = :CODPARC
       AND CAB.CODTIPOPER IN ({tops})
       AND CAB.DTNEG >= {inicio}
),
NOTAS_EFETIVAS AS
(
    SELECT N.*
      FROM NOTAS N
     WHERE NOT EXISTS (
               SELECT 1
                 FROM TGFVAR VAR
                 JOIN TGFCAB FILHA ON FILHA.NUNOTA = VAR.NUNOTA
                WHERE VAR.NUNOTAORIG = N.NUNOTA
                  AND FILHA.CODTIPOPER IN ({tops})
           )
)"""

# Compras: os últimos N meses DO CALENDÁRIO (do dia 1º do mês mais antigo até hoje),
# a mesma janela da série mensal. Uma janela em dias ("372 dias") pegava notas de
# um 13º mês que entravam no total e no ticket sem aparecer em nenhuma coluna.
INICIO_MESES = "TRUNC(ADD_MONTHS(TRUNC(SYSDATE), 1 - :MESES), 'MM')"
INICIO_DIAS = "TRUNC(SYSDATE) - :DIAS"

SQL_COMPRAS = (
    "WITH"
    + _NOTAS_EFETIVAS.replace("{inicio}", INICIO_MESES)
    + """
SELECT N.NUNOTA, N.NUMNOTA, N.DTNEG, N.CODTIPOPER, TOP.DESCROPER, N.VLRNOTA,
       VEN.APELIDO AS VENDEDOR, VINT.APELIDO AS VENDEDOR_INTERNO
  FROM NOTAS_EFETIVAS N
  LEFT JOIN TGFTOP TOP ON TOP.CODTIPOPER = N.CODTIPOPER AND TOP.DHALTER = N.DHTIPOPER
  LEFT JOIN TGFVEN VEN ON VEN.CODVEND = N.CODVEND
  LEFT JOIN TGFVEN VINT ON VINT.CODVEND = N.AD_CODVENDINT
 ORDER BY N.DTNEG DESC, N.NUNOTA DESC
"""
)

SQL_MIX = (
    "WITH"
    + _NOTAS_EFETIVAS.replace("{inicio}", INICIO_DIAS)
    + """
SELECT ITE.CODPROD, PRO.DESCRPROD, PRO.CODVOL, PRO.MARCA,
       SUM(ITE.QTDNEG) AS QTD,
       SUM(ITE.VLRTOT) AS VALOR,
       COUNT(DISTINCT N.NUNOTA) AS COMPRAS,
       MAX(N.DTNEG) AS ULTIMA
  FROM NOTAS_EFETIVAS N
  JOIN TGFITE ITE ON ITE.NUNOTA = N.NUNOTA
  JOIN TGFPRO PRO ON PRO.CODPROD = ITE.CODPROD
 GROUP BY ITE.CODPROD, PRO.DESCRPROD, PRO.CODVOL, PRO.MARCA
"""
)

# Consulta do admin (itens do último pedido), com TOPs e empresa da configuração.
SQL_ULTIMO_PEDIDO = """
WITH ULTIMO_PEDIDO AS
(
    SELECT MAX(CAB.NUNOTA) KEEP (DENSE_RANK LAST ORDER BY CAB.DTNEG, CAB.NUNOTA) AS NUNOTA
      FROM TGFCAB CAB
     WHERE CAB.CODEMP = :CODEMP
       AND CAB.CODPARC = :CODPARC
       AND CAB.CODTIPOPER IN ({tops})
)
SELECT CAB.NUNOTA, CAB.NUMNOTA, CAB.DTNEG, CAB.CODTIPOPER, TOP.DESCROPER, CAB.VLRNOTA,
       VEN.APELIDO AS VENDEDOR, VINT.APELIDO AS VENDEDOR_INTERNO, CAB.ORDEMCARGA,
       ITE.SEQUENCIA, ITE.CODPROD, PRO.DESCRPROD, PRO.CODVOL, PRO.MARCA,
       ITE.QTDNEG, ITE.VLRUNIT, ITE.VLRTOT
  FROM ULTIMO_PEDIDO U
  JOIN TGFCAB CAB ON CAB.NUNOTA = U.NUNOTA
  JOIN TGFITE ITE ON ITE.NUNOTA = CAB.NUNOTA
  JOIN TGFPRO PRO ON PRO.CODPROD = ITE.CODPROD
  LEFT JOIN TGFVEN VEN ON VEN.CODVEND = CAB.CODVEND
  LEFT JOIN TGFVEN VINT ON VINT.CODVEND = CAB.AD_CODVENDINT
  LEFT JOIN TGFTOP TOP ON TOP.CODTIPOPER = CAB.CODTIPOPER AND TOP.DHALTER = CAB.DHTIPOPER
 WHERE U.NUNOTA IS NOT NULL
 ORDER BY ITE.SEQUENCIA
"""


def hoje_oracle(cur):
    """Data de hoje pelo relógio do Oracle (UTC-3). O container roda em UTC:
    date.today() viraria o dia às 21h de Maceió."""
    cur.execute("SELECT TRUNC(SYSDATE) FROM DUAL")
    return _dia(cur.fetchone()[0])


def _binds_tops(config):
    nomes = [f"T{i}" for i in range(len(config["tops"]))]
    return ", ".join(f":{n}" for n in nomes), dict(zip(nomes, config["tops"]))


def _txt(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _dia(v):
    if isinstance(v, datetime):
        return v.date()
    return v


def _num(v):
    return float(v) if v is not None else None


def compras(cur, codparc, config, meses=12):
    """Notas efetivas dos últimos `meses`, série mensal, ticket médio e frequência."""
    tops, binds = _binds_tops(config)
    binds.update({"CODEMP": config["codemp"], "CODPARC": codparc, "MESES": meses})
    cur.execute(SQL_COMPRAS.replace("{tops}", tops), binds)
    notas = []
    for r in cur.fetchall():
        notas.append(
            {
                "nunota": int(r[0]),
                "numNota": int(r[1]) if r[1] is not None else None,
                "dtNeg": _dia(r[2]).isoformat(),
                "codTipOper": int(r[3]),
                "descrOper": _txt(r[4]),
                "valor": _num(r[5]) or 0.0,
                "vendedor": _txt(r[6]),
                "vendedorInterno": _txt(r[7]),
            }
        )

    # Série dos últimos `meses` meses (inclusive o atual), com zeros nos meses sem compra.
    hoje = hoje_oracle(cur)
    chaves = []
    ano, mes = hoje.year, hoje.month
    for _ in range(meses):
        chaves.append(f"{ano:04d}-{mes:02d}")
        ano, mes = (ano, mes - 1) if mes > 1 else (ano - 1, 12)
    serie = {k: {"mes": k, "valor": 0.0, "notas": 0} for k in reversed(chaves)}
    for n in notas:
        k = n["dtNeg"][:7]
        if k in serie:
            serie[k]["valor"] += n["valor"]
            serie[k]["notas"] += 1

    dias = sorted({n["dtNeg"] for n in notas})
    intervalos = [
        (date.fromisoformat(b) - date.fromisoformat(a)).days for a, b in zip(dias, dias[1:])
    ]
    total = sum(n["valor"] for n in notas)
    return {
        "meses": meses,
        "notas": notas,
        "serie": list(serie.values()),
        "resumo": {
            "qtdNotas": len(notas),
            "valorTotal": round(total, 2),
            "ticketMedio": round(total / len(notas), 2) if notas else None,
            # Média de dias entre DATAS de compra distintas (duas notas no mesmo dia = uma compra).
            "frequenciaDias": round(sum(intervalos) / len(intervalos)) if intervalos else None,
            "ultimaCompra": dias[-1] if dias else None,
        },
    }


def mix(cur, codparc, config):
    """Produtos dos últimos 6 meses + os que o cliente parou de comprar."""
    tops, binds = _binds_tops(config)
    binds.update({"CODEMP": config["codemp"], "CODPARC": codparc, "DIAS": MIX_DIAS})
    # A data vem ANTES da consulta: no mesmo cursor, depois dela, descartaria o resultado.
    hoje = hoje_oracle(cur)
    cur.execute(SQL_MIX.replace("{tops}", tops), binds)
    produtos = []
    for r in cur.fetchall():
        ultima = _dia(r[7])
        sem_comprar = (hoje - ultima).days if ultima else None
        compras_n = int(r[6] or 0)
        produtos.append(
            {
                "codProd": int(r[0]),
                "descricao": _txt(r[1]),
                "unidade": _txt(r[2]),
                "marca": _txt(r[3]),
                "quantidade": _num(r[4]),
                "valor": _num(r[5]),
                "compras": compras_n,
                "ultimaCompra": ultima.isoformat() if ultima else None,
                "diasSemComprar": sem_comprar,
                "parou": compras_n >= PAROU_MIN_COMPRAS and sem_comprar is not None and sem_comprar >= PAROU_SEM_COMPRAR,
            }
        )
    # Mais frequentes primeiro; empate, maior valor.
    produtos.sort(key=lambda p: (-p["compras"], -(p["valor"] or 0)))
    parou = [p for p in produtos if p["parou"]]
    return {
        "janelaDias": MIX_DIAS,
        "regraParou": f"comprado {PAROU_MIN_COMPRAS}+ vezes em {MIX_DIAS} dias e sem comprar há {PAROU_SEM_COMPRAR}+ dias",
        "produtos": produtos[:MIX_LIMITE],
        "totalProdutos": len(produtos),
        "parouDeComprar": parou,
    }


def ultimo_pedido(cur, codparc, config):
    tops, binds = _binds_tops(config)
    binds.update({"CODEMP": config["codemp"], "CODPARC": codparc})
    cur.execute(SQL_ULTIMO_PEDIDO.replace("{tops}", tops), binds)
    linhas = cur.fetchall()
    if not linhas:
        return {"pedido": None}
    r0 = linhas[0]
    return {
        "pedido": {
            "nunota": int(r0[0]),
            "numNota": int(r0[1]) if r0[1] is not None else None,
            "dtNeg": _dia(r0[2]).isoformat(),
            "codTipOper": int(r0[3]),
            "descrOper": _txt(r0[4]),
            "valor": _num(r0[5]),
            "vendedor": _txt(r0[6]),
            "vendedorInterno": _txt(r0[7]),
            "ordemCarga": int(r0[8]) if r0[8] else None,
            "itens": [
                {
                    "sequencia": int(r[9]),
                    "codProd": int(r[10]),
                    "descricao": _txt(r[11]),
                    "unidade": _txt(r[12]),
                    "marca": _txt(r[13]),
                    "quantidade": _num(r[14]),
                    "valorUnit": _num(r[15]),
                    "valorTotal": _num(r[16]),
                }
                for r in linhas
            ],
        }
    }
