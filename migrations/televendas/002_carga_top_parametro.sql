-- Carga inicial das TOPs e dos parâmetros (decisões de 03/10/2026, plano §2 e §5.2).
-- Depois daqui, quem muda é o gerente pela tela de configuração.

-- Conversão: orçamento 1000; pedido 1001, 1010, 1997, 1998 e as de televendas 2130 a 2134.
-- Última compra: as TOPs das consultas do admin (1988, 1990, 2006, 2007) + as de pedido.
INSERT INTO top (codtipoper, conversao, ultima_compra) VALUES
    (1000, 'ORCAMENTO', false),
    (1001, 'PEDIDO',    true),
    (1010, 'PEDIDO',    true),
    (1997, 'PEDIDO',    true),
    (1998, 'PEDIDO',    true),
    (2130, 'PEDIDO',    true),
    (2131, 'PEDIDO',    true),
    (2132, 'PEDIDO',    true),
    (2133, 'PEDIDO',    true),
    (2134, 'PEDIDO',    true),
    (1988, NULL,        true),
    (1990, NULL,        true),
    (2006, NULL,        true),
    (2007, NULL,        true);

INSERT INTO parametro (chave, valor, descricao) VALUES
    ('JANELA_ATRIBUICAO_DIAS', '4',  'Dias depois da ligação em que uma nota do cliente ainda conta como conversão dela.'),
    ('ATRASO_MAX_DIAS',        '5',  'Cliente (ou família matriz/filiais) com atraso maior que isto não entra nas listas.'),
    ('TRAVA_MINUTOS',          '20', 'Quanto tempo o cliente fica reservado para quem abriu a ligação, renovado enquanto a tela está aberta.'),
    ('CODEMP',                 '3',  'Empresa que centraliza as vendas (TGFCAB.CODEMP).');
