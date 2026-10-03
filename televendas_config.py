"""Configuração do televendas editada pela gerência: escala, TOPs e parâmetros.

Os dados moram no Postgres (televendas.escala / top / parametro); os NOMES
(representante, cidade, bairro, operação) vêm do Oracle na hora de mostrar.
Cada escrita valida contra o Sankhya que o código existe e grava quem alterou.
"""

DIAS = {1: "segunda", 2: "terça", 3: "quarta", 4: "quinta", 5: "sexta"}

# Parâmetros editáveis e seus limites. Chave fora daqui não é aceita pela API,
# para a tela não virar um editor de banco.
PARAMETROS = {
    "JANELA_ATRIBUICAO_DIAS": (0, 30),
    "ATRASO_MAX_DIAS": (0, 365),
    "TRAVA_MINUTOS": (5, 120),
    "CODEMP": (1, 9999),
}


class Invalido(ValueError):
    """Pedido com dado inválido: vira 400 com a mensagem."""


def _binds(prefixo, valores):
    nomes = [f"{prefixo}{i}" for i in range(len(valores))]
    return ", ".join(f":{n}" for n in nomes), dict(zip(nomes, valores))


def _nomes(cur_ora, sql, ids):
    """{id: (colunas...)} para os ids pedidos, em lotes de IN."""
    ids = sorted({int(i) for i in ids if i is not None})
    out = {}
    for i in range(0, len(ids), 900):
        lote = ids[i:i + 900]
        ph, b = _binds("I", lote)
        cur_ora.execute(sql.format(ids=ph), b)
        for r in cur_ora.fetchall():
            out[int(r[0])] = r[1:]
    return out


SQL_VEND = "SELECT V.CODVEND, V.APELIDO, V.AD_CODVEND FROM TGFVEN V WHERE V.CODVEND IN ({ids})"
SQL_CID = """SELECT CID.CODCID, CID.NOMECID, UFS.UF FROM TSICID CID
               LEFT JOIN TSIUFS UFS ON UFS.CODUF = CID.UF WHERE CID.CODCID IN ({ids})"""
SQL_BAI = "SELECT BAI.CODBAI, BAI.NOMEBAI FROM TSIBAI BAI WHERE BAI.CODBAI IN ({ids})"
# Descrição da versão MAIS RECENTE de cada TOP (a TGFTOP é versionada por DHALTER).
SQL_TOP = """SELECT T.CODTIPOPER, MAX(T.DESCROPER) KEEP (DENSE_RANK LAST ORDER BY T.DHALTER)
               FROM TGFTOP T WHERE T.CODTIPOPER IN ({ids}) GROUP BY T.CODTIPOPER"""

# Representantes externos ligados a algum televendas (os únicos com sentido na escala).
SQL_REPRESENTANTES = """
    SELECT V.CODVEND, V.APELIDO, V.AD_CODVEND, TV.APELIDO, V.ATIVO
      FROM TGFVEN V
      LEFT JOIN TGFVEN TV ON TV.CODVEND = V.AD_CODVEND
     WHERE NVL(V.AD_CODVEND, 0) > 0
     ORDER BY V.APELIDO
"""

SQL_BUSCA_CIDADE = """
    SELECT * FROM (
        SELECT CID.CODCID, CID.NOMECID, UFS.UF
          FROM TSICID CID JOIN TSIUFS UFS ON UFS.CODUF = CID.UF
         WHERE UFS.UF = 'AL' AND UPPER(CID.NOMECID) LIKE UPPER(:Q)
         ORDER BY CID.NOMECID
    ) WHERE ROWNUM <= 30
"""
SQL_BUSCA_BAIRRO = """
    SELECT * FROM (
        SELECT BAI.CODBAI, BAI.NOMEBAI FROM TSIBAI BAI
         WHERE UPPER(BAI.NOMEBAI) LIKE UPPER(:Q)
         ORDER BY BAI.NOMEBAI
    ) WHERE ROWNUM <= 30
"""


