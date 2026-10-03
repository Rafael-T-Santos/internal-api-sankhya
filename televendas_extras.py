"""Fase 5 do televendas: metas, "Meu dia", campanhas e roteiros.

Tudo no Postgres (tabelas da migração 001). Do Oracle vêm só nomes: operadores
(TSIUSU) e produtos (TGFPRO).

"Meu dia" conta o que o PRÓPRIO registro de ligação sabe: ligações, atendidas,
vendas e orçamentos marcados no desfecho e clientes com venda no mês. O valor em
R$ vendido depende da atribuição de notas da Fase 4 (televendas.conversao) e,
enquanto ela não existir, vem como null — a tela diz isso, em vez de inventar.
"""

from datetime import date

from televendas_config import Invalido, _int, _nomes, _txt

LISTAS_ALVO = ("CARTEIRA", "INTERNA", "AMBAS")

SQL_USUARIOS = "SELECT CODUSU, NOMEUSU FROM TSIUSU WHERE CODUSU IN ({ids})"
SQL_PRODUTOS = "SELECT CODPROD, DESCRPROD, CODVOL FROM TGFPRO WHERE CODPROD IN ({ids})"


def _competencia(v):
    v = str(v or "")
    if len(v) != 6 or not v.isdigit() or not 1 <= int(v[4:]) <= 12:
        raise Invalido("competencia é AAAAMM (ex.: 202610).")
    return v


def _data(dados, campo):
    try:
        return date.fromisoformat(str(dados.get(campo)))
    except (TypeError, ValueError):
        raise Invalido(f"'{campo}' é uma data AAAA-MM-DD.")


# ---------------------------------------------------------------------------
# Metas
# ---------------------------------------------------------------------------


def listar_metas(cur_pg, cur_ora, competencia):
    """Uma linha por usuário com perfil ativo no televendas, com a meta do mês (ou vazia)."""
    competencia = _competencia(competencia)
    cur_ora.execute("SELECT CODUSU, PERFIL FROM AD_PERFILTVL WHERE NVL(ATIVO, 'N') = 'S' ORDER BY CODUSU")
    perfis = {int(r[0]): _txt(r[1]) for r in cur_ora.fetchall()}
    nomes = _nomes(cur_ora, SQL_USUARIOS, list(perfis))
    cur_pg.execute(
        "SELECT codusu, ligacoes_dia, valor_venda, positivacao FROM meta WHERE competencia = %s", (competencia,)
    )
    metas = {r[0]: r[1:] for r in cur_pg.fetchall()}
    out = []
    for cod, perfil in perfis.items():
        lig, valor, pos = metas.get(cod, (None, None, None))
        out.append(
            {
                "codUsu": cod,
                "nomeUsu": _txt(nomes.get(cod, (None,))[0]),
                "perfil": perfil,
                "ligacoesDia": lig,
                "valorVenda": float(valor) if valor is not None else None,
                "positivacao": pos,
            }
        )
    return sorted(out, key=lambda m: m["nomeUsu"] or "")


def salvar_meta(cur_pg, dados):
    cod = _int(dados, "codUsu")
    comp = _competencia(dados.get("competencia"))
    vals = []
    for campo, minimo in (("ligacoesDia", 0), ("valorVenda", 0), ("positivacao", 0)):
        v = dados.get(campo)
        if v in (None, ""):
            vals.append(None)
            continue
        try:
            n = float(v) if campo == "valorVenda" else int(v)
        except (TypeError, ValueError):
            raise Invalido(f"'{campo}' precisa ser número.")
        if n < minimo:
            raise Invalido(f"'{campo}' não pode ser negativo.")
        vals.append(n)
    cur_pg.execute(
        """INSERT INTO meta (codusu, competencia, ligacoes_dia, valor_venda, positivacao)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (codusu, competencia) DO UPDATE
              SET ligacoes_dia = EXCLUDED.ligacoes_dia, valor_venda = EXCLUDED.valor_venda,
                  positivacao = EXCLUDED.positivacao""",
        (cod, comp, *vals),
    )


