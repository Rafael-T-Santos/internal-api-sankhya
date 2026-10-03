"""Registro de ligações do televendas (Fase 3): iniciar, renovar, finalizar,
cancelar, anexar; histórico do cliente, agenda de retornos e travas.

Tudo no Postgres (schema televendas). O Oracle só é consultado para LER: se o
cliente pode receber ligação agora (mesma regra das listas) e se o NUNOTA
vinculado existe e é do cliente.

Trava: um cliente está "em ligação" enquanto existir chamada ABERTA com
expira > agora. O /iniciar serializa por cliente com pg_advisory_xact_lock —
o equivalente do FOR UPDATE que, na cobrança, garantiu exatamente 1×201 + 1×409
numa corrida real. Ninguém precisa "soltar" a trava: ela expira sozinha, e o
app renova enquanto a tela está aberta.
"""

from datetime import datetime

import televendas_listas as listas

# Namespace do advisory lock (a 1ª chave): o banco é compartilhado com o
# check-my-load, e só a chave dupla garante não colidir com outro uso.
LOCK_NS = 7001

RESULTADOS = ("ATENDEU", "NAO_ATENDEU", "OCUPADO", "CAIXA_POSTAL", "NUMERO_ERRADO", "RETORNAR_DEPOIS")
DESFECHOS = ("VENDA", "ORCAMENTO", "SEM_COMPRA", "INFORMACAO", "RECLAMACAO", "RETORNO_AGENDADO")
TIPOS_NOTA = ("PEDIDO", "ORCAMENTO")
MAX_NOTAS = 10


class ErroChamada(Exception):
    status = 400

    def __init__(self, mensagem, corpo=None):
        super().__init__(mensagem)
        self.corpo = corpo or {}


class Invalido(ErroChamada):
    status = 400


class Proibido(ErroChamada):
    status = 403


class NaoEncontrado(ErroChamada):
    status = 404


class Conflito(ErroChamada):
    status = 409


# ---------------------------------------------------------------------------
# Elegibilidade (Oracle, só leitura) — a mesma regra das listas, para UM cliente
# ---------------------------------------------------------------------------

SQL_CLIENTE_CARTEIRA = """
    SELECT A.CODVEND, A.CODCID, A.CODBAI
      FROM TGFPAR A
      JOIN TGFVEN VEXT ON VEXT.CODVEND = A.CODVEND
     WHERE A.CODPARC = :CODPARC
       AND A.TIPPESSOA = 'J' AND A.ATIVO = 'S' AND A.CLIENTE = 'S'
       AND VEXT.AD_CODVEND = :CODTELEVEND
"""

SQL_CLIENTE_INTERNA = """
    SELECT PAR.CODPARC
      FROM TGFPAR PAR
      JOIN TSICID CID ON CID.CODCID = PAR.CODCID
      JOIN TSIUFS UFS ON UFS.CODUF = CID.UF
     WHERE PAR.CODPARC = :CODPARC
       AND PAR.TIPPESSOA = 'J' AND PAR.CLIENTE = 'S' AND PAR.ATIVO = 'S'
       AND UFS.UF = 'AL' AND NVL(PAR.CODVEND, 0) = 0
"""


def checar_elegivel(cur_ora, cur_pg, codparc, lista, codtelevend):
    """Devolve o representante externo (ou None). Levanta Proibido se não pode ligar.

    O atraso financeiro não é conferido aqui: ele tira o cliente da LISTA, mas
    a régua do televendas é a escala. (Plano §6.2.)
    """
    if lista == "CARTEIRA":
        cur_ora.execute(SQL_CLIENTE_CARTEIRA, {"CODPARC": codparc, "CODTELEVEND": codtelevend})
        r = cur_ora.fetchone()
        if not r:
            raise Proibido("Este cliente não é da carteira deste televendas.")
        cli = {"codVendExterno": int(r[0]), "codCid": r[1], "codBai": r[2]}
        cur_ora.execute(listas.SQL_HOJE)
        _, dia_semana = cur_ora.fetchone()
        escala = listas.ler_escala(cur_pg, {cli["codVendExterno"]})
        dia = listas.dia_da_escala(escala, cli)
        if not listas.liberado(dia, int(dia_semana)):
            quando = listas.LIBERA_EM.get(dia)
            raise Proibido(
                "Cliente ainda não liberado pela escala de visitas"
                + (f" (libera na {quando})." if quando else " (sem rota cadastrada).")
            )
        return cli["codVendExterno"]
    if lista == "INTERNA":
        cur_ora.execute(SQL_CLIENTE_INTERNA, {"CODPARC": codparc})
        if not cur_ora.fetchone():
            raise Proibido("Este cliente não está na fila interna.")
        return None
    raise Invalido("lista é CARTEIRA ou INTERNA.")


