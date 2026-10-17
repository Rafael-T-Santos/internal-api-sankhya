-- Ligações recebidas (plano: televendas/docs/PLANO-LIGACOES-RECEBIDAS.md).
--
-- 1) Quem ligou e não tem cadastro no Sankhya (ou ainda não tinha). Quando o
--    cadastro aparece com o mesmo documento, codparc é preenchido e as ligações
--    do contato passam para o cliente.
CREATE TABLE contato (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    documento   text,                  -- só dígitos: 14 = CNPJ, 11 = CPF; nulo se não informou
    nome        text NOT NULL,         -- razão social ou nome da pessoa
    fantasia    text,
    telefone    text,
    cidade      text,
    uf          char(2),
    obs         text,
    codparc     integer,               -- cadastro no Sankhya, quando ligado
    ligado_em   timestamptz,
    ligado_por  text,                  -- 'AUTO' ou "NOME (codusu)" de quem ligou à mão
    criado_em   timestamptz NOT NULL DEFAULT now(),
    criado_por  integer     NOT NULL,
    CHECK (documento IS NULL OR documento ~ '^([0-9]{11}|[0-9]{14})$')
);
CREATE UNIQUE INDEX contato_documento ON contato (documento) WHERE documento IS NOT NULL;
CREATE INDEX contato_pendente ON contato (criado_em) WHERE codparc IS NULL;

-- 2) Assunto da ligação recebida: lista mantida pela gerência (igual a motivo).
CREATE TABLE assunto (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    descricao  text    NOT NULL UNIQUE,
    ativo      boolean NOT NULL DEFAULT true,
    ordem      integer NOT NULL DEFAULT 0
);
INSERT INTO assunto (descricao, ordem) VALUES
    ('Pedido', 1), ('Cotação / preço', 2), ('2ª via de boleto', 3), ('Entrega', 4),
    ('Troca / devolução', 5), ('Reclamação', 6), ('Outro', 99);

-- 3) A ligação recebida é uma chamada com lista = 'RECEBIDA', de um cliente
--    (codparc) ou de um contato sem cadastro (contato_id).
ALTER TABLE chamada ALTER COLUMN codparc DROP NOT NULL;
ALTER TABLE chamada ADD COLUMN contato_id bigint REFERENCES contato (id);
ALTER TABLE chamada ADD COLUMN assunto_id bigint REFERENCES assunto (id);
-- A regra antiga da coluna lista foi criada sem nome (o Postgres chama de
-- chamada_lista_check); procura pelo conteúdo para não depender disso.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
              WHERE conrelid = 'chamada'::regclass AND contype = 'c'
                AND pg_get_constraintdef(oid) LIKE '%lista%' AND pg_get_constraintdef(oid) LIKE '%INTERNA%'
    LOOP
        EXECUTE format('ALTER TABLE chamada DROP CONSTRAINT %I', r.conname);
    END LOOP;
END $$;
ALTER TABLE chamada ADD CONSTRAINT chamada_lista_check
    CHECK (lista IN ('CARTEIRA', 'INTERNA', 'RECEBIDA'));
ALTER TABLE chamada ADD CONSTRAINT chamada_quem
    CHECK (codparc IS NOT NULL OR (contato_id IS NOT NULL AND lista = 'RECEBIDA'));
CREATE INDEX chamada_contato ON chamada (contato_id) WHERE contato_id IS NOT NULL;