def meu_dia(cur_pg, codusu):
    """Hoje e o mês corrente do operador (datas no fuso de Maceió: a sessão do pg.py)."""
    cur_pg.execute(
        """SELECT
             count(*) FILTER (WHERE inicio::date = current_date),
             count(*) FILTER (WHERE inicio::date = current_date AND resultado = 'ATENDEU'),
             count(*) FILTER (WHERE inicio::date = current_date AND desfecho = 'VENDA'),
             count(*) FILTER (WHERE inicio::date = current_date AND desfecho = 'ORCAMENTO'),
             count(*),
             count(*) FILTER (WHERE resultado = 'ATENDEU'),
             count(*) FILTER (WHERE desfecho = 'VENDA'),
             count(*) FILTER (WHERE desfecho = 'ORCAMENTO'),
             count(DISTINCT codparc) FILTER (WHERE desfecho = 'VENDA'),
             count(DISTINCT inicio::date)
           FROM chamada
          WHERE codusu = %s AND situacao = 'FINALIZADA'
            AND inicio >= date_trunc('month', now())""",
        (codusu,),
    )
    r = cur_pg.fetchone()
    cur_pg.execute("SELECT to_char(now(), 'YYYYMM'), current_date")
    comp, hoje = cur_pg.fetchone()
    cur_pg.execute(
        "SELECT ligacoes_dia, valor_venda, positivacao FROM meta WHERE codusu = %s AND competencia = %s",
        (codusu, comp),
    )
    m = cur_pg.fetchone() or (None, None, None)
    cur_pg.execute(
        """SELECT c.id, c.codparc, c.inicio, c.resultado, c.desfecho, c.lista
             FROM chamada c
            WHERE c.codusu = %s AND c.situacao = 'FINALIZADA' AND c.inicio::date = current_date
            ORDER BY c.inicio DESC""",
        (codusu,),
    )
    ligacoes = [
        {"id": x[0], "codParc": x[1], "inicio": x[2].isoformat(), "resultado": x[3], "desfecho": x[4], "lista": x[5]}
        for x in cur_pg.fetchall()
    ]
    return {
        "hoje": hoje.isoformat(),
        "competencia": comp,
        "dia": {"ligacoes": r[0], "atendidas": r[1], "vendas": r[2], "orcamentos": r[3]},
        "mes": {
            "ligacoes": r[4],
            "atendidas": r[5],
            "vendas": r[6],
            "orcamentos": r[7],
            "clientesComVenda": r[8],
            "diasTrabalhados": r[9],
            "valorVendido": None,  # Fase 4 (televendas.conversao)
        },
        "meta": {
            "ligacoesDia": m[0],
            "valorVenda": float(m[1]) if m[1] is not None else None,
            "positivacao": m[2],
        },
        "ligacoesHoje": ligacoes,
    }


# ---------------------------------------------------------------------------
# Campanhas
# ---------------------------------------------------------------------------


def listar_campanhas(cur_pg, cur_ora, so_vigentes=False, lista=None):
    filtro, vals = [], []
    if so_vigentes:
        filtro.append("ativo AND current_date BETWEEN inicio AND fim")
    if lista:
        filtro.append("lista IN (%s, 'AMBAS')")
        vals.append(lista)
    cur_pg.execute(
        "SELECT id, titulo, texto, inicio, fim, lista, ativo FROM campanha"
        + (" WHERE " + " AND ".join(filtro) if filtro else "")
        + " ORDER BY ativo DESC, fim DESC, id DESC",
        vals,
    )
    camps = cur_pg.fetchall()
    ids = [c[0] for c in camps]
    prods = {}
    if ids:
        cur_pg.execute("SELECT campanha_id, codprod FROM campanha_produto WHERE campanha_id = ANY(%s)", (ids,))
        for cid, cp in cur_pg.fetchall():
            prods.setdefault(cid, []).append(cp)
    nomes = _nomes(cur_ora, SQL_PRODUTOS, [p for ps in prods.values() for p in ps])
    return [
        {
            "id": c[0],
            "titulo": c[1],
            "texto": c[2],
            "inicio": c[3].isoformat(),
            "fim": c[4].isoformat(),
            "lista": c[5],
            "ativo": c[6],
            "produtos": [
                {"codProd": p, "descricao": _txt(nomes.get(p, (None,))[0]), "unidade": _txt(nomes.get(p, (None, None))[1])}
                for p in prods.get(c[0], [])
            ],
        }
        for c in camps
    ]


