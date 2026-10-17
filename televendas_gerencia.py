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

LISTAS = ("CARTEIRA", "INTERNA", "RECEBIDA")

# Ligação FEITA x RECEBIDA (plano das recebidas, §2): "ligações" e "atendidas"
# (e a meta de ligações/dia) contam só as feitas; recebidas têm coluna própria;
# vendas, orçamentos e motivos somam as duas.
FEITA = "c.lista <> 'RECEBIDA'"
RECEBIDA = "c.lista = 'RECEBIDA'"
MAX_DIAS = 366

SQL_USUARIOS = "SELECT CODUSU, NOMEUSU FROM TSIUSU WHERE CODUSU IN ({ids})"
SQL_PARCEIROS = "SELECT CODPARC, NOMEPARC, RAZAOSOCIAL FROM TGFPAR WHERE CODPARC IN ({ids})"
# Vendedor de cada usuário (a mesma regra principal da sessão: MIN(CODVEND) ativo).
SQL_VEND_USUARIOS = """SELECT T.CODUSU, MIN(T.CODVEND) FROM TGFVEN T
                        WHERE T.ATIVO = 'S' AND T.CODUSU IN ({ids}) GROUP BY T.CODUSU"""


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
                  count(*) FILTER (WHERE situacao = 'FINALIZADA' AND lista <> 'RECEBIDA'),
                  count(*) FILTER (WHERE situacao = 'FINALIZADA' AND lista <> 'RECEBIDA' AND resultado = 'ATENDEU'),
                  count(*) FILTER (WHERE situacao = 'FINALIZADA' AND desfecho = 'VENDA'),
                  count(*) FILTER (WHERE situacao = 'FINALIZADA' AND desfecho = 'ORCAMENTO'),
                  max(fim) FILTER (WHERE situacao = 'FINALIZADA'),
                  count(*) FILTER (WHERE situacao = 'FINALIZADA' AND lista = 'RECEBIDA')
             FROM chamada
            WHERE inicio::date = current_date
            GROUP BY codusu"""
    )
    hoje = {r[0]: r[1:] for r in cur_pg.fetchall()}

    cur_pg.execute(
        """SELECT DISTINCT ON (c.codusu) c.codusu, c.codparc, c.inicio, c.lista, k.nome
             FROM chamada c
             LEFT JOIN contato k ON k.id = c.contato_id
            WHERE c.situacao = 'ABERTA' AND c.expira > now()
            ORDER BY c.codusu, c.inicio DESC"""
    )
    abertas = {r[0]: r[1:] for r in cur_pg.fetchall()}

    cur_ora.execute("SELECT CODUSU, PERFIL FROM AD_PERFILTVL WHERE NVL(ATIVO, 'N') = 'S'")
    perfis = {int(r[0]): _txt(r[1]) for r in cur_ora.fetchall()}

    codigos = set(perfis) | set(hoje) | set(abertas)
    nomes = _nomes(cur_ora, SQL_USUARIOS, codigos)
    parc = _nomes(cur_ora, SQL_PARCEIROS, [a[0] for a in abertas.values() if a[0]])

    cur_pg.execute("SELECT now()")
    agora = cur_pg.fetchone()[0]

    out = []
    for cod in codigos:
        nome_pg, lig, atend, vendas, orc, ultimo, receb = hoje.get(cod, (None, 0, 0, 0, 0, None, 0))
        aberta = abertas.get(cod)
        em_chamada = None
        if aberta:
            fantasia, razao = parc.get(aberta[0], (None, None)) if aberta[0] else (None, None)
            em_chamada = {
                "codParc": aberta[0],
                # Contato sem cadastro: o nome vem do Postgres.
                "fantasia": _txt(fantasia) or _txt(razao) or _txt(aberta[3]),
                "desde": _iso(aberta[1]),
                "lista": aberta[2],
            }
        out.append(
            {
                "codUsu": cod,
                "nomeUsu": _txt(nomes.get(cod, (None,))[0]) or nome_pg or f"Usuário {cod}",
                "perfil": perfis.get(cod),
                "emChamada": em_chamada,
                "hoje": {"ligacoes": lig, "atendidas": atend, "vendas": vendas, "orcamentos": orc, "recebidas": receb},
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
        raise Invalido("'lista' é CARTEIRA, INTERNA ou RECEBIDA.")
    return de, ate, codusu, lista


def vendedores_dos_usuarios(cur_ora, codusus):
    """{codusu: codvend} — para saber se a recebida era de outra carteira."""
    return {cod: int(v[0]) for cod, v in _nomes(cur_ora, SQL_VEND_USUARIOS, codusus).items() if v[0] is not None}


def indicadores(cur_pg, de, ate, codusu=None, lista=None, cur_ora=None):
    """Produtividade, resultados e motivos de não compra das ligações FINALIZADAS do período.

    "ligacoes" e "atendidas" são as FEITAS; "recebidas" vem à parte. Com cur_ora,
    cada operador ganha "recebidasOutraCarteira" (cliente de outro televendas).
    """
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
                   count(*) FILTER (WHERE {FEITA}),
                   count(*) FILTER (WHERE {FEITA} AND c.resultado = 'ATENDEU'),
                   count(*) FILTER (WHERE c.desfecho = 'VENDA'),
                   count(*) FILTER (WHERE c.desfecho = 'ORCAMENTO'),
                   count(DISTINCT c.codparc),
                   count(DISTINCT c.inicio::date),
                   avg(extract(epoch FROM c.fim - c.inicio)) FILTER (WHERE {FEITA} AND c.resultado = 'ATENDEU'),
                   count(*) FILTER (WHERE c.desfecho = 'SEM_COMPRA' AND c.motivo_id IS NULL),
                   count(*) FILTER (WHERE {RECEBIDA}),
                   avg(extract(epoch FROM c.fim - c.inicio)) FILTER (WHERE {RECEBIDA})
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
            "recebidas": r[10],
            "duracaoMediaRecebidaSeg": _seg(r[11]),
            "recebidasOutraCarteira": None,
        }
        for r in cur_pg.fetchall()
    ]
    por_op.sort(key=lambda o: (-(o["ligacoes"] + o["recebidas"]), o["nomeUsu"] or ""))

    # Recebida de "outra carteira": o dono da carteira gravado na ligação (codvend)
    # não é o vendedor do próprio operador.
    if cur_ora is not None and por_op:
        cur_pg.execute(
            f"""SELECT c.codusu, c.codvend, count(*) FROM chamada c
                 WHERE {w} AND {RECEBIDA} AND c.codvend IS NOT NULL
                 GROUP BY 1, 2""",
            vals,
        )
        linhas = cur_pg.fetchall()
        proprio = vendedores_dos_usuarios(cur_ora, [o["codUsu"] for o in por_op])
        outra = {}
        for cod, vend, n in linhas:
            if vend != proprio.get(cod):
                outra[cod] = outra.get(cod, 0) + n
        for o in por_op:
            o["recebidasOutraCarteira"] = outra.get(o["codUsu"], 0)

    cur_pg.execute(
        f"""SELECT count(*) FILTER (WHERE {FEITA}),
                   count(*) FILTER (WHERE {FEITA} AND c.resultado = 'ATENDEU'),
                   count(*) FILTER (WHERE c.desfecho = 'VENDA'),
                   count(*) FILTER (WHERE c.desfecho = 'ORCAMENTO'),
                   count(DISTINCT c.codparc),
                   avg(extract(epoch FROM c.fim - c.inicio)) FILTER (WHERE {FEITA} AND c.resultado = 'ATENDEU'),
                   count(*) FILTER (WHERE c.desfecho = 'SEM_COMPRA'),
                   count(*) FILTER (WHERE c.desfecho = 'SEM_COMPRA' AND c.motivo_id IS NULL),
                   count(*) FILTER (WHERE {RECEBIDA}),
                   count(*) FILTER (WHERE {RECEBIDA} AND c.codparc IS NULL)
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
        "recebidas": t[8],
        "recebidasSemCadastro": t[9],
    }

    cur_pg.execute(
        f"""SELECT c.inicio::date, count(*) FILTER (WHERE {FEITA}),
                   count(*) FILTER (WHERE {FEITA} AND c.resultado = 'ATENDEU'),
                   count(*) FILTER (WHERE {RECEBIDA})
              FROM chamada c
             WHERE {w}
             GROUP BY 1 ORDER BY 1""",
        vals,
    )
    por_dia = [{"dia": _iso(r[0]), "ligacoes": r[1], "atendidas": r[2], "recebidas": r[3]} for r in cur_pg.fetchall()]

    # Resultados só das feitas: na recebida é sempre "atendeu" e inflaria a taxa.
    cur_pg.execute(
        f"SELECT c.resultado, count(*) FROM chamada c WHERE {w} AND {FEITA} GROUP BY 1 ORDER BY 2 DESC",
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

    cur_pg.execute(
        f"""SELECT coalesce(s.descricao, 'sem assunto'), count(*)
              FROM chamada c LEFT JOIN assunto s ON s.id = c.assunto_id
             WHERE {w} AND {RECEBIDA}
             GROUP BY 1 ORDER BY 2 DESC, 1""",
        vals,
    )
    assuntos = [{"assunto": r[0], "n": r[1]} for r in cur_pg.fetchall()]

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
        "assuntos": assuntos,
    }