def _txt(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _quando(v):
    return v.isoformat() if v else None


# ---------------------------------------------------------------------------
# Escala
# ---------------------------------------------------------------------------


def listar_escala(cur_pg, cur_ora):
    cur_pg.execute(
        """SELECT id, codvend, dia_visita, tipo_local, codcid, codbai, ativo, alterado_por, alterado_em
             FROM escala ORDER BY codvend, dia_visita, id"""
    )
    linhas = cur_pg.fetchall()
    vend = _nomes(cur_ora, SQL_VEND, [r[1] for r in linhas])
    cid = _nomes(cur_ora, SQL_CID, [r[4] for r in linhas])
    bai = _nomes(cur_ora, SQL_BAI, [r[5] for r in linhas])
    out = []
    for id_, codvend, dia, tipo, codcid, codbai, ativo, por, em in linhas:
        if tipo == "C":
            nome, uf = cid.get(codcid, (None, None))
            local = {"codigo": codcid, "nome": _txt(nome), "uf": _txt(uf)}
        else:
            (nome,) = bai.get(codbai, (None,))
            local = {"codigo": codbai, "nome": _txt(nome), "uf": None}
        out.append(
            {
                "id": id_,
                "codVend": codvend,
                "representante": _txt(vend.get(codvend, (None,))[0]),
                "diaVisita": dia,
                "dia": DIAS[dia],
                "tipoLocal": tipo,
                "local": local,
                "ativo": ativo,
                "alteradoPor": por,
                "alteradoEm": _quando(em),
            }
        )
    return out


def _int(dados, campo, obrigatorio=True):
    v = dados.get(campo)
    if v in (None, ""):
        if obrigatorio:
            raise Invalido(f"Campo '{campo}' é obrigatório.")
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise Invalido(f"Campo '{campo}' precisa ser número.")


def _existe(cur_ora, sql, codigo, msg):
    if not _nomes(cur_ora, sql, [codigo]):
        raise Invalido(msg)


def criar_escala(cur_pg, cur_ora, dados, quem):
    codvend = _int(dados, "codVend")
    dia = _int(dados, "diaVisita")
    tipo = dados.get("tipoLocal")
    if dia not in DIAS:
        raise Invalido("diaVisita vai de 1 (segunda) a 5 (sexta).")
    if tipo not in ("C", "B"):
        raise Invalido("tipoLocal é 'C' (cidade) ou 'B' (bairro).")
    codigo = _int(dados, "codCid" if tipo == "C" else "codBai")
    _existe(cur_ora, SQL_VEND, codvend, f"Vendedor {codvend} não existe no Sankhya.")
    if tipo == "C":
        _existe(cur_ora, SQL_CID, codigo, f"Cidade {codigo} não existe no Sankhya.")
    else:
        _existe(cur_ora, SQL_BAI, codigo, f"Bairro {codigo} não existe no Sankhya.")

    col = "codcid" if tipo == "C" else "codbai"
    cur_pg.execute(
        f"SELECT dia_visita FROM escala WHERE ativo AND codvend = %s AND tipo_local = %s AND {col} = %s",
        (codvend, tipo, codigo),
    )
    ja = cur_pg.fetchone()
    if ja:
        raise Invalido(f"Esse local já está na escala deste representante ({DIAS[ja[0]]}). Mude o dia na linha existente.")
    cur_pg.execute(
        f"""INSERT INTO escala (codvend, dia_visita, tipo_local, {col}, alterado_por, alterado_em)
            VALUES (%s, %s, %s, %s, %s, now()) RETURNING id""",
        (codvend, dia, tipo, codigo, quem),
    )
    return cur_pg.fetchone()[0]


def alterar_escala(cur_pg, id_, dados, quem):
    sets, vals = [], []
    if "diaVisita" in dados:
        dia = _int(dados, "diaVisita")
        if dia not in DIAS:
            raise Invalido("diaVisita vai de 1 (segunda) a 5 (sexta).")
        sets.append("dia_visita = %s")
        vals.append(dia)
    if "ativo" in dados:
        sets.append("ativo = %s")
        vals.append(bool(dados["ativo"]))
    if not sets:
        raise Invalido("Nada para alterar (diaVisita ou ativo).")
    cur_pg.execute(
        f"UPDATE escala SET {', '.join(sets)}, alterado_por = %s, alterado_em = now() WHERE id = %s",
        (*vals, quem, id_),
    )
    return cur_pg.rowcount


def representantes(cur_ora):
    cur_ora.execute(SQL_REPRESENTANTES)
    return [
        {
            "codVend": int(r[0]),
            "apelido": _txt(r[1]),
            "codTelevend": int(r[2]),
            "televendas": _txt(r[3]),
            "ativo": r[4] == "S",
        }
        for r in cur_ora.fetchall()
    ]


def buscar_local(cur_ora, tipo, termo):
    termo = (termo or "").strip()
    if len(termo) < 2:
        return []
    cur_ora.execute(SQL_BUSCA_CIDADE if tipo == "C" else SQL_BUSCA_BAIRRO, {"Q": f"%{termo}%"})
    return [
        {"codigo": int(r[0]), "nome": _txt(r[1]), "uf": _txt(r[2]) if len(r) > 2 else None}
        for r in cur_ora.fetchall()
    ]


# ---------------------------------------------------------------------------
# TOPs
# ---------------------------------------------------------------------------


def listar_tops(cur_pg, cur_ora):
    cur_pg.execute(
        "SELECT codtipoper, conversao, ultima_compra, ativo, alterado_por, alterado_em FROM top ORDER BY codtipoper"
    )
    linhas = cur_pg.fetchall()
    desc = _nomes(cur_ora, SQL_TOP, [r[0] for r in linhas])
    return [
        {
            "codTipOper": cod,
            "descricao": _txt(desc.get(cod, (None,))[0]),
            "conversao": conv,
            "ultimaCompra": ult,
            "ativo": ativo,
            "alteradoPor": por,
            "alteradoEm": _quando(em),
        }
        for cod, conv, ult, ativo, por, em in linhas
    ]


def _conversao(dados):
    v = dados.get("conversao") or None
    if v not in (None, "ORCAMENTO", "PEDIDO"):
        raise Invalido("conversao é ORCAMENTO, PEDIDO ou vazio.")
    return v


def salvar_top(cur_pg, cur_ora, cod, dados, quem, criar=False):
    if criar:
        _existe(cur_ora, SQL_TOP, cod, f"A TOP {cod} não existe no Sankhya.")
        cur_pg.execute(
            """INSERT INTO top (codtipoper, conversao, ultima_compra, ativo, alterado_por, alterado_em)
               VALUES (%s, %s, %s, true, %s, now())
               ON CONFLICT (codtipoper) DO NOTHING""",
            (cod, _conversao(dados), bool(dados.get("ultimaCompra")), quem),
        )
        if not cur_pg.rowcount:
            raise Invalido(f"A TOP {cod} já está cadastrada; altere a linha existente.")
        return 1
    sets, vals = [], []
    if "conversao" in dados:
        sets.append("conversao = %s")
        vals.append(_conversao(dados))
    for campo, col in (("ultimaCompra", "ultima_compra"), ("ativo", "ativo")):
        if campo in dados:
            sets.append(f"{col} = %s")
            vals.append(bool(dados[campo]))
    if not sets:
        raise Invalido("Nada para alterar (conversao, ultimaCompra ou ativo).")
    cur_pg.execute(
        f"UPDATE top SET {', '.join(sets)}, alterado_por = %s, alterado_em = now() WHERE codtipoper = %s",
        (*vals, quem, cod),
    )
    return cur_pg.rowcount


def checar_tops_minimas(cur_pg):
    """Sem nenhuma TOP de última compra, as listas inteiras deixariam de funcionar."""
    cur_pg.execute("SELECT count(*) FROM top WHERE ativo AND ultima_compra")
    if not cur_pg.fetchone()[0]:
        raise Invalido("Pelo menos uma TOP precisa continuar ativa como 'última compra': sem ela as listas param.")


# ---------------------------------------------------------------------------
# Parâmetros
# ---------------------------------------------------------------------------


def listar_parametros(cur_pg):
    cur_pg.execute("SELECT chave, valor, descricao, alterado_por, alterado_em FROM parametro ORDER BY chave")
    return [
        {
            "chave": c,
            "valor": v,
            "descricao": d,
            "minimo": PARAMETROS.get(c, (None, None))[0],
            "maximo": PARAMETROS.get(c, (None, None))[1],
            "alteradoPor": por,
            "alteradoEm": _quando(em),
        }
        for c, v, d, por, em in cur_pg.fetchall()
    ]


def salvar_parametro(cur_pg, chave, dados, quem):
    if chave not in PARAMETROS:
        raise Invalido(f"Parâmetro '{chave}' não é editável.")
    valor = _int(dados, "valor")
    lo, hi = PARAMETROS[chave]
    if not lo <= valor <= hi:
        raise Invalido(f"{chave} vai de {lo} a {hi}.")
    cur_pg.execute(
        "UPDATE parametro SET valor = %s, alterado_por = %s, alterado_em = now() WHERE chave = %s",
        (str(valor), quem, chave),
    )
    return cur_pg.rowcount