def salvar_campanha(cur_pg, cur_ora, dados, id_=None):
    titulo = (dados.get("titulo") or "").strip()
    if not titulo or len(titulo) > 120:
        raise Invalido("Dê um título à campanha (até 120 caracteres).")
    inicio, fim = _data(dados, "inicio"), _data(dados, "fim")
    if fim < inicio:
        raise Invalido("O fim da campanha é antes do início.")
    lista = dados.get("lista") or "AMBAS"
    if lista not in LISTAS_ALVO:
        raise Invalido("lista é CARTEIRA, INTERNA ou AMBAS.")
    produtos = sorted({int(p) for p in (dados.get("produtos") or [])})
    if len(produtos) > 50:
        raise Invalido("No máximo 50 produtos por campanha.")
    if produtos:
        achados = _nomes(cur_ora, SQL_PRODUTOS, produtos)
        faltam = [p for p in produtos if p not in achados]
        if faltam:
            raise Invalido(f"Produto(s) não encontrado(s) no Sankhya: {', '.join(map(str, faltam))}.")
    texto = (dados.get("texto") or "").strip() or None
    ativo = bool(dados.get("ativo", True))
    if id_ is None:
        cur_pg.execute(
            "INSERT INTO campanha (titulo, texto, inicio, fim, lista, ativo) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (titulo, texto, inicio, fim, lista, ativo),
        )
        id_ = cur_pg.fetchone()[0]
    else:
        cur_pg.execute(
            "UPDATE campanha SET titulo = %s, texto = %s, inicio = %s, fim = %s, lista = %s, ativo = %s WHERE id = %s",
            (titulo, texto, inicio, fim, lista, ativo, id_),
        )
        if not cur_pg.rowcount:
            raise Invalido(f"Campanha {id_} não existe.")
        cur_pg.execute("DELETE FROM campanha_produto WHERE campanha_id = %s", (id_,))
    for p in produtos:
        cur_pg.execute("INSERT INTO campanha_produto (campanha_id, codprod) VALUES (%s, %s)", (id_, p))
    return id_


def buscar_produtos(cur_ora, termo):
    termo = (termo or "").strip()
    if len(termo) < 2:
        return []
    if termo.isdigit():
        cur_ora.execute(SQL_PRODUTOS.format(ids=":I0"), {"I0": int(termo)})
    else:
        cur_ora.execute(
            """SELECT * FROM (SELECT CODPROD, DESCRPROD, CODVOL FROM TGFPRO
                WHERE ATIVO = 'S' AND UPPER(DESCRPROD) LIKE UPPER(:Q) ORDER BY DESCRPROD) WHERE ROWNUM <= 20""",
            {"Q": f"%{termo}%"},
        )
    return [{"codProd": int(r[0]), "descricao": _txt(r[1]), "unidade": _txt(r[2])} for r in cur_ora.fetchall()]


# ---------------------------------------------------------------------------
# Roteiros
# ---------------------------------------------------------------------------


def listar_roteiros(cur_pg, so_ativos=False, lista=None):
    filtro, vals = [], []
    if so_ativos:
        filtro.append("r.ativo")
    if lista:
        filtro.append("r.lista IN (%s, 'AMBAS')")
        vals.append(lista)
    cur_pg.execute(
        """SELECT r.id, r.titulo, r.texto, r.lista, r.campanha_id, c.titulo, r.ativo
             FROM roteiro r LEFT JOIN campanha c ON c.id = r.campanha_id"""
        + (" WHERE " + " AND ".join(filtro) if filtro else "")
        + " ORDER BY r.ativo DESC, r.id",
        vals,
    )
    return [
        {"id": r[0], "titulo": r[1], "texto": r[2], "lista": r[3], "campanhaId": r[4], "campanha": r[5], "ativo": r[6]}
        for r in cur_pg.fetchall()
    ]


def salvar_roteiro(cur_pg, dados, id_=None):
    titulo = (dados.get("titulo") or "").strip()
    texto = (dados.get("texto") or "").strip()
    if not titulo or len(titulo) > 120:
        raise Invalido("Dê um título ao roteiro (até 120 caracteres).")
    if not texto:
        raise Invalido("Escreva o texto do roteiro.")
    lista = dados.get("lista") or "AMBAS"
    if lista not in LISTAS_ALVO:
        raise Invalido("lista é CARTEIRA, INTERNA ou AMBAS.")
    campanha_id = dados.get("campanhaId") or None
    if campanha_id:
        cur_pg.execute("SELECT 1 FROM campanha WHERE id = %s", (campanha_id,))
        if not cur_pg.fetchone():
            raise Invalido("Campanha inválida.")
    ativo = bool(dados.get("ativo", True))
    if id_ is None:
        cur_pg.execute(
            "INSERT INTO roteiro (titulo, texto, lista, campanha_id, ativo) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (titulo, texto, lista, campanha_id, ativo),
        )
        return cur_pg.fetchone()[0]
    cur_pg.execute(
        "UPDATE roteiro SET titulo = %s, texto = %s, lista = %s, campanha_id = %s, ativo = %s WHERE id = %s",
        (titulo, texto, lista, campanha_id, ativo, id_),
    )
    if not cur_pg.rowcount:
        raise Invalido(f"Roteiro {id_} não existe.")
    return id_
