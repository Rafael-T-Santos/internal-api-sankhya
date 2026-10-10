"""Listas de clientes do televendas: Minha carteira e Fila interna.

Origem: as consultas enviadas pelo administrador do Sankhya em 03/10/2026
(repositório televendas, docs/carteira_televendas_representantes.txt e
docs/clientes_televendas_geral.txt). A lógica delas é reproduzida aqui, com
estas diferenças DE PROPÓSITO (plano §6.4 e §12):

  - A escala de visitas não está mais escrita no SQL: o Oracle devolve o
    cliente com representante, cidade e bairro, e a escala (televendas.escala,
    no Postgres) é aplicada em Python. Oracle e Postgres não se juntam numa
    consulta só.
  - As TOPs de "última compra", o atraso máximo e a empresa vêm da
    configuração (televendas.top / televendas.parametro).
  - Contatos: a consulta original fazia MAX(NOMECONTATO) e MAX(CELULAR) em
    separado (nome de um contato com telefone de outro). O admin confirmou o
    erro; agora vêm TODOS os contatos que têm telefone ou celular.
  - Carteira ganha PAR.CLIENTE = 'S' (pedido do admin). O parâmetro
    `so_clientes=False` existe só para a conferência contra a consulta original.
  - Entram a última ordem de carga do cliente e a hora de saída dela.

Os filtros de nome, cidade, representante e período das consultas originais
não estão aqui: viraram filtros no navegador. A rota devolve a lista inteira.
"""

from datetime import date, datetime

# ---------------------------------------------------------------------------
# SQL (Oracle)
# ---------------------------------------------------------------------------

# Clientes da carteira do televendas: representante externo (TGFPAR.CODVEND)
# cujo TGFVEN.AD_CODVEND aponta para o televendas.
_BASE_CARTEIRA = """
BASE AS
(
    SELECT
        A.CODPARC,
        A.NOMEPARC AS FANTASIA,
        A.RAZAOSOCIAL,
        A.CGC_CPF,
        NVL(A.CODPARCMATRIZ, A.CODPARC) AS CODGRUPO,
        PMAT.NOMEPARC AS PARCEIRO_MATRIZ,
        A.CODCID,
        CID.NOMECID AS CIDADE,
        A.CODBAI,
        BAI.NOMEBAI AS BAIRRO,
        A.TELEFONE,
        A.OBSERVACOES,
        VEXT.CODVEND AS CODVEND_EXTERNO,
        VEXT.APELIDO AS VENDEDOR
    FROM TGFPAR A
    LEFT JOIN TGFPAR PMAT ON PMAT.CODPARC = NVL(A.CODPARCMATRIZ, A.CODPARC)
    LEFT JOIN TSICID CID ON CID.CODCID = A.CODCID
    LEFT JOIN TSIBAI BAI ON BAI.CODBAI = A.CODBAI
    INNER JOIN TGFVEN VEXT ON VEXT.CODVEND = A.CODVEND
    WHERE A.TIPPESSOA = 'J'
      AND A.ATIVO = 'S'
      AND VEXT.AD_CODVEND = :CODTELEVEND
      {filtro_cliente}
)"""

# Fila interna: PJ, cliente, ativo, de AL e SEM vendedor.
_BASE_INTERNA = """
BASE AS
(
    SELECT
        PAR.CODPARC,
        PAR.NOMEPARC AS FANTASIA,
        PAR.RAZAOSOCIAL,
        PAR.CGC_CPF,
        NVL(PAR.CODPARCMATRIZ, PAR.CODPARC) AS CODGRUPO,
        PMAT.NOMEPARC AS PARCEIRO_MATRIZ,
        PAR.CODCID,
        CID.NOMECID AS CIDADE,
        PAR.CODBAI,
        BAI.NOMEBAI AS BAIRRO,
        PAR.TELEFONE,
        PAR.OBSERVACOES,
        CAST(NULL AS NUMBER) AS CODVEND_EXTERNO,
        CAST(NULL AS VARCHAR2(1)) AS VENDEDOR
    FROM TGFPAR PAR
    LEFT JOIN TGFPAR PMAT ON PMAT.CODPARC = NVL(PAR.CODPARCMATRIZ, PAR.CODPARC)
    INNER JOIN TSICID CID ON CID.CODCID = PAR.CODCID
    INNER JOIN TSIUFS UFS ON UFS.CODUF = CID.UF
    LEFT JOIN TSIBAI BAI ON BAI.CODBAI = PAR.CODBAI
    WHERE PAR.TIPPESSOA = 'J'
      AND PAR.CLIENTE = 'S'
      AND PAR.ATIVO = 'S'
      AND UFS.UF = 'AL'
      AND NVL(PAR.CODVEND, 0) = 0
)"""

