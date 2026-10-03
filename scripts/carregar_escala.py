"""Carga inicial da escala de visitas (televendas.escala) a partir da consulta do admin.

A consulta da carteira enviada pelo administrador do Sankhya trazia a escala
escrita à mão (ESCALA_RAW, "transcrita das fotos"), casando cidade e bairro por
NOME normalizado. Aqui cada nome vira o(s) código(s) do Sankhya (TSICID/TSIBAI),
usando no Oracle EXATAMENTE a mesma normalização da consulta.

Fidelidade à consulta original:
  - local repetido em dois dias vale o MAIOR dia (o ESCALA da consulta faz MAX);
  - um nome que casa com mais de um código (ex.: cidade homônima em outro
    estado) entra com TODOS os códigos, porque a consulta original casava por
    nome e liberava todos. O relatório aponta esses casos para o gerente
    limpar na tela de configuração;
  - nome que não casa com nada é LISTADO, nunca descartado em silêncio.

Rodar DENTRO do container da API:

    docker compose exec api-sankhya python scripts/carregar_escala.py              # só mostra
    docker compose exec api-sankhya python scripts/carregar_escala.py --aplicar    # grava
"""

import os
import sys
from collections import defaultdict

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from db import conectar_oracle  # noqa: E402
from pg import conectar_postgres  # noqa: E402

# Mesma expressão da consulta do admin (CIDADE_NORM / BAIRRO_NORM).
_NORM = """REGEXP_REPLACE(
    TRANSLATE(UPPER(TRIM(NVL({col}, ''))),
              'ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ', 'AAAAAEEEEIIIIOOOOOUUUUC'),
    '[^A-Z0-9]', '')"""

SQL_CIDADES = f"""
    SELECT CID.CODCID, {_NORM.format(col='CID.NOMECID')}, CID.NOMECID, UFS.UF
      FROM TSICID CID
      LEFT JOIN TSIUFS UFS ON UFS.CODUF = CID.UF
"""
SQL_BAIRROS = f"SELECT BAI.CODBAI, {_NORM.format(col='BAI.NOMEBAI')}, BAI.NOMEBAI FROM TSIBAI BAI"

DIAS = {1: "seg", 2: "ter", 3: "qua", 4: "qui", 5: "sex"}