# ---------------------------------------------------------------------------
# Ciclo da ligação (Postgres)
# ---------------------------------------------------------------------------


def _iso(v):
    return v.isoformat() if v else None


def _trava(row):
    return {"codUsu": row[1], "nomeUsu": row[2], "desde": _iso(row[3]), "expiraEm": _iso(row[4])}


def trava_minutos(cur_pg):
    cur_pg.execute("SELECT valor FROM parametro WHERE chave = 'TRAVA_MINUTOS'")
    r = cur_pg.fetchone()
    return int(r[0]) if r else 20


def iniciar(cur_pg, codparc, lista, op, codvend, codvend_ext):
    """Abre a ligação e adquire a trava. Ligação já aberta pelo MESMO operador
    para o mesmo cliente é devolvida (retomar depois de um F5), não é conflito."""
    cur_pg.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_NS, codparc))
    cur_pg.execute(
        """SELECT id, codusu, nome_usu, inicio, expira FROM chamada
            WHERE codparc = %s AND situacao = 'ABERTA' AND expira > now()""",
        (codparc,),
    )
    aberta = cur_pg.fetchone()
    if aberta:
        if aberta[1] == op["codUsu"]:
            return {"id": aberta[0], "inicio": _iso(aberta[3]), "expiraEm": _iso(aberta[4]), "retomada": True}
        raise Conflito(f"{aberta[2]} já está ligando para este cliente.", {"emChamada": _trava(aberta)})
    minutos = trava_minutos(cur_pg)
    cur_pg.execute(
        """INSERT INTO chamada (codparc, lista, codusu, nome_usu, codvend, codvend_ext, expira)
           VALUES (%s, %s, %s, %s, %s, %s, now() + make_interval(mins => %s))
           RETURNING id, inicio, expira""",
        (codparc, lista, op["codUsu"], op["nomeUsu"], codvend, codvend_ext, minutos),
    )
    id_, inicio, expira = cur_pg.fetchone()
    return {"id": id_, "inicio": _iso(inicio), "expiraEm": _iso(expira), "retomada": False}


def _minha(cur_pg, id_, op):
    """A chamada (travada para atualização), conferindo que é do operador."""
    cur_pg.execute(
        "SELECT id, codparc, codusu, situacao, expira FROM chamada WHERE id = %s FOR UPDATE", (id_,)
    )
    r = cur_pg.fetchone()
    if not r:
        raise NaoEncontrado(f"Ligação {id_} não existe.")
    if r[2] != op["codUsu"]:
        raise Proibido("Esta ligação é de outro operador.")
    return {"id": r[0], "codparc": r[1], "situacao": r[3], "expira": r[4]}


def renovar(cur_pg, id_, op):
    """Heartbeat: estica a trava. Se ela expirou e outro operador abriu o
    cliente nesse meio-tempo, devolve 409 — o registro ainda pode ser salvo."""
    ch = _minha(cur_pg, id_, op)
    if ch["situacao"] != "ABERTA":
        raise Conflito("A ligação já foi encerrada.")
    cur_pg.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_NS, ch["codparc"]))
    cur_pg.execute(
        """SELECT id, codusu, nome_usu, inicio, expira FROM chamada
            WHERE codparc = %s AND situacao = 'ABERTA' AND expira > now() AND id <> %s""",
        (ch["codparc"], id_),
    )
    outra = cur_pg.fetchone()
    if outra:
        raise Conflito(
            f"A reserva expirou e {outra[2]} abriu este cliente. Você ainda pode salvar o registro.",
            {"emChamada": _trava(outra)},
        )
    cur_pg.execute(
        "UPDATE chamada SET expira = now() + make_interval(mins => %s) WHERE id = %s RETURNING expira",
        (trava_minutos(cur_pg), id_),
    )
    return {"expiraEm": _iso(cur_pg.fetchone()[0])}