# Parte comum às duas listas: última compra (parceiro, senão família), atraso
# do grupo e última ordem de carga. FIN_PEND é cópia fiel da consulta do admin
# (RECDESP <> 0 confirmado como correto por ele em 03/10).
_RESTO = """,
GRUPOS AS
(
    SELECT DISTINCT CODGRUPO FROM BASE
),
VENDAS_BASE AS
(
    SELECT CAB.CODPARC, NVL(PAR.CODPARCMATRIZ, PAR.CODPARC) AS CODGRUPO, CAB.DTNEG
      FROM TGFCAB CAB
     INNER JOIN TGFPAR PAR ON PAR.CODPARC = CAB.CODPARC
     INNER JOIN GRUPOS G ON G.CODGRUPO = NVL(PAR.CODPARCMATRIZ, PAR.CODPARC)
     WHERE CAB.CODEMP = :CODEMP
       AND CAB.CODTIPOPER IN ({tops})
),
C_VENDAS_PAR AS
(
    SELECT CODPARC, MAX(DTNEG) AS DT_ULT_COMPRA FROM VENDAS_BASE GROUP BY CODPARC
),
C_VENDAS_FAM AS
(
    SELECT CODGRUPO, MAX(DTNEG) AS DT_ULT_COMPRA_FAM FROM VENDAS_BASE GROUP BY CODGRUPO
),
FIN_PEND AS
(
    SELECT
        NVL(PAR.CODPARCMATRIZ, PAR.CODPARC) AS CODGRUPO,
        MAX(
            CASE
                WHEN FIN.DTVENC IS NOT NULL
                THEN GREATEST(TRUNC(SYSDATE) - TRUNC(FIN.DTVENC), 0)
                ELSE 0
            END
        ) AS DIAS_MAIOR_ATRASO,
        SUM(CASE WHEN FIN.DTVENC < SYSDATE THEN NVL(FIN.VLRDESDOB, 0) ELSE 0 END) AS VLR_ATRASADO
    FROM TGFFIN FIN
    INNER JOIN TGFPAR PAR ON PAR.CODPARC = FIN.CODPARC
    INNER JOIN GRUPOS G ON G.CODGRUPO = NVL(PAR.CODPARCMATRIZ, PAR.CODPARC)
    WHERE FIN.DHBAIXA IS NULL
      AND FIN.PROVISAO = 'N'
      AND FIN.RECDESP <> 0
      AND
      (
          (
              FIN.CODTIPTIT = 3
              AND FIN.CODTIPOPER <> 1657
              AND FIN.CGC_CPF_CMC7 IS NOT NULL
              AND FIN.DTVENC < SYSDATE
          )
          OR
          (
              FIN.CODTIPTIT IN (41,42)
              AND FIN.DTVENC < SYSDATE
              AND NOT EXISTS
              (
                  SELECT 1 FROM TGFTEF TEF WHERE TEF.NUFIN = FIN.NUFIN AND TEF.NUMNSU IS NOT NULL
              )
          )
          OR
          (
              FIN.CODTIPTIT IN (4,2)
              AND FIN.DTVENC < SYSDATE
          )
          OR
          (
              FIN.CODTIPTIT = 3
              AND FIN.CODTIPOPER = 1657
              AND FIN.RECDESP = 1
          )
      )
    GROUP BY NVL(PAR.CODPARCMATRIZ, PAR.CODPARC)
),
-- Última ordem de carga: a nota mais recente do cliente, entre as TOPs de
-- compra, que JÁ TEM ordem de carga. Um pedido de hoje ainda sem carregamento
-- não esconde a última entrega real (plano §12, pedido novo de 03/10).
-- A data da OC é a da carga (TGFORD.DTPREVSAIDA, previsão de saída), como no
-- relatório do admin (docs/rel_com_ordem_carga.txt no televendas), não a do pedido.
ULT_OC AS
(
    SELECT CAB.CODPARC,
           MAX(CAB.ORDEMCARGA) KEEP (DENSE_RANK LAST ORDER BY CAB.DTNEG, CAB.NUNOTA) AS ORDEMCARGA
      FROM TGFCAB CAB
     INNER JOIN BASE B ON B.CODPARC = CAB.CODPARC
     WHERE CAB.CODEMP = :CODEMP
       AND CAB.CODTIPOPER IN ({tops})
       AND NVL(CAB.ORDEMCARGA, 0) > 0
     GROUP BY CAB.CODPARC
)
SELECT
    B.CODPARC, B.FANTASIA, B.RAZAOSOCIAL, B.CGC_CPF, B.CODGRUPO, B.PARCEIRO_MATRIZ,
    B.CODCID, B.CIDADE, B.CODBAI, B.BAIRRO, B.TELEFONE, B.OBSERVACOES,
    B.CODVEND_EXTERNO, B.VENDEDOR,
    NVL(VP.DT_ULT_COMPRA, VF.DT_ULT_COMPRA_FAM) AS ULTIMA_COMPRA,
    NVL(FP.DIAS_MAIOR_ATRASO, 0) AS DIAS_ATRASO,
    NVL(FP.VLR_ATRASADO, 0) AS VLR_ATRASADO,
    UO.ORDEMCARGA,
    ORD.DTPREVSAIDA,
    ORD.HORASAIDA
FROM BASE B
LEFT JOIN C_VENDAS_PAR VP ON VP.CODPARC = B.CODPARC
LEFT JOIN C_VENDAS_FAM VF ON VF.CODGRUPO = B.CODGRUPO
LEFT JOIN FIN_PEND FP ON FP.CODGRUPO = B.CODGRUPO
LEFT JOIN ULT_OC UO ON UO.CODPARC = B.CODPARC
LEFT JOIN TGFORD ORD ON ORD.CODEMP = :CODEMP AND ORD.ORDEMCARGA = UO.ORDEMCARGA
WHERE NVL(FP.DIAS_MAIOR_ATRASO, 0) <= :ATRASO_MAX
ORDER BY B.CIDADE, B.BAIRRO, B.VENDEDOR, B.FANTASIA
"""

