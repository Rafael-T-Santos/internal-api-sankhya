-- Onde os dashboards da Fase 4 vão buscar os dados (plano §4.5 e §6.3).
--
-- 1) FOTO DIÁRIA DAS LISTAS. A positivação ("% dos clientes da lista que compraram
--    no período") precisa saber quem ESTAVA na lista em cada dia — e a lista muda
--    todo dia (escala, atraso, cadastro). Sem a foto, o mês passado mudaria
--    retroativamente. Ela começa a ser tirada antes da Fase 4 existir, para os
--    dashboards já nascerem com histórico. Preenchida por scripts/foto_listas.py.
CREATE TABLE foto_dia (
    data        date PRIMARY KEY,
    tirada_em   timestamptz NOT NULL DEFAULT now(),
    carteiras   integer NOT NULL,          -- quantos televendas tiveram a carteira fotografada
    clientes    integer NOT NULL           -- linhas gravadas em foto_lista nesse dia
);

CREATE TABLE foto_lista (
    data         date     NOT NULL REFERENCES foto_dia (data) ON DELETE CASCADE,
    lista        text     NOT NULL CHECK (lista IN ('CARTEIRA', 'INTERNA')),
    codvend      integer  NOT NULL DEFAULT 0,   -- televendas dono da carteira; 0 na fila interna
    codparc      integer  NOT NULL,
    codvend_ext  integer,                       -- representante externo naquele dia
    liberado     boolean  NOT NULL,             -- carteira: liberado pela escala; interna: sempre true
    PRIMARY KEY (data, lista, codvend, codparc)
);
CREATE INDEX foto_lista_codparc ON foto_lista (codparc, data);

-- 2) CONVERSÕES: nota (pedido/orçamento) atribuída a uma ligação. MANUAL vem do
--    NUNOTA que o operador informou (chamada_nota); AUTO, do cálculo da Fase 4
--    (TOP de conversão + AD_CODVENDINT do operador + até JANELA_ATRIBUICAO_DIAS
--    depois da ligação). Cada nota conta UMA vez (UNIQUE nunota), e o manual vence.
--    Fica vazia até a Fase 4; o cálculo pode ser refeito para trás a qualquer momento.
CREATE TABLE conversao (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chamada_id     bigint      NOT NULL REFERENCES chamada (id) ON DELETE CASCADE,
    nunota         integer     NOT NULL UNIQUE,
    tipo           text        NOT NULL CHECK (tipo IN ('PEDIDO', 'ORCAMENTO')),
    origem         text        NOT NULL CHECK (origem IN ('MANUAL', 'AUTO')),
    codtipoper     integer,
    dtneg          date,
    valor          numeric(14, 2),
    calculado_em   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversao_chamada ON conversao (chamada_id);