def cancelar(cur_pg, id_, op):
    """Descarta sem registrar. IDEMPOTENTE: o app chama de novo ao fechar a aba
    (sendBeacon), e a segunda chamada não pode virar erro."""
    ch = _minha(cur_pg, id_, op)
    if ch["situacao"] == "ABERTA":
        cur_pg.execute("UPDATE chamada SET situacao = 'CANCELADA', fim = now() WHERE id = %s", (id_,))
    return {"situacao": "CANCELADA" if ch["situacao"] == "ABERTA" else ch["situacao"]}


def _texto(dados, campo, limite):
    v = dados.get(campo)
    if v is None:
        return None
    v = str(v).strip()
    if len(v) > limite:
        raise Invalido(f"'{campo}' passa de {limite} caracteres.")
    return v or None


def _data_hora(dados, campo):
    v = dados.get(campo)
    if not v:
        return None
    try:
        # "2026-10-09T10:00" (sem fuso) = horário de Maceió; a sessão do Postgres já está nesse fuso.
        datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise Invalido(f"'{campo}' não é uma data/hora válida.")
    return str(v)


SQL_NOTAS = "SELECT NUNOTA, CODPARC FROM TGFCAB WHERE NUNOTA IN ({ids})"


def _validar_notas(cur_ora, codparc, notas):
    if not notas:
        return []
    if not isinstance(notas, list) or len(notas) > MAX_NOTAS:
        raise Invalido(f"notas é uma lista de até {MAX_NOTAS} itens.")
    limpas = []
    for n in notas:
        try:
            nunota = int(n.get("nunota"))
        except (TypeError, ValueError, AttributeError):
            raise Invalido("Cada nota precisa de um 'nunota' numérico.")
        tipo = n.get("tipo")
        if tipo not in TIPOS_NOTA:
            raise Invalido("O tipo da nota é PEDIDO ou ORCAMENTO.")
        limpas.append((nunota, tipo))
    nomes = [f"N{i}" for i in range(len(limpas))]
    cur_ora.execute(SQL_NOTAS.format(ids=", ".join(f":{x}" for x in nomes)), dict(zip(nomes, [n for n, _ in limpas])))
    dono = {int(r[0]): int(r[1]) for r in cur_ora.fetchall()}
    for nunota, _ in limpas:
        if nunota not in dono:
            raise Invalido(f"A nota (NUNOTA) {nunota} não existe no Sankhya.")
        if dono[nunota] != codparc:
            raise Invalido(f"A nota {nunota} é de outro cliente ({dono[nunota]}).")
    return limpas


def finalizar(cur_pg, cur_ora, id_, op, dados):
    """Grava o registro. Só o Resultado é obrigatório (plano §2). Trava expirada
    NÃO impede salvar: expirar libera o cliente para outro, não joga fora o que
    foi digitado."""
    ch = _minha(cur_pg, id_, op)
    if ch["situacao"] == "FINALIZADA":
        raise Conflito("Esta ligação já foi registrada.")
    if ch["situacao"] == "CANCELADA":
        raise Conflito("Esta ligação foi descartada; inicie outra para registrar.")

    resultado = dados.get("resultado")
    if resultado not in RESULTADOS:
        raise Invalido("Escolha o resultado da ligação.")
    desfecho = dados.get("desfecho") or None
    if desfecho and desfecho not in DESFECHOS:
        raise Invalido("Desfecho inválido.")
    if desfecho and resultado != "ATENDEU":
        raise Invalido("Desfecho só existe quando o cliente atendeu.")

    motivo_id = dados.get("motivoId") or None
    if motivo_id is not None:
        cur_pg.execute("SELECT 1 FROM motivo WHERE id = %s AND ativo", (motivo_id,))
        if not cur_pg.fetchone():
            raise Invalido("Motivo de não compra inválido.")

    notas = _validar_notas(cur_ora, ch["codparc"], dados.get("notas"))

    cur_pg.execute(
        """UPDATE chamada
              SET situacao = 'FINALIZADA', fim = now(), resultado = %s, desfecho = %s, motivo_id = %s,
                  retorno_em = %s::timestamptz, telefone = %s, contato = %s, obs = %s
            WHERE id = %s
        RETURNING inicio, fim""",
        (
            resultado,
            desfecho,
            motivo_id,
            _data_hora(dados, "retornoEm"),
            _texto(dados, "telefone", 40),
            _texto(dados, "contato", 100),
            _texto(dados, "obs", 4000),
            id_,
        ),
    )
    inicio, fim = cur_pg.fetchone()
    for nunota, tipo in notas:
        cur_pg.execute(
            "INSERT INTO chamada_nota (chamada_id, nunota, tipo) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (id_, nunota, tipo),
        )
    return {"id": id_, "codParc": ch["codparc"], "inicio": _iso(inicio), "fim": _iso(fim)}