# Hoje e o dia da semana ISO (1 = seg … 7 = dom), pelo relógio do ORACLE — a
# mesma conta da consulta do admin. O container roda em UTC; o Oracle, em UTC-3.
SQL_HOJE = "SELECT TRUNC(SYSDATE), TRUNC(SYSDATE) - TRUNC(SYSDATE, 'IW') + 1 FROM DUAL"

SQL_CONTATOS = """
    SELECT CTT.CODPARC, CTT.CODCONTATO, CTT.NOMECONTATO, CTT.TELEFONE, CTT.CELULAR, CTT.EMAIL
      FROM TGFCTT CTT
     WHERE CTT.CODPARC IN ({codparcs})
       AND (TRIM(CTT.TELEFONE) IS NOT NULL OR TRIM(CTT.CELULAR) IS NOT NULL)
     ORDER BY CTT.CODPARC, CTT.CODCONTATO
"""

DIA_CURTO = {1: "SEG", 2: "TER", 3: "QUA", 4: "QUI", 5: "SEX"}
# Liberado a partir do dia SEGUINTE ao da visita (DIA_VISITA < dia atual).
LIBERA_EM = {1: "terça", 2: "quarta", 3: "quinta", 4: "sexta", 5: "sábado"}

LOTE_IN = 900  # Oracle aceita no máximo 1000 itens num IN (...)


def _binds(prefixo, valores):
    nomes = [f"{prefixo}{i}" for i in range(len(valores))]
    return ", ".join(f":{n}" for n in nomes), dict(zip(nomes, valores))


def sql_lista(lista, so_clientes=True):
    """Monta o SQL da lista ('CARTEIRA' | 'INTERNA'). Os IN de TOP ficam {tops}."""
    if lista == "CARTEIRA":
        base = _BASE_CARTEIRA.format(filtro_cliente="AND A.CLIENTE = 'S'" if so_clientes else "")
    else:
        base = _BASE_INTERNA
    return "WITH" + base + _RESTO