# (CODVEND do representante, DIA_VISITA, TIPO_LOCAL, LOCAL_NORM) — copiado da
# consulta do admin recebida em 03/10/2026 (televendas/docs/carteira_televendas_representantes.txt).
ESCALA_ADMIN = [
    (23, 1, "C", "MESSIAS"),
    (23, 1, "C", "FLEXEIRAS"),
    (23, 1, "C", "JOAQUIMGOMES"),
    (23, 1, "C", "NOVOLINO"),
    (23, 2, "C", "COLONIALEOPOLDINA"),
    (23, 2, "C", "CAMPESTRE"),
    (23, 2, "C", "JACUIPE"),
    (23, 2, "C", "JUNDIA"),
    (23, 3, "C", "IBATEGUARA"),
    (23, 3, "C", "SAOJOSEDALAJE"),
    (23, 3, "C", "UNIAODOSPALMARES"),
    (23, 4, "C", "BRANQUINHA"),
    (23, 4, "C", "MURICI"),
    (51, 1, "C", "COQUEIROSECO"),
    (51, 1, "C", "PARIPUEIRA"),
    (51, 1, "C", "BARRADESANTOANTONIO"),
    (51, 2, "C", "RIOLARGO"),
    (51, 3, "C", "SATUBA"),
    (51, 3, "C", "SANTALUZIADONORTE"),
    (51, 3, "C", "PILAR"),
    (51, 3, "C", "TANQUEDARCA"),
    (51, 3, "C", "BELEM"),
    (28, 1, "C", "ATALAIA"),
    (28, 1, "C", "CAPELA"),
    (28, 1, "C", "CAJUEIRO"),
    (28, 2, "C", "VICOSA"),
    (28, 2, "C", "CHAPRETA"),
    (28, 2, "C", "PAULOJACINTO"),
    (28, 3, "C", "QUEBRANGULO"),
    (28, 3, "C", "PALMEIRADOSINDIOS"),
    (28, 4, "C", "PALMEIRADOSINDIOS"),
    (21, 1, "C", "OLHODAGUADASFLORES"),
    (21, 1, "C", "CARNEIROS"),
    (21, 1, "C", "SENADORRUIPALMEIRA"),
    (21, 1, "C", "SAOJOSEDATAPERA"),
    (21, 2, "C", "PIRANHAS"),
    (21, 2, "C", "OLHODAGUADOCASADO"),
    (21, 2, "C", "DELMIROGOUVEIA"),
    (21, 2, "C", "PARICONHA"),
    (21, 2, "C", "AGUABRANCA"),
    (21, 2, "C", "INHAPI"),
    (21, 3, "C", "MATAGRANDE"),
    (21, 3, "C", "CANAPI"),
    (21, 3, "C", "OUROBRANCO"),
    (21, 3, "C", "MARAVILHA"),
    (21, 3, "C", "POCODASTRINCHEIRAS"),
    (2, 1, "C", "MARECHALDEODORO"),
    (2, 1, "C", "BARRADESAOMIGUEL"),
    (2, 1, "C", "CORURIPE"),
    (2, 2, "C", "FELIZDESERTO"),
    (2, 2, "C", "PIACABUCU"),
    (2, 2, "C", "PENEDO"),
    (2, 2, "C", "IGREJANOVA"),
    (2, 3, "C", "PORTOREALDOCOLEGIO"),
    (2, 3, "C", "JUNQUEIRO"),
    (2, 3, "C", "TEOTONIOVILELA"),
    (2, 4, "C", "SAOSEBASTIAO"),
    (3, 1, "C", "BOCADAMATA"),
    (3, 1, "C", "ANADIA"),
    (3, 1, "C", "MARIBONDO"),
    (3, 1, "C", "TAQUARANA"),
    (3, 1, "C", "COITEDONOIA"),
    (3, 2, "C", "SAOMIGUELDOSCAMPOS"),
    (3, 2, "C", "ROTEIRO"),
    (3, 2, "C", "CAMPOALEGRE"),
    (3, 2, "C", "LIMOEIRODEANADIA"),
    (3, 3, "C", "TRAIPU"),
    (3, 3, "C", "GIRAUDOPONCIANO"),
    (3, 3, "C", "CAMPOGRANDE"),
    (3, 3, "C", "OLHODAGUAGRANDE"),
    (3, 3, "C", "LAGOADACANOA"),
    (3, 4, "C", "FEIRAGRANDE"),
    (34, 1, "C", "IGACI"),
    (34, 1, "C", "CRAIBAS"),
    (34, 1, "C", "FOLHAMIUDA"),
    (34, 2, "C", "JARAMATAIA"),
    (34, 2, "C", "JACAREDOSHOMENS"),
    (34, 2, "C", "MONTEIROPOLIS"),
    (34, 2, "C", "PALESTINA"),
    (34, 2, "C", "PAODEACUCAR"),
    (34, 2, "C", "BELOMONTE"),
    (34, 2, "C", "BATALHA"),
    (34, 3, "C", "OLIVENCA"),
    (34, 3, "C", "SANTANADOIPANEMA"),
    (34, 3, "C", "MAJORIZIDORO"),
    (34, 4, "C", "DOISRIACHOS"),
    (34, 4, "C", "CACIMBINHAS"),
    (34, 4, "C", "MINADORDONEGRAO"),
    (34, 4, "C", "ESTRELADEALAGOAS"),
    (53, 1, "C", "MARAGOGI"),
    (53, 1, "C", "JAPARATINGA"),
    (53, 2, "C", "PORTOCALVO"),
    (53, 2, "C", "PORTODEPEDRAS"),
    (53, 2, "C", "SAOMIGUELDOSMILAGRES"),
    (53, 2, "C", "PASSODECAMARAGIBE"),
    (53, 2, "C", "MATRIZDECAMARAGIBE"),
    (53, 3, "C", "MATRIZDECAMARAGIBE"),
    (53, 3, "C", "SAOLUISDOQUITUNDE"),
    (38, 1, "B", "PESCARIA"),
    (38, 1, "B", "IPIOCA"),
    (38, 1, "B", "RIACHODOCE"),
    (38, 1, "B", "BENEDITOBENTES"),
    (38, 2, "B", "BENEDITOBENTES"),
    (38, 2, "B", "CIDADEUNIVERSITARIA"),
    (38, 2, "B", "SANTOSDUMONT"),
    (38, 3, "B", "CHADAJAQUEIRA"),
    (38, 3, "B", "FERNAOVELHO"),
    (38, 3, "B", "SERRARIA"),
    (38, 3, "B", "OUROPRETO"),
    (38, 3, "B", "BARRODURO"),
]