def gravar_anexo(cur_pg, id_, op, descricao, url):
    ch = _minha(cur_pg, id_, op)
    if ch["situacao"] == "CANCELADA":
        raise Conflito("A ligação foi descartada.")
    cur_pg.execute(
        "INSERT INTO anexo (chamada_id, descricao, url, codusu) VALUES (%s, %s, %s, %s) RETURNING id",
        (id_, descricao, url, op["codUsu"]),
    )
    return {"id": cur_pg.fetchone()[0], "url": url}


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------


def historico(cur_pg, codparc, limite=100):
    """Ligações REGISTRADAS do cliente, da mais nova para a mais antiga, com notas e anexos."""
    cur_pg.execute(
        """SELECT c.id, c.lista, c.codusu, c.nome_usu, c.inicio, c.fim, c.resultado, c.desfecho,
                  m.descricao, c.retorno_em, c.telefone, c.contato, c.obs,
                  COALESCE((SELECT json_agg(json_build_object('nunota', n.nunota, 'tipo', n.tipo) ORDER BY n.id)
                              FROM chamada_nota n WHERE n.chamada_id = c.id), '[]'),
                  COALESCE((SELECT json_agg(json_build_object('descricao', a.descricao, 'url', a.url) ORDER BY a.id)
                              FROM anexo a WHERE a.chamada_id = c.id), '[]')
             FROM chamada c
             LEFT JOIN motivo m ON m.id = c.motivo_id
            WHERE c.codparc = %s AND c.situacao = 'FINALIZADA'
            ORDER BY c.inicio DESC
            LIMIT %s""",
        (codparc, limite),
    )
    return [
        {
            "id": r[0],
            "lista": r[1],
            "codUsu": r[2],
            "nomeUsu": r[3],
            "inicio": _iso(r[4]),
            "duracaoSeg": int((r[5] - r[4]).total_seconds()) if r[5] else None,
            "resultado": r[6],
            "desfecho": r[7],
            "motivo": r[8],
            "retornoEm": _iso(r[9]),
            "telefone": r[10],
            "contato": r[11],
            "obs": r[12],
            "notas": r[13],
            "anexos": r[14],
        }
        for r in cur_pg.fetchall()
    ]


def agenda(cur_pg, codusu, de, ate):
    """Retornos marcados pelo operador entre `de` e `ate` (datas, fuso de Maceió),
    só onde aquela ligação ainda é o ÚLTIMO contato registrado do cliente — se
    alguém já ligou depois, o retorno foi cumprido (ou superado)."""
    cur_pg.execute(
        """SELECT c.id, c.codparc, c.retorno_em, c.resultado, c.desfecho, c.obs, c.lista
             FROM chamada c
            WHERE c.codusu = %s AND c.situacao = 'FINALIZADA' AND c.retorno_em IS NOT NULL
              AND c.retorno_em::date BETWEEN %s AND %s
              AND NOT EXISTS (SELECT 1 FROM chamada d
                               WHERE d.codparc = c.codparc AND d.situacao = 'FINALIZADA' AND d.inicio > c.inicio)
            ORDER BY c.retorno_em""",
        (codusu, de, ate),
    )
    return [
        {
            "chamadaId": r[0],
            "codParc": r[1],
            "retornoEm": _iso(r[2]),
            "resultado": r[3],
            "desfecho": r[4],
            "obs": r[5],
            "lista": r[6],
        }
        for r in cur_pg.fetchall()
    ]


def travas(cur_pg):
    """Todas as ligações abertas agora (são poucas): o app consulta a cada 30 s."""
    cur_pg.execute(
        """SELECT codparc, codusu, nome_usu, inicio, expira FROM chamada
            WHERE situacao = 'ABERTA' AND expira > now() ORDER BY inicio"""
    )
    return [{"codParc": r[0], **_trava(r)} for r in cur_pg.fetchall()]


def motivos(cur_pg, so_ativos=True):
    cur_pg.execute(
        "SELECT id, descricao, ordem, ativo FROM motivo"
        + (" WHERE ativo" if so_ativos else "")
        + " ORDER BY ordem, descricao"
    )
    return [{"id": r[0], "descricao": r[1], "ordem": r[2], "ativo": r[3]} for r in cur_pg.fetchall()]