# ---------------------------------------------------------------------------
# Configuração (Postgres)
# ---------------------------------------------------------------------------


class ConfiguracaoIncompleta(RuntimeError):
    """Falta TOP ou parâmetro no Postgres — a lista sairia errada sem aviso."""


def ler_configuracao(cur_pg):
    """TOPs de última compra + parâmetros, lidos de televendas.top/parametro."""
    cur_pg.execute("SELECT codtipoper FROM top WHERE ativo AND ultima_compra ORDER BY codtipoper")
    tops = [r[0] for r in cur_pg.fetchall()]
    cur_pg.execute("SELECT chave, valor FROM parametro")
    params = dict(cur_pg.fetchall())
    faltam = [c for c in ("ATRASO_MAX_DIAS", "CODEMP") if c not in params]
    if not tops or faltam:
        raise ConfiguracaoIncompleta(
            "Configuração do televendas incompleta: "
            + ("nenhuma TOP de última compra; " if not tops else "")
            + (f"faltam os parâmetros {', '.join(faltam)}" if faltam else "")
            + ". Rode scripts/migrar.py."
        )
    return {"tops": tops, "atraso_max": int(params["ATRASO_MAX_DIAS"]), "codemp": int(params["CODEMP"])}


def ler_escala(cur_pg, codvends):
    """(codvend, 'C'|'B', codcid|codbai) -> dia. Local repetido vale o MAIOR dia (como o MAX da consulta)."""
    if not codvends:
        return {}
    cur_pg.execute(
        """SELECT codvend, tipo_local, codcid, codbai, dia_visita
             FROM escala WHERE ativo AND codvend = ANY(%s)""",
        (list(codvends),),
    )
    idx = {}
    for codvend, tipo, codcid, codbai, dia in cur_pg.fetchall():
        chave = (codvend, tipo, codcid if tipo == "C" else codbai)
        idx[chave] = max(dia, idx.get(chave, 0))
    return idx


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------


def _txt(valor):
    if valor is None:
        return None
    limpo = str(valor).strip()
    return limpo or None


def _data(valor):
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    return None


