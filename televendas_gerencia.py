"""Gerência do televendas (Fase 4, primeira parte): Ao vivo e indicadores.

Tudo sai do registro de ligações no Postgres. Do Oracle vêm só nomes (operadores
com perfil e clientes em ligação agora). Vendas e orçamentos aqui são os
DESFECHOS marcados pelo operador; o valor em R$ e a atribuição automática de
notas (plano §6.3) ficam para a próxima etapa, e a tela diz isso.

Datas no fuso de Maceió: a sessão do pg.py abre com timezone=America/Maceio,
então `inicio::date` é o dia local.
"""

from datetime import date

from televendas_config import Invalido, _nomes, _txt

LISTAS = ("CARTEIRA", "INTERNA")
MAX_DIAS = 366

SQL_USUARIOS = "SELECT CODUSU, NOMEUSU FROM TSIUSU WHERE CODUSU IN ({ids})"
SQL_PARCEIROS = "SELECT CODPARC, NOMEPARC, RAZAOSOCIAL FROM TGFPAR WHERE CODPARC IN ({ids})"


def _iso(v):
    return v.isoformat() if v is not None else None


def _seg(v):
    return int(round(v)) if v is not None else None


# ---------------------------------------------------------------------------
# Ao vivo
# ---------------------------------------------------------------------------


def ao_vivo(cur_pg, cur_ora):
    """Uma linha por operador: ligação aberta agora, números de hoje e o último registro.

    Entra quem tem perfil ativo e também quem ligou hoje sem perfil ativo (perfil
    tirado no meio do dia): os números de hoje não somem da tela.
    """
    cur_pg.execute(
        """SELECT codusu, max(nome_usu),
                  count(*) FILTER (WHERE situacao = 'FINALIZADA'),
                  count(*) FILTER (WHERE situacao = 'FINALIZADA' AND resultado = 'ATENDEU'),
                  count(*) FILTER (WHERE situacao = 'FINALIZADA' AND desfecho = 'VENDA'),
                  count(*) FILTER (WHERE situacao = 'FINALIZADA' AND desfecho = 'ORCAMENTO'),
                  max(fim) FILTER (WHERE situacao = 'FINALIZADA')
             FROM chamada
            WHERE inicio::date = current_date
            GROUP BY codusu"""
    )
    hoje = {r[0]: r[1:] for r in cur_pg.fetchall()}

    cur_pg.execute(
        """SELECT DISTINCT ON (codusu) codusu, codparc, inicio, lista
             FROM chamada
            WHERE situacao = 'ABERTA' AND expira > now()
            ORDER BY codusu, inicio DESC"""
    )
    abertas = {r[0]: r[1:] for r in cur_pg.fetchall()}

    cur_ora.execute("SELECT CODUSU, PERFIL FROM AD_PERFILTVL WHERE NVL(ATIVO, 'N') = 'S'")
    perfis = {int(r[0]): _txt(r[1]) for r in cur_ora.fetchall()}

    codigos = set(perfis) | set(hoje) | set(abertas)
    nomes = _nomes(cur_ora, SQL_USUARIOS, codigos)
    parc = _nomes(cur_ora, SQL_PARCEIROS, [a[0] for a in abertas.values()])

    cur_pg.execute("SELECT now()")
    agora = cur_pg.fetchone()[0]

    out = []
    for cod in codigos:
        nome_pg, lig, atend, vendas, orc, ultimo = hoje.get(cod, (None, 0, 0, 0, 0, None))
        aberta = abertas.get(cod)
        em_chamada = None
        if aberta:
            fantasia, razao = parc.get(aberta[0], (None, None))
            em_chamada = {
                "codParc": aberta[0],
                "fantasia": _txt(fantasia) or _txt(razao),
                "desde": _iso(aberta[1]),
                "lista": aberta[2],
            }
        out.append(
            {
                "codUsu": cod,
                "nomeUsu": _txt(nomes.get(cod, (None,))[0]) or nome_pg or f"Usuário {cod}",
                "perfil": perfis.get(cod),
                "emChamada": em_chamada,
                "hoje": {"ligacoes": lig, "atendidas": atend, "vendas": vendas, "orcamentos": orc},
                "ultimoRegistro": _iso(ultimo),
            }
        )
    out.sort(key=lambda o: o["nomeUsu"].lower())
    return {"agora": _iso(agora), "operadores": out}


# ---------------------------------------------------------------------------
# Indicadores (período e operador filtráveis)
# ---------------------------------------------------------------------------


def filtros(args):
    """Valida ?de=&ate=&codUsu=&lista= e devolve (de, ate, codusu, lista)."""
    try:
        de = date.fromisoformat(args.get("de") or "")
        ate = date.fromisoformat(args.get("ate") or "")
    except ValueError:
        raise Invalido("'de' e 'ate' são datas AAAA-MM-DD.")
    if de > ate:
        raise Invalido("'de' precisa ser antes de 'ate'.")
    if (ate - de).days >= MAX_DIAS:
        raise Invalido(f"Período de no máximo {MAX_DIAS} dias.")
    codusu = args.get("codUsu") or None
    if codusu is not None:
        try:
            codusu = int(codusu)
        except ValueError:
            raise Invalido("'codUsu' precisa ser número.")
    lista = args.get("lista") or None
    if lista is not None and lista not in LISTAS:
        raise Invalido("'lista' é CARTEIRA ou INTERNA.")
    return de, ate, codusu, lista


