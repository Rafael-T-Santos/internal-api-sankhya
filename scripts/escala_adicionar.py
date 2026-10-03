"""Acrescenta um local à escala de visitas, procurando o código pelo NOME.

Para correções pontuais enquanto a tela de configuração da escala não existe.
Cidades são procuradas só em AL (a carga inicial mostrou nomes iguais em vários
estados); o nome é normalizado como na consulta do admin (sem acento, espaço e
pontuação). Sem --aplicar, só mostra o que gravaria.

    docker compose exec api-sankhya python scripts/escala_adicionar.py 34 qua C "Major Isidoro"
    docker compose exec api-sankhya python scripts/escala_adicionar.py 34 qua C "Major Isidoro" --aplicar
"""

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from db import conectar_oracle  # noqa: E402
from pg import conectar_postgres  # noqa: E402

DIAS = {"seg": 1, "ter": 2, "qua": 3, "qui": 4, "sex": 5}
_NORM = """REGEXP_REPLACE(
    TRANSLATE(UPPER(TRIM(NVL({col}, ''))),
              'ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ', 'AAAAAEEEEIIIIOOOOOUUUUC'),
    '[^A-Z0-9]', '')"""
SQL = {
    "C": f"""SELECT CID.CODCID, CID.NOMECID FROM TSICID CID
               JOIN TSIUFS UFS ON UFS.CODUF = CID.UF
              WHERE UFS.UF = 'AL' AND {_NORM.format(col='CID.NOMECID')} = {_NORM.format(col=':NOME')}""",
    "B": f"""SELECT BAI.CODBAI, BAI.NOMEBAI FROM TSIBAI BAI
              WHERE {_NORM.format(col='BAI.NOMEBAI')} = {_NORM.format(col=':NOME')}""",
}


def main():
    args = [a for a in sys.argv[1:] if a != "--aplicar"]
    if len(args) != 4 or args[1] not in DIAS or args[2] not in ("C", "B"):
        sys.exit(__doc__)
    codvend, dia, tipo, nome = int(args[0]), DIAS[args[1]], args[2], args[3]

    ora = conectar_oracle()
    if not ora:
        sys.exit("Sem conexão com o Oracle.")
    try:
        cur = ora.cursor()
        cur.execute(SQL[tipo], {"NOME": nome})
        achados = [(int(r[0]), r[1]) for r in cur.fetchall()]
    finally:
        ora.close()

    if not achados:
        sys.exit(f"Nenhum{'a cidade de AL' if tipo == 'C' else ' bairro'} com o nome '{nome}'. Nada gravado.")
    for cod, n in achados:
        print(f"  encontrado: {cod} {n}")

    pg = conectar_postgres()
    try:
        with pg, pg.cursor() as cur:
            col = "codcid" if tipo == "C" else "codbai"
            novos = []
            for cod, _ in achados:
                cur.execute(
                    f"SELECT dia_visita FROM escala WHERE ativo AND codvend = %s AND tipo_local = %s AND {col} = %s",
                    (codvend, tipo, cod),
                )
                ja = cur.fetchone()
                if ja:
                    print(f"  {cod} já está na escala do rep {codvend} (dia {ja[0]}): ignorado")
                else:
                    novos.append(cod)
            if not novos:
                return
            if "--aplicar" not in sys.argv:
                print(f"Gravaria {len(novos)} linha(s) para o rep {codvend}, dia {args[1]}. Rode com --aplicar.")
                return
            for cod in novos:
                cur.execute(
                    f"INSERT INTO escala (codvend, dia_visita, tipo_local, {col}) VALUES (%s, %s, %s, %s)",
                    (codvend, dia, tipo, cod),
                )
            print(f"Gravadas {len(novos)} linha(s).")
    finally:
        pg.close()


if __name__ == "__main__":
    main()