def _hora(valor):
    """HORASAIDA pode vir como data/hora ou como número HHMM, conforme a instalação."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.strftime("%H:%M")
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return _txt(valor)
    return f"{n // 100:02d}:{n % 100:02d}"


def dia_da_escala(escala, cli):
    """Maior dia de visita entre a regra da cidade e a do bairro (ou None)."""
    codvend = cli["codVendExterno"]
    if codvend is None:
        return None
    dias = [
        escala.get((codvend, "C", cli["codCid"])),
        escala.get((codvend, "B", cli["codBai"])),
    ]
    dias = [d for d in dias if d]
    return max(dias) if dias else None


def liberado(dia_visita, dia_semana):
    """Mesma regra da consulta: ter–sáb, e a visita já aconteceu nesta semana."""
    return dia_visita is not None and 2 <= dia_semana <= 6 and dia_visita < dia_semana


def buscar_clientes(cur_ora, lista, config, codtelevend=None, so_clientes=True):
    """Executa a consulta da lista no Oracle e devolve (hoje, dia_semana, [clientes])."""
    cur_ora.execute(SQL_HOJE)
    hoje, dia_semana = cur_ora.fetchone()
    hoje = hoje.date() if isinstance(hoje, datetime) else hoje

    tops_sql, binds = _binds("T", config["tops"])
    binds.update({"CODEMP": config["codemp"], "ATRASO_MAX": config["atraso_max"]})
    if lista == "CARTEIRA":
        binds["CODTELEVEND"] = codtelevend
    cur_ora.execute(sql_lista(lista, so_clientes).replace("{tops}", tops_sql), binds)

    clientes = []
    for r in cur_ora.fetchall():
        ultima = r[14].date() if isinstance(r[14], datetime) else r[14]
        clientes.append(
            {
                "codParc": int(r[0]),
                "fantasia": _txt(r[1]),
                "razaoSocial": _txt(r[2]),
                "cgcCpf": _txt(r[3]),
                "codParcMatriz": int(r[4]) if r[4] is not None else None,
                "parceiroMatriz": _txt(r[5]),
                "codCid": int(r[6]) if r[6] is not None else None,
                "cidade": _txt(r[7]),
                "codBai": int(r[8]) if r[8] is not None else None,
                "bairro": _txt(r[9]),
                "telefone": _txt(r[10]),
                "observacoes": _txt(r[11]),
                "codVendExterno": int(r[12]) if r[12] is not None else None,
                "vendedor": _txt(r[13]),
                "ultimaCompra": _data(ultima),
                "diasSemCompra": (hoje - ultima).days if ultima else None,
                "diasAtraso": int(r[15] or 0),
                "vlrAtrasado": float(r[16] or 0),
                "ultimaOrdemCarga": int(r[17]) if r[17] else None,
                "dataOrdemCarga": _data(r[18]),
                "horaSaidaOrdem": _hora(r[19]),
                "contatos": [],
            }
        )
    return hoje, int(dia_semana), clientes


def anexar_contatos(cur_ora, clientes):
    """Todos os contatos da TGFCTT com telefone ou celular, em lotes de IN."""
    por_parc = {c["codParc"]: c for c in clientes}
    codigos = list(por_parc)
    for i in range(0, len(codigos), LOTE_IN):
        lote = codigos[i:i + LOTE_IN]
        placeholders, binds = _binds("P", lote)
        cur_ora.execute(SQL_CONTATOS.format(codparcs=placeholders), binds)
        for codparc, codcontato, nome, tel, cel, email in cur_ora.fetchall():
            por_parc[int(codparc)]["contatos"].append(
                {
                    "codContato": int(codcontato) if codcontato is not None else None,
                    "nome": _txt(nome),
                    "telefone": _txt(tel),
                    "celular": _txt(cel),
                    "email": _txt(email),
                }
            )


def anexar_rota(clientes, escala, dia_semana):
    for c in clientes:
        dia = dia_da_escala(escala, c)
        c["rota"] = {
            "diaVisita": dia,
            "diaVisitaDesc": DIA_CURTO.get(dia, "SEM ROTA"),
            "liberado": liberado(dia, dia_semana),
            "liberaEm": LIBERA_EM.get(dia),
        }


def anexar_contatos_televendas(cur_pg, clientes):
    """Último contato, contatos de hoje, retorno agendado e trava — do Postgres."""
    codigos = [c["codParc"] for c in clientes]
    por_parc = {c["codParc"]: c for c in clientes}
    for c in clientes:
        c["televendas"] = {"ultimoContato": None, "contatosHoje": 0, "retornoEm": None, "emChamada": None}
    if not codigos:
        return

    cur_pg.execute(
        """SELECT DISTINCT ON (codparc) codparc, inicio, nome_usu, resultado, desfecho, retorno_em
             FROM chamada
            WHERE situacao = 'FINALIZADA' AND codparc = ANY(%s)
            ORDER BY codparc, inicio DESC""",
        (codigos,),
    )
    for codparc, inicio, nome, resultado, desfecho, retorno in cur_pg.fetchall():
        tv = por_parc[codparc]["televendas"]
        tv["ultimoContato"] = {
            "em": inicio.isoformat(),
            "nomeUsu": nome,
            "resultado": resultado,
            "desfecho": desfecho,
        }
        tv["retornoEm"] = retorno.isoformat() if retorno else None

    # current_date na sessão = data de Maceió (pg.py abre a sessão nesse fuso).
    cur_pg.execute(
        """SELECT codparc, count(*) FROM chamada
            WHERE situacao = 'FINALIZADA' AND inicio::date = current_date AND codparc = ANY(%s)
            GROUP BY codparc""",
        (codigos,),
    )
    for codparc, n in cur_pg.fetchall():
        por_parc[codparc]["televendas"]["contatosHoje"] = int(n)

    cur_pg.execute(
        """SELECT codparc, codusu, nome_usu, inicio, expira FROM chamada
            WHERE situacao = 'ABERTA' AND expira > now() AND codparc = ANY(%s)""",
        (codigos,),
    )
    for codparc, codusu, nome, inicio, expira in cur_pg.fetchall():
        por_parc[codparc]["televendas"]["emChamada"] = {
            "codUsu": codusu,
            "nomeUsu": nome,
            "desde": inicio.isoformat(),
            "expiraEm": expira.isoformat(),
        }
