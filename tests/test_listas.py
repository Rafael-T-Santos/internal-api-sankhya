"""Regras das listas do televendas que não dependem de banco: escala e formatação.

Rodar da raiz do repositório:  python tests/test_listas.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import televendas_listas as L  # noqa: E402

ok = True


def chk(nome, cond):
    global ok
    ok &= bool(cond)
    print(("OK  " if cond else "FALHOU ") + nome)


# Mesma regra da consulta do admin: ter–sáb, visita já feita nesta semana.
chk("segunda: ninguém liberado", not L.liberado(1, 1))
chk("terça libera quem tem visita na segunda", L.liberado(1, 2) and not L.liberado(2, 2))
chk("sexta libera seg a qui", all(L.liberado(d, 5) for d in (1, 2, 3, 4)))
chk("visita na sexta só libera no sábado", not L.liberado(5, 5) and L.liberado(5, 6))
chk("domingo: ninguém liberado", not any(L.liberado(d, 7) for d in (1, 2, 3, 4, 5)))
chk("sem rota nunca é liberado", not L.liberado(None, 4))

escala = {(2, "C", 100): 1, (38, "B", 7): 3, (38, "C", 200): 2}
cli = lambda v, cid, bai: {"codVendExterno": v, "codCid": cid, "codBai": bai}
chk("casa pela cidade", L.dia_da_escala(escala, cli(2, 100, None)) == 1)
chk("casa pelo bairro", L.dia_da_escala(escala, cli(38, 999, 7)) == 3)
chk("cidade e bairro: vale o maior dia", L.dia_da_escala(escala, cli(38, 200, 7)) == 3)
chk("outro representante não herda a escala", L.dia_da_escala(escala, cli(3, 100, None)) is None)
chk("fila interna (sem representante) sem rota", L.dia_da_escala(escala, cli(None, 100, 7)) is None)

chk("hora de saída como data/hora", L._hora(datetime(2026, 9, 19, 7, 40)) == "07:40")
chk("hora de saída como número HHMM", L._hora(740) == "07:40" and L._hora(1305) == "13:05")
chk("hora de saída nula", L._hora(None) is None)

sql = L.sql_lista("CARTEIRA", so_clientes=True)
chk("carteira com CLIENTE = 'S'", "A.CLIENTE = 'S'" in sql)
chk("conferência sem CLIENTE = 'S'", "A.CLIENTE = 'S'" not in L.sql_lista("CARTEIRA", so_clientes=False))
chk("escala fora do SQL", "ESCALA" not in sql.upper().replace("ESCALA DE", ""))
chk("TOPs viram binds", "{tops}" in sql and "1988" not in sql)

print("TUDO OK" if ok else "HOUVE FALHA")
sys.exit(0 if ok else 1)