def indicadores(cur_pg, de, ate, codusu=None, lista=None):
    """Produtividade, resultados e motivos de não compra das ligações FINALIZADAS do período."""
    where = ["c.situacao = 'FINALIZADA'", "c.inicio::date BETWEEN %s AND %s"]
    vals = [de, ate]
    if codusu is not None:
        where.append("c.codusu = %s")
        vals.append(codusu)
    if lista is not None:
        where.append("c.lista = %s")
        vals.append(lista)
    w = " AND ".join(where)

    # Duração = do "Iniciar ligação" ao "Salvar": inclui o tempo de preencher o registro.
    cur_pg.execute(
        f"""SELECT c.codusu, max(c.nome_usu),
                   count(*),
                   count(*) FILTER (WHERE c.resultado = 'ATENDEU'),
                   count(*) FILTER (WHERE c.desfecho = 'VENDA'),
                   count(*) FILTER (WHERE c.desfecho = 'ORCAMENTO'),
                   count(DISTINCT c.codparc),
                   count(DISTINCT c.inicio::date),
                   avg(extract(epoch FROM c.fim - c.inicio)) FILTER (WHERE c.resultado = 'ATENDEU'),
                   count(*) FILTER (WHERE c.desfecho = 'SEM_COMPRA' AND c.motivo_id IS NULL)
              FROM chamada c
             WHERE {w}
             GROUP BY c.codusu""",
        vals,
    )
    por_op = [
        {
            "codUsu": r[0],
            "nomeUsu": r[1],
            "ligacoes": r[2],
            "atendidas": r[3],
            "vendas": r[4],
            "orcamentos": r[5],
            "clientes": r[6],
            "diasTrabalhados": r[7],
            "duracaoMediaSeg": _seg(r[8]),
            "semCompraSemMotivo": r[9],
        }
        for r in cur_pg.fetchall()
    ]
    por_op.sort(key=lambda o: (-o["ligacoes"], o["nomeUsu"] or ""))

    cur_pg.execute(
        f"""SELECT count(*),
                   count(*) FILTER (WHERE c.resultado = 'ATENDEU'),
                   count(*) FILTER (WHERE c.desfecho = 'VENDA'),
                   count(*) FILTER (WHERE c.desfecho = 'ORCAMENTO'),
                   count(DISTINCT c.codparc),
                   avg(extract(epoch FROM c.fim - c.inicio)) FILTER (WHERE c.resultado = 'ATENDEU'),
                   count(*) FILTER (WHERE c.desfecho = 'SEM_COMPRA'),
                   count(*) FILTER (WHERE c.desfecho = 'SEM_COMPRA' AND c.motivo_id IS NULL)
              FROM chamada c
             WHERE {w}""",
        vals,
    )
    t = cur_pg.fetchone()
    totais = {
        "ligacoes": t[0],
        "atendidas": t[1],
        "vendas": t[2],
        "orcamentos": t[3],
        "clientes": t[4],
        "duracaoMediaSeg": _seg(t[5]),
        "semCompra": t[6],
        "semCompraSemMotivo": t[7],
    }

    cur_pg.execute(
        f"""SELECT c.inicio::date, count(*), count(*) FILTER (WHERE c.resultado = 'ATENDEU')
              FROM chamada c
             WHERE {w}
             GROUP BY 1 ORDER BY 1""",
        vals,
    )
    por_dia = [{"dia": _iso(r[0]), "ligacoes": r[1], "atendidas": r[2]} for r in cur_pg.fetchall()]

    cur_pg.execute(
        f"SELECT c.resultado, count(*) FROM chamada c WHERE {w} GROUP BY 1 ORDER BY 2 DESC",
        vals,
    )
    resultados = [{"resultado": r[0], "n": r[1]} for r in cur_pg.fetchall()]

    cur_pg.execute(
        f"""SELECT c.desfecho, count(*) FROM chamada c
             WHERE {w} AND c.desfecho IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""",
        vals,
    )
    desfechos = [{"desfecho": r[0], "n": r[1]} for r in cur_pg.fetchall()]

    # Motivo vale para qualquer ligação que o tenha (o formulário pede no "sem compra",
    # mas não proíbe nos outros desfechos); o ranking conta onde ele foi marcado.
    cur_pg.execute(
        f"""SELECT m.id, m.descricao, c.codusu, max(c.nome_usu), count(*)
              FROM chamada c JOIN motivo m ON m.id = c.motivo_id
             WHERE {w}
             GROUP BY m.id, m.descricao, c.codusu""",
        vals,
    )
    motivos = {}
    for mid, desc, cod, nome, n in cur_pg.fetchall():
        m = motivos.setdefault(mid, {"id": mid, "descricao": desc, "n": 0, "porOperador": []})
        m["n"] += n
        m["porOperador"].append({"codUsu": cod, "nomeUsu": nome, "n": n})
    for m in motivos.values():
        m["porOperador"].sort(key=lambda o: -o["n"])
    ranking = sorted(motivos.values(), key=lambda m: (-m["n"], m["descricao"]))

    return {
        "de": _iso(de),
        "ate": _iso(ate),
        "codUsu": codusu,
        "lista": lista,
        "totais": totais,
        "porDia": por_dia,
        "porOperador": por_op,
        "resultados": resultados,
        "desfechos": desfechos,
        "motivos": ranking,
    }
