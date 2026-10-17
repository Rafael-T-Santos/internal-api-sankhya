"""Confere no servidor a busca de quem ligou e a ficha avulsa (ligações recebidas).

Para cada termo, roda a busca de verdade no Oracle e no Postgres e mostra o que
achou, com "de quem é" e o tempo. Com --ficha <CODPARC>, monta também a ficha
avulsa do cliente (a mesma das listas, sem filtro de lista). Só lê. Rodar:

    docker compose exec api-sankhya python scripts/conferir_busca.py 558235301722 "construcao" 11842
    docker compose exec api-sankhya python scripts/conferir_busca.py --ficha 11842
"""

import os
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import televendas_recebidas as R  # noqa: E402
from db import conectar_oracle  # noqa: E402
from pg import conectar_postgres  # noqa: E402


def rotulo(d):
    if d["tipo"] == "CARTEIRA":
        return f"carteira de {d.get('apelido')} #{d['codVend']}"
    if d["tipo"] == "INTERNA":
        return "fila interna"
    return "sem lista" + (f" (rep. {d['representante']})" if d.get("representante") else "")


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    ora, pg = conectar_oracle(), conectar_postgres()
    try:
        cur_ora, cur_pg = ora.cursor(), pg.cursor()
        if args[0] == "--ficha":
            for cod in args[1:]:
                t = time.time()
                _, c = R.cliente_avulso(cur_ora, cur_pg, int(cod))
                ms = (time.time() - t) * 1000
                if not c:
                    print(f"#{cod}: não existe ({ms:.0f} ms)")
                    continue
                print(f"#{cod} {c['fantasia']} · {rotulo(c['dono'])} · {ms:.0f} ms")
                print(f"   última compra {c['ultimaCompra']} · atraso {c['diasAtraso']} d · "
                      f"{len(c['contatos'])} contato(s) · OC {c['ultimaOrdemCarga']}")
            return
        for termo in args:
            crit = R.entender(termo)
            t = time.time()
            achados = R.buscar_parceiros(cur_ora, crit)
            ms = (time.time() - t) * 1000
            contatos = R.buscar_contatos(cur_pg, crit)
            print(f"\n'{termo}' -> {', '.join(f'{k}={v}' for k, v in crit.items() if v is not None)}"
                  f" · {len(achados)} parceiro(s) em {ms:.0f} ms · {len(contatos)} contato(s) sem cadastro")
            for p in achados:
                print(f"   #{p['codParc']:<7} {p['fantasia'] or p['razaoSocial']} · {p['cgcCpf'] or 'sem doc'} · "
                      f"{p['cidade'] or '?'}/{p['uf'] or '?'} · tel {p['telefone'] or '-'} · {rotulo(p['dono'])}"
                      + ("" if p["ativo"] else " · INATIVO"))
            for k in contatos:
                print(f"   contato {k['id']}: {k['nome']} · {k['documento'] or 'sem doc'} · {k['telefone'] or '-'}")
    finally:
        ora.close()
        pg.close()


if __name__ == "__main__":
    main()