def _indice(linhas):
    """nome normalizado -> [(codigo, nome original, uf)]"""
    idx = defaultdict(list)
    for r in linhas:
        idx[r[1]].append((int(r[0]), r[2], r[3] if len(r) > 3 else None))
    return idx


def main():
    aplicar = "--aplicar" in sys.argv

    # 1) Mesmo colapso da consulta: (rep, tipo, local) repetido vale o maior dia.
    efetiva = {}
    for codvend, dia, tipo, local in ESCALA_ADMIN:
        chave = (codvend, tipo, local)
        if chave in efetiva and efetiva[chave] != dia:
            print(f"  repetido: rep {codvend} {local} em {DIAS[efetiva[chave]]} e {DIAS[dia]} -> vale {DIAS[max(dia, efetiva[chave])]}")
        efetiva[chave] = max(dia, efetiva.get(chave, 0))

    # 2) Nomes -> códigos, normalizados pelo próprio Oracle.
    ora = conectar_oracle()
    if not ora:
        sys.exit("Sem conexão com o Oracle (confira DB_USER/DB_PASS/DB_DSN).")
    try:
        cur = ora.cursor()
        cur.execute(SQL_CIDADES)
        cidades = _indice(cur.fetchall())
        cur.execute(SQL_BAIRROS)
        bairros = _indice(cur.fetchall())
    finally:
        ora.close()

    linhas, sem_par, ambiguos = [], [], []
    for (codvend, tipo, local), dia in sorted(efetiva.items()):
        achados = (cidades if tipo == "C" else bairros).get(local, [])
        if not achados:
            sem_par.append((codvend, tipo, local, dia))
            continue
        if len(achados) > 1:
            ambiguos.append((codvend, tipo, local, achados))
        for cod, _nome, _uf in achados:
            linhas.append((codvend, dia, tipo, cod if tipo == "C" else None, cod if tipo == "B" else None))

    print(f"\n{len(ESCALA_ADMIN)} linhas na consulta -> {len(efetiva)} locais distintos -> {len(linhas)} linhas a gravar.")
    if ambiguos:
        print("\nNOME COM MAIS DE UM CÓDIGO (entram todos, como na consulta original; limpar na configuração):")
        for codvend, tipo, local, achados in ambiguos:
            print(f"  rep {codvend} {tipo} {local}: " + "; ".join(f"{c} {n} {u or ''}".strip() for c, n, u in achados))
    if sem_par:
        print("\nNÃO CASOU COM NENHUM CÓDIGO (fica de fora; corrigir o nome ou cadastrar na tela):")
        for codvend, tipo, local, dia in sem_par:
            print(f"  rep {codvend} {'cidade' if tipo == 'C' else 'bairro'} {local} ({DIAS[dia]})")

    if not aplicar:
        print("\nNada gravado. Rode com --aplicar para gravar.")
        return

    pg = conectar_postgres()
    try:
        with pg, pg.cursor() as cur:
            cur.execute("SELECT count(*) FROM escala")
            if cur.fetchone()[0]:
                sys.exit("A escala já tem linhas: esta carga é só a inicial. Ajustes vão pela tela de configuração.")
            cur.executemany(
                "INSERT INTO escala (codvend, dia_visita, tipo_local, codcid, codbai) VALUES (%s, %s, %s, %s, %s)",
                linhas,
            )
        print(f"\nGravadas {len(linhas)} linhas em televendas.escala.")
    finally:
        pg.close()


if __name__ == "__main__":
    main()
