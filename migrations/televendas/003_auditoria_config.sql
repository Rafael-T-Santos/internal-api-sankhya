-- Quem mexeu por último em cada linha da configuração, e quando.
-- A escala, as TOPs e os parâmetros mudam a lista de TODOS os operadores: quando
-- alguém perguntar "por que esse cliente sumiu da minha lista?", a resposta
-- começa aqui. Linhas da carga inicial ficam com alterado_por nulo.

ALTER TABLE escala    ADD COLUMN alterado_por text, ADD COLUMN alterado_em timestamptz;
ALTER TABLE top       ADD COLUMN alterado_por text, ADD COLUMN alterado_em timestamptz;
ALTER TABLE parametro ADD COLUMN alterado_por text, ADD COLUMN alterado_em timestamptz;

-- O mesmo local não pode aparecer duas vezes ativo para o mesmo representante
-- (a carga inicial já colapsou os repetidos; a tela não deixa recriar).
CREATE UNIQUE INDEX escala_local_unico_cid ON escala (codvend, codcid) WHERE ativo AND tipo_local = 'C';
CREATE UNIQUE INDEX escala_local_unico_bai ON escala (codvend, codbai) WHERE ativo AND tipo_local = 'B';
