-- Televendas: tabelas do schema `televendas` (plano §5.2).
-- Aplicada por scripts/migrar.py, dentro de uma transação: ou entra tudo, ou nada.
--
-- Colunas que apontam para o Sankhya (codparc, codusu, codvend, codcid, codbai,
-- codprod, nunota) NÃO têm FK: os dados moram em outro banco.

CREATE TABLE motivo (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    descricao   text    NOT NULL,
    ordem       integer NOT NULL DEFAULT 0,
    ativo       boolean NOT NULL DEFAULT true
);

CREATE TABLE campanha (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    titulo  text    NOT NULL,
    texto   text,
    inicio  date    NOT NULL,
    fim     date    NOT NULL,
    lista   text    NOT NULL DEFAULT 'AMBAS' CHECK (lista IN ('CARTEIRA', 'INTERNA', 'AMBAS')),
    ativo   boolean NOT NULL DEFAULT true,
    CHECK (fim >= inicio)
);

CREATE TABLE campanha_produto (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    campanha_id  bigint  NOT NULL REFERENCES campanha (id) ON DELETE CASCADE,
    codprod      integer NOT NULL,
    UNIQUE (campanha_id, codprod)
);

CREATE TABLE roteiro (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    titulo       text    NOT NULL,
    texto        text    NOT NULL,
    lista        text    NOT NULL DEFAULT 'AMBAS' CHECK (lista IN ('CARTEIRA', 'INTERNA', 'AMBAS')),
    campanha_id  bigint  REFERENCES campanha (id) ON DELETE SET NULL,
    ativo        boolean NOT NULL DEFAULT true
);

CREATE TABLE meta (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codusu       integer NOT NULL,
    competencia  char(6) NOT NULL CHECK (competencia ~ '^[0-9]{4}(0[1-9]|1[0-2])$'),  -- AAAAMM
    ligacoes_dia integer CHECK (ligacoes_dia >= 0),
    valor_venda  numeric(14, 2) CHECK (valor_venda >= 0),
    positivacao  integer CHECK (positivacao >= 0),                                     -- qtd de clientes
    UNIQUE (codusu, competencia)
);

-- Escala de visita dos representantes externos. O cliente fica liberado para
-- ligação depois do dia de visita do representante na cidade (C) ou bairro (B) dele.
CREATE TABLE escala (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codvend     integer  NOT NULL,                                        -- representante externo
    dia_visita  smallint NOT NULL CHECK (dia_visita BETWEEN 1 AND 5),     -- 1 = seg … 5 = sex
    tipo_local  char(1)  NOT NULL CHECK (tipo_local IN ('C', 'B')),
    codcid      integer,
    codbai      integer,
    ativo       boolean  NOT NULL DEFAULT true,
    CHECK ((tipo_local = 'C' AND codcid IS NOT NULL) OR (tipo_local = 'B' AND codbai IS NOT NULL))
);
CREATE INDEX escala_codvend ON escala (codvend) WHERE ativo;

-- TOPs com dois papéis independentes: conversão (atribuir venda a uma ligação)
-- e "conta como última compra" (data da última compra nas listas).
CREATE TABLE top (
    codtipoper     integer PRIMARY KEY,
    conversao      text    CHECK (conversao IN ('ORCAMENTO', 'PEDIDO')),
    ultima_compra  boolean NOT NULL DEFAULT false,
    ativo          boolean NOT NULL DEFAULT true
);

CREATE TABLE parametro (
    chave      text PRIMARY KEY,
    valor      text NOT NULL,
    descricao  text
);

CREATE TABLE chamada (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codparc      integer     NOT NULL,
    lista        text        NOT NULL CHECK (lista IN ('CARTEIRA', 'INTERNA')),
    codusu       integer     NOT NULL,
    nome_usu     text        NOT NULL,          -- copiado na hora: a tela mostra "quem ligou" sem ir ao Oracle
    codvend      integer,                       -- televendas dono da carteira (nulo na fila interna)
    codvend_ext  integer,                       -- representante externo na hora da ligação
    situacao     text        NOT NULL DEFAULT 'ABERTA' CHECK (situacao IN ('ABERTA', 'FINALIZADA', 'CANCELADA')),
    inicio       timestamptz NOT NULL DEFAULT now(),
    expira       timestamptz NOT NULL,
    fim          timestamptz,
    telefone     text,
    contato      text,
    resultado    text CHECK (resultado IN ('ATENDEU', 'NAO_ATENDEU', 'OCUPADO', 'CAIXA_POSTAL', 'NUMERO_ERRADO', 'RETORNAR_DEPOIS')),
    desfecho     text CHECK (desfecho IN ('VENDA', 'ORCAMENTO', 'SEM_COMPRA', 'INFORMACAO', 'RECLAMACAO', 'RETORNO_AGENDADO')),
    motivo_id    bigint REFERENCES motivo (id),
    retorno_em   timestamptz,
    campanha_id  bigint REFERENCES campanha (id),
    obs          text,
    -- Só o Resultado é obrigatório, e só para finalizar (plano §2).
    CHECK (situacao <> 'FINALIZADA' OR (resultado IS NOT NULL AND fim IS NOT NULL)),
    -- Desfecho só existe quando alguém atendeu.
    CHECK (desfecho IS NULL OR resultado = 'ATENDEU')
);
CREATE INDEX chamada_codparc_inicio ON chamada (codparc, inicio DESC);
CREATE INDEX chamada_abertas ON chamada (codparc) WHERE situacao = 'ABERTA';
CREATE INDEX chamada_retorno ON chamada (codusu, retorno_em) WHERE retorno_em IS NOT NULL;

-- Vínculo MANUAL de nota a uma ligação. O automático é calculado, não gravado.
CREATE TABLE chamada_nota (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chamada_id  bigint  NOT NULL REFERENCES chamada (id) ON DELETE CASCADE,
    nunota      integer NOT NULL,
    tipo        text    NOT NULL CHECK (tipo IN ('PEDIDO', 'ORCAMENTO')),
    UNIQUE (chamada_id, nunota)
);

CREATE TABLE anexo (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chamada_id  bigint      NOT NULL REFERENCES chamada (id) ON DELETE CASCADE,
    descricao   text,
    url         text        NOT NULL CHECK (url ~* '^https?://'),   -- o app abre o link direto
    criado_em   timestamptz NOT NULL DEFAULT now(),
    codusu      integer     NOT NULL
);
