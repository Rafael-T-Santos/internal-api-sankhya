"""Ligações recebidas do televendas: busca de quem ligou, cliente avulso,
contatos sem cadastro e assuntos.

Plano no repositório do front: televendas/docs/PLANO-LIGACOES-RECEBIDAS.md.

Quem liga pode ser de qualquer carteira, de nenhuma lista, pessoa física ou nem
ter cadastro. Por isso aqui nada filtra por lista: a busca olha a TGFPAR inteira
e diz DE QUEM é o cliente (carteira de qual televendas, fila interna ou nenhuma).
"""

import re
import unicodedata
from datetime import datetime

import televendas_listas as listas

MAX_RESULTADOS = 30
# Telefone: compara os últimos 8 dígitos. São os únicos presentes em todos os
# formatos do cadastro (com 55, com DDD, sem DDD, celular antigo sem o 9).
DIGITOS_FONE = 8


from televendas_config import Invalido  # noqa: E402  (400 com a mensagem nos helpers)


class JaCadastrado(Exception):
    """O documento do contato já existe no Sankhya: vira 409 com o codParc."""

    def __init__(self, codparc):
        super().__init__(f"Este documento já tem cadastro no Sankhya (cliente #{codparc}).")
        self.codparc = codparc


def _txt(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _iso(v):
    return v.isoformat() if v else None


def _sem_acento(s):
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")


def digitos(v):
    return re.sub(r"\D", "", str(v or ""))


def _dv_ok(base, pesos):
    soma = sum(int(d) * p for d, p in zip(base, pesos))
    resto = soma % 11
    return "0" if resto < 2 else str(11 - resto)


def cpf_valido(d):
    if len(d) != 11 or d == d[0] * 11:
        return False
    d1 = _dv_ok(d[:9], range(10, 1, -1))
    d2 = _dv_ok(d[:9] + d1, range(11, 1, -1))
    return d[9:] == d1 + d2


def cnpj_valido(d):
    if len(d) != 14 or d == d[0] * 14:
        return False
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    p2 = [6] + p1
    d1 = _dv_ok(d[:12], p1)
    d2 = _dv_ok(d[:12] + d1, p2)
    return d[12:] == d1 + d2


def documento_valido(d):
    return cpf_valido(d) if len(d) == 11 else cnpj_valido(d) if len(d) == 14 else False


# ---------------------------------------------------------------------------
# O que foi digitado na busca
# ---------------------------------------------------------------------------


def entender(q):
    """Devolve os critérios de busca para o texto digitado.

    {doc, codparc, fone, texto}: só os que fazem sentido vêm preenchidos. Com 11
    dígitos busca CPF E telefone (celular com DDD também tem 11).
    """
    q = (q or "").strip()
    d = digitos(q)
    so_numero = bool(d) and not re.search(r"[A-Za-zÀ-ÿ]", q)
    crit = {"doc": None, "codparc": None, "fone": None, "texto": None}
    if not so_numero:
        t = _sem_acento(q).upper()
        if len(t.replace(" ", "")) < 2:
            raise Invalido("Digite pelo menos 2 letras, ou um CNPJ, CPF, código ou telefone.")
        crit["texto"] = t
        return crit
    if len(d) <= 7:
        crit["codparc"] = int(d)
    if len(d) in (11, 14):
        crit["doc"] = d
    if 8 <= len(d) <= 13:
        crit["fone"] = d[-DIGITOS_FONE:]
    return crit


# ---------------------------------------------------------------------------
# Oracle: parceiros
# ---------------------------------------------------------------------------

_COLUNAS = """P.CODPARC, P.NOMEPARC, P.RAZAOSOCIAL, P.CGC_CPF, CID.NOMECID, UFS.UF,
       P.ATIVO, P.CLIENTE, P.TELEFONE, P.TIPPESSOA, NVL(P.CODVEND, 0),
       VEXT.APELIDO, NVL(VEXT.AD_CODVEND, 0), TV.APELIDO"""

_DE = """FROM TGFPAR P
  LEFT JOIN TSICID CID ON CID.CODCID = P.CODCID
  LEFT JOIN TSIUFS UFS ON UFS.CODUF = CID.UF
  LEFT JOIN TGFVEN VEXT ON VEXT.CODVEND = P.CODVEND
  LEFT JOIN TGFVEN TV ON TV.CODVEND = VEXT.AD_CODVEND"""

_FONE = """(SUBSTR(REGEXP_REPLACE(P.TELEFONE, '[^0-9]', ''), -8) = :FONE
        OR EXISTS (SELECT 1 FROM TGFCTT C
                    WHERE C.CODPARC = P.CODPARC
                      AND (SUBSTR(REGEXP_REPLACE(C.TELEFONE, '[^0-9]', ''), -8) = :FONE
                           OR SUBSTR(REGEXP_REPLACE(C.CELULAR, '[^0-9]', ''), -8) = :FONE)))"""


def sql_busca(crit):
    """SQL e binds da busca na TGFPAR. Os critérios presentes entram com OR."""
    conds, binds = [], {}
    if crit["codparc"] is not None:
        conds.append("P.CODPARC = :COD")
        binds["COD"] = crit["codparc"]
    if crit["doc"]:
        conds.append("REGEXP_REPLACE(P.CGC_CPF, '[^0-9]', '') = :DOC")
        binds["DOC"] = crit["doc"]
    if crit["fone"]:
        conds.append(_FONE)
        binds["FONE"] = crit["fone"]
    if crit["texto"]:
        conds.append("(UPPER(P.NOMEPARC) LIKE :TXT OR UPPER(P.RAZAOSOCIAL) LIKE :TXT)")
        binds["TXT"] = f"%{crit['texto']}%"
    sql = f"""SELECT * FROM (
        SELECT {_COLUNAS}
          {_DE}
         WHERE {' OR '.join(conds)}
         ORDER BY CASE WHEN P.ATIVO = 'S' THEN 0 ELSE 1 END,
                  CASE WHEN P.CLIENTE = 'S' THEN 0 ELSE 1 END, P.NOMEPARC
    ) WHERE ROWNUM <= {MAX_RESULTADOS}"""
    return sql, binds


def dono(tippessoa, cliente, ativo, uf, codvend, apelido_rep, codtelevend, apelido_tv):
    """De quem é o cliente: carteira de um televendas, fila interna ou nenhuma lista.

    Carteira = o representante do cliente aponta para um televendas
    (TGFVEN.AD_CODVEND), a mesma ligação das listas. Fila interna = a regra da
    fila (PJ, cliente, ativo, de AL, sem vendedor).
    """
    if codtelevend:
        return {"tipo": "CARTEIRA", "codVend": int(codtelevend), "apelido": _txt(apelido_tv),
                "representante": _txt(apelido_rep), "codVendExterno": int(codvend) or None}
    if not codvend and tippessoa == "J" and cliente == "S" and ativo == "S" and uf == "AL":
        return {"tipo": "INTERNA"}
    return {"tipo": "NENHUMA", "representante": _txt(apelido_rep), "codVendExterno": int(codvend) or None}


def _parceiro(r):
    return {
        "codParc": int(r[0]),
        "fantasia": _txt(r[1]),
        "razaoSocial": _txt(r[2]),
        "cgcCpf": _txt(r[3]),
        "cidade": _txt(r[4]),
        "uf": _txt(r[5]),
        "ativo": r[6] == "S",
        "cliente": r[7] == "S",
        "telefone": _txt(r[8]),
        "dono": dono(r[9], r[7], r[6], _txt(r[5]), r[10], r[11], r[12], r[13]),
    }


def buscar_parceiros(cur_ora, crit):
    sql, binds = sql_busca(crit)
    cur_ora.execute(sql, binds)
    return [_parceiro(r) for r in cur_ora.fetchall()]


def parceiro(cur_ora, codparc):
    """Um parceiro com o dono, ou None."""
    cur_ora.execute(f"SELECT {_COLUNAS} {_DE} WHERE P.CODPARC = :COD", {"COD": codparc})
    r = cur_ora.fetchone()
    return _parceiro(r) if r else None


# Cliente avulso: a mesma montagem das listas (última compra, atraso, carga),
# com a base trocada por "este parceiro", sem filtro de lista nem de atraso.
_BASE_AVULSO = """
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
    LEFT JOIN TGFVEN VEXT ON VEXT.CODVEND = NULLIF(A.CODVEND, 0)
    WHERE A.CODPARC = :CODPARC
)"""

SEM_LIMITE_ATRASO = 10 ** 9


def cliente_avulso(cur_ora, cur_pg, codparc):
    """O cliente no MESMO formato das linhas das listas, para a ficha e o registro.

    Sem `rota` (a escala não vale na recebida) e com `dono`. None se não existe.
    """
    config = listas.ler_configuracao(cur_pg)
    cur_ora.execute(listas.SQL_HOJE)
    hoje, _ = cur_ora.fetchone()
    hoje = hoje.date() if isinstance(hoje, datetime) else hoje

    tops_sql, binds = listas._binds("T", config["tops"])
    binds.update({"CODEMP": config["codemp"], "ATRASO_MAX": SEM_LIMITE_ATRASO, "CODPARC": codparc})
    cur_ora.execute(("WITH" + _BASE_AVULSO + listas._RESTO).replace("{tops}", tops_sql), binds)
    linhas = cur_ora.fetchall()
    if not linhas:
        return hoje, None
    c = listas.linha_cliente(linhas[0], hoje)
    clientes = [c]
    listas.anexar_contatos(cur_ora, clientes)
    listas.anexar_contatos_televendas(cur_pg, clientes)
    p = parceiro(cur_ora, codparc)
    c["dono"] = p["dono"] if p else {"tipo": "NENHUMA"}
    c["ativo"] = p["ativo"] if p else None
    return hoje, c


# ---------------------------------------------------------------------------
# Postgres: contatos sem cadastro
# ---------------------------------------------------------------------------

_CONTATO_COLS = """k.id, k.documento, k.nome, k.fantasia, k.telefone, k.cidade, k.uf, k.obs, k.codparc,
       k.ligado_em, k.ligado_por, k.criado_em,
       (SELECT count(*) FROM chamada c WHERE c.contato_id = k.id AND c.situacao = 'FINALIZADA'),
       (SELECT max(c.inicio) FROM chamada c WHERE c.contato_id = k.id AND c.situacao = 'FINALIZADA'),
       (SELECT c.nome_usu FROM chamada c WHERE c.contato_id = k.id AND c.situacao = 'FINALIZADA'
         ORDER BY c.inicio DESC LIMIT 1)"""


def _contato(r):
    return {
        "id": r[0],
        "documento": r[1],
        "nome": r[2],
        "fantasia": r[3],
        "telefone": r[4],
        "cidade": r[5],
        "uf": r[6],
        "obs": r[7],
        "codParc": r[8],
        "ligadoEm": _iso(r[9]),
        "ligadoPor": r[10],
        "criadoEm": _iso(r[11]),
        "ligacoes": int(r[12] or 0),
        "ultimaLigacao": _iso(r[13]),
        "ultimoAtendente": r[14],
    }


# Nome digitado pelo operador pode ter acento; a busca chega sem (ver entender()).
_SEM_ACENTO_PG = "translate({}, 'áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ', 'aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC')"


def buscar_contatos(cur_pg, crit):
    """Contatos AINDA sem cadastro que batem com a busca."""
    conds, vals = [], []
    if crit["doc"]:
        conds.append("k.documento = %s")
        vals.append(crit["doc"])
    if crit["fone"]:
        conds.append("right(regexp_replace(coalesce(k.telefone, ''), '[^0-9]', '', 'g'), 8) = %s")
        vals.append(crit["fone"])
    if crit["texto"]:
        nome = _SEM_ACENTO_PG.format("k.nome")
        fantasia = _SEM_ACENTO_PG.format("coalesce(k.fantasia, '')")
        conds.append(f"(upper({nome}) LIKE %s OR upper({fantasia}) LIKE %s)")
        vals += [f"%{crit['texto']}%"] * 2
    if not conds:
        return []
    sql = f"""SELECT {_CONTATO_COLS} FROM contato k
               WHERE k.codparc IS NULL AND ({' OR '.join(conds)})
               ORDER BY k.criado_em DESC LIMIT {MAX_RESULTADOS}"""
    cur_pg.execute(sql, vals)
    return [_contato(r) for r in cur_pg.fetchall()]


def ler_contato(cur_pg, id_):
    cur_pg.execute(f"SELECT {_CONTATO_COLS} FROM contato k WHERE k.id = %s", (id_,))
    r = cur_pg.fetchone()
    return _contato(r) if r else None


def listar_contatos(cur_pg, pendentes=True):
    cur_pg.execute(
        f"SELECT {_CONTATO_COLS} FROM contato k"
        + (" WHERE k.codparc IS NULL" if pendentes else "")
        + " ORDER BY k.criado_em DESC LIMIT 500"
    )
    return [_contato(r) for r in cur_pg.fetchall()]


def _campo(dados, campo, limite, obrigatorio=False):
    v = _txt(dados.get(campo))
    if v and len(v) > limite:
        raise Invalido(f"'{campo}' passa de {limite} caracteres.")
    if obrigatorio and not v:
        raise Invalido(f"'{campo}' é obrigatório.")
    return v


def _documento(dados):
    bruto = dados.get("documento")
    if not _txt(bruto):
        return None
    d = digitos(bruto)
    if not documento_valido(d):
        raise Invalido("CNPJ ou CPF inválido (confira os dígitos).")
    return d


def _codparc_do_documento(cur_ora, doc):
    """Parceiros da TGFPAR com esse documento (lista de codparc)."""
    cur_ora.execute(
        "SELECT CODPARC FROM TGFPAR WHERE REGEXP_REPLACE(CGC_CPF, '[^0-9]', '') = :DOC ORDER BY CODPARC",
        {"DOC": doc},
    )
    return [int(r[0]) for r in cur_ora.fetchall()]


def criar_contato(cur_pg, cur_ora, dados, codusu):
    """201 {id}. Documento já no Sankhya -> JaCadastrado; já nos contatos -> o que existe."""
    doc = _documento(dados)
    nome = _campo(dados, "nome", 150, obrigatorio=True)
    uf = _campo(dados, "uf", 2)
    if doc:
        existentes = _codparc_do_documento(cur_ora, doc)
        if existentes:
            raise JaCadastrado(existentes[0])
        cur_pg.execute("SELECT id FROM contato WHERE documento = %s", (doc,))
        r = cur_pg.fetchone()
        if r:
            return {"id": r[0], "existente": True}
    cur_pg.execute(
        """INSERT INTO contato (documento, nome, fantasia, telefone, cidade, uf, obs, criado_por)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (
            doc,
            nome,
            _campo(dados, "fantasia", 150),
            _campo(dados, "telefone", 40),
            _campo(dados, "cidade", 100),
            uf.upper() if uf else None,
            _campo(dados, "obs", 1000),
            codusu,
        ),
    )
    return {"id": cur_pg.fetchone()[0], "existente": False}


def alterar_contato(cur_pg, cur_ora, id_, dados):
    atual = ler_contato(cur_pg, id_)
    if not atual:
        raise Invalido(f"Contato {id_} não existe.")
    campos = {}
    if "documento" in dados:
        doc = _documento(dados)
        if doc and doc != atual["documento"]:
            existentes = _codparc_do_documento(cur_ora, doc)
            if existentes:
                raise JaCadastrado(existentes[0])
            cur_pg.execute("SELECT 1 FROM contato WHERE documento = %s AND id <> %s", (doc, id_))
            if cur_pg.fetchone():
                raise Invalido("Já existe outro contato com este documento.")
        campos["documento"] = doc
    for campo, limite in (("nome", 150), ("fantasia", 150), ("telefone", 40), ("cidade", 100), ("uf", 2), ("obs", 1000)):
        if campo in dados:
            campos[campo] = _campo(dados, campo, limite, obrigatorio=campo == "nome")
    if campos.get("uf"):
        campos["uf"] = campos["uf"].upper()
    if not campos:
        raise Invalido("Nada para alterar.")
    sets = ", ".join(f"{k} = %s" for k in campos)
    cur_pg.execute(f"UPDATE contato SET {sets} WHERE id = %s", (*campos.values(), id_))
    return {}


def ligar_contato(cur_pg, id_, codparc, quem):
    """Liga o contato ao cadastro: o contato e as ligações dele passam ao cliente."""
    cur_pg.execute(
        "UPDATE contato SET codparc = %s, ligado_em = now(), ligado_por = %s WHERE id = %s AND codparc IS NULL",
        (codparc, quem, id_),
    )
    if not cur_pg.rowcount:
        return False
    cur_pg.execute("UPDATE chamada SET codparc = %s WHERE contato_id = %s AND codparc IS NULL", (codparc, id_))
    return True


def conferir_contato(cur_pg, cur_ora, id_):
    """Procura o documento do contato na TGFPAR e liga se achar UM parceiro só."""
    k = ler_contato(cur_pg, id_)
    if not k:
        raise Invalido(f"Contato {id_} não existe.")
    if k["codParc"]:
        return {"ligado": True, "codParc": k["codParc"], "motivo": "Já estava ligado."}
    if not k["documento"]:
        return {"ligado": False, "motivo": "Contato sem CNPJ/CPF: só a gerência liga à mão."}
    achados = _codparc_do_documento(cur_ora, k["documento"])
    if not achados:
        return {"ligado": False, "motivo": "Ainda não tem cadastro no Sankhya com este documento."}
    if len(achados) > 1:
        return {"ligado": False, "motivo": f"Mais de um cadastro com este documento ({', '.join(map(str, achados))}): a gerência liga à mão."}
    ligar_contato(cur_pg, id_, achados[0], "AUTO")
    return {"ligado": True, "codParc": achados[0]}


def ligar_pendentes(cur_pg, cur_ora):
    """Roda a conferência em todos os contatos pendentes com documento. Devolve quantos ligou."""
    cur_pg.execute("SELECT id FROM contato WHERE codparc IS NULL AND documento IS NOT NULL")
    n = 0
    for (id_,) in cur_pg.fetchall():
        if conferir_contato(cur_pg, cur_ora, id_)["ligado"]:
            n += 1
    return n


# ---------------------------------------------------------------------------
# Assuntos
# ---------------------------------------------------------------------------


def assuntos(cur_pg, so_ativos=True):
    cur_pg.execute(
        "SELECT id, descricao, ordem, ativo FROM assunto"
        + (" WHERE ativo" if so_ativos else "")
        + " ORDER BY ordem, descricao"
    )
    return [{"id": r[0], "descricao": r[1], "ordem": r[2], "ativo": r[3]} for r in cur_pg.fetchall()]


def criar_assunto(cur_pg, dados):
    descricao = _campo(dados, "descricao", 100, obrigatorio=True)
    cur_pg.execute("SELECT 1 FROM assunto WHERE upper(descricao) = upper(%s)", (descricao,))
    if cur_pg.fetchone():
        raise Invalido("Já existe um assunto com esse nome.")
    cur_pg.execute(
        "INSERT INTO assunto (descricao, ordem) VALUES (%s, (SELECT coalesce(max(ordem), 0) + 1 FROM assunto WHERE ordem < 99)) RETURNING id",
        (descricao,),
    )
    return {"id": cur_pg.fetchone()[0]}


def alterar_assunto(cur_pg, id_, dados):
    campos = {}
    if "descricao" in dados:
        campos["descricao"] = _campo(dados, "descricao", 100, obrigatorio=True)
    if "ativo" in dados:
        campos["ativo"] = bool(dados["ativo"])
    if "ordem" in dados:
        try:
            campos["ordem"] = int(dados["ordem"])
        except (TypeError, ValueError):
            raise Invalido("'ordem' precisa ser número.")
    if not campos:
        raise Invalido("Nada para alterar.")
    sets = ", ".join(f"{k} = %s" for k in campos)
    cur_pg.execute(f"UPDATE assunto SET {sets} WHERE id = %s", (*campos.values(), id_))
    if not cur_pg.rowcount:
        raise Invalido(f"Assunto {id_} não existe.")
    return {}


# ---------------------------------------------------------------------------
# Receita (cnpj.ws): preencher o formulário do contato
# ---------------------------------------------------------------------------


def dados_receita(bruto):
    """Recorta a resposta da cnpj.ws no que o formulário do contato usa."""
    est = bruto.get("estabelecimento") or {}
    cidade = (est.get("cidade") or {}).get("nome")
    uf = (est.get("estado") or {}).get("sigla")
    ddd, fone = est.get("ddd1"), est.get("telefone1")
    return {
        "nome": _txt(bruto.get("razao_social")),
        "fantasia": _txt(est.get("nome_fantasia")),
        "cidade": _txt(cidade),
        "uf": _txt(uf),
        "telefone": f"{ddd or ''}{fone or ''}" or None,
        "situacao": _txt(est.get("situacao_cadastral")),
    }
