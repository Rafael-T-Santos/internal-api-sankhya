# internal-api-sankhya

API interna em Flask que expõe dados do ERP **Sankhya** (Oracle) para os sistemas da Neto Distribuidora.

Todo o código vive em [app.py](app.py). Cada endpoint abre sua própria conexão com o Oracle, executa uma query e devolve JSON. Não há ORM, não há camada de serviço, não há autenticação.

---

## Índice

- [Como rodar](#como-rodar)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Convenções de resposta](#convenções-de-resposta)
- [Endpoints](#endpoints)
  - [Produtos](#produtos)
  - [Logística](#logística)
  - [Contagem de estoque](#contagem-de-estoque)
  - [Cobrança](#cobrança)
- [Constantes hardcoded](#constantes-hardcoded)
- [Tabelas do Sankhya usadas](#tabelas-do-sankhya-usadas)
- [Limitações conhecidas](#limitações-conhecidas)
- [Manutenção desta documentação](#manutenção-desta-documentação)

---

## Como rodar

### Docker (recomendado)

O [Dockerfile](Dockerfile) já instala o Oracle Instant Client 19.20 (exigido pelo `cx_Oracle`), então não é preciso instalar nada no host.

```bash
# crie o .env na raiz (veja a seção abaixo)
docker compose up -d --build
```

A API sobe em `http://localhost:5000`. O container reinicia sozinho (`restart: unless-stopped`).

### Local

Requer Python 3.9+ **e** o Oracle Instant Client instalado e visível no `PATH`/`LD_LIBRARY_PATH` — sem ele o `cx_Oracle` não carrega.

```bash
pip install -r requirements.txt
flask run --host=0.0.0.0 --port=5000
```

---

## Variáveis de ambiente

Lidas em [`conectar_oracle()`](app.py#L12). As três são obrigatórias; se qualquer uma faltar, toda requisição responde `500`.

| Variável | Descrição | Exemplo |
|---|---|---|
| `DB_USER` | Usuário do Oracle | `SANKHYA` |
| `DB_PASS` | Senha do Oracle | `••••••` |
| `DB_DSN` | Host:porta/serviço | `192.168.255.250:1521/xe` |

Para o recálculo de impostos via API Sankhya (ver [`/api/teste-recalcular-impostos`](#post-apiteste-recalcular-impostos)) também são necessárias:

| Variável | Descrição | Origem |
|---|---|---|
| `SANKHYA_X_TOKEN` | Token de vínculo do Gateway | Tela **Configurações de Gateway** do Sankhya Om |
| `SANKHYA_CLIENT_ID` | Identificador da aplicação | **Portal do Desenvolvedor** |
| `SANKHYA_CLIENT_SECRET` | Segredo da aplicação | **Portal do Desenvolvedor** |
| `SANKHYA_API_BASE` | Base do Gateway (opcional) | Default `https://api.sankhya.com.br`; use `https://api.sandbox.sankhya.com.br` para testar |

> Os três primeiros precisam ser da **mesma aplicação** — misturar Client ID/Secret de uma aplicação com o X-Token de outra é o erro mais comum (`Token e Appkey não associados`).

O `.env` é lido pelo `docker-compose` e está no `.gitignore` — **nunca commite credenciais**.

---

## Convenções de resposta

Consultas bem-sucedidas seguem um destes dois formatos:

```jsonc
// registro único
{ "sucesso": true, "campo": "valor" }

// lista
{ "sucesso": true, "totalRegistros": 42, "dados": [ ... ] }
```

Erros sempre trazem a chave `erro`:

```jsonc
{ "erro": "Parâmetro 'codProd' é obrigatório." }
```

| Status | Quando acontece |
|---|---|
| `200` | Sucesso (inclusive quando a busca não acha nada em `/api/verificar-produto` e `/api/consultar-estoque`) |
| `400` | Body ausente ou parâmetro obrigatório faltando |
| `404` | Recurso não encontrado |
| `500` | Falha de conexão com o Oracle ou erro de SQL |

> ⚠️ Nos `500`, a mensagem do erro Oracle é repassada crua no corpo da resposta. Como a API é interna isso é tolerável, mas não exponha estes endpoints para fora da rede.

---

## Endpoints

### Produtos

#### `POST /api/verificar-produto`

Verifica se já existe um produto cadastrado com **exatamente** a mesma fórmula (base + pigmentos). Serve para evitar cadastro duplicado de tinta.

**Regra de match:** o produto precisa ter o mesmo número de componentes do payload, nem um a mais. Pigmentos batem por código **e** quantidade (tolerância de `0.001`); a base bate **só por código** — a quantidade dela é ignorada.

```jsonc
// Request
{
  "base": { "codigo": 12345 },
  "pigmentos": [
    { "codigo": 999, "quantidade": 1.5 },
    { "codigo": 888, "quantidade": 0.25 }
  ]
}
```

```jsonc
// 200 — encontrado
{ "cadastrada": true, "codigoProduto": "17420", "nomeProduto": "TINTA ACRILICA 18L AZUL IQUINE" }

// 200 — não encontrado
{ "cadastrada": false }

// 400 — nenhum componente informado
{ "erro": "Nenhum componente informado." }
```

---

#### `POST /api/cadastrar-produto`

Cria um produto de tinta completo. **Operação transacional:** cria o cabeçalho, clona a tributação e insere os componentes; qualquer erro no meio faz rollback de tudo.

O que acontece por baixo:

1. Gera o próximo `CODPROD` a partir da `TGFNUM` (com `FOR UPDATE`, para não colidir).
2. Insere em `TGFPRO` **clonando o produto-modelo `11783`** — grupo, marca, NCM, margens e impostos vêm dele.
3. Gera a `REFERENCIA` (EAN de 13 dígitos) incrementando a última referência da faixa `299…`.
4. Gera o `REFFORN` incrementando o último da mesma marca.
5. Clona `TGFPEM`, `TGFFCP` e `TGFEPR` do produto-modelo (clonagem tributária).
6. Insere a base (`QTDMISTURA = 1`) e cada pigmento em `TGFICP`, na ordem recebida.

A descrição é montada como `TINTA {base} {tamanho} {cor} IQUINE`, em maiúsculas, truncada em 100 caracteres.

```jsonc
// Request
{
  "cor":      { "nome": "Azul Sereno" },
  "base":     { "codigo": 12345, "nome": "Acrílica" },
  "tamanho":  { "nome": "18L", "codVol": "LT", "litros": "3,6" },
  "pigmentos": [
    { "codigo": 999, "quantidade": 1.5 }
  ]
}
```

`litros` aceita vírgula ou ponto decimal (`"3,6"` e `3.6` funcionam). Se vier inválido, cai para `0`. `codVol` default é `"UN"`.

```jsonc
// 200
{
  "sucesso": true,
  "codigo": "17421",
  "nomeProduto": "TINTA ACRÍLICA 18L AZUL SERENO IQUINE",
  "mensagem": "Produto cadastrado com 2 componentes."
}

// 400 — sem base e sem pigmentos
{ "erro": "O produto precisa ter pelo menos uma base ou componente." }
```

---

#### `POST /api/teste-recalcular-impostos`

> ⚠️ **Rota provisória de validação.** Existe para testar o recálculo de impostos pela API do Sankhya antes de integrá-lo ao `/api/cadastrar-produto`. Pode ser removida/absorvida depois.

**Por que existe:** o `/api/cadastrar-produto` grava a tributação com `INSERT` direto no Oracle (clonando o modelo `11783`), e isso **não dispara o motor de cálculo tributário do Sankhya** — o produto nasce com impostos inconsistentes. Esta rota reprocessa a tributação pela API oficial, chamando o serviço `DatasetSP.save` nas entidades `EmpresaProdutoImpostos` (uma vez **por empresa** do modelo em `TGFPEM`) e `Produto`, o que força o ERP a recalcular.

**Fluxo:** lê a tributação-base do produto-modelo `11783` (empresas de `TGFPEM` + campos de `TGFPRO`) → autentica no Gateway (OAuth 2.0 `client_credentials`, token de ~5 min) → dispara os `DatasetSP.save` no `codProd` informado. Requer as variáveis `SANKHYA_*` (ver [Variáveis de ambiente](#variáveis-de-ambiente)).

```jsonc
// Request
{ "codProd": 17421, "limparAntes": false }   // limparAntes default: false
```

> `limparAntes: true` **zera no banco** (`TGFPEM`/`TGFPRO`) os campos `GRUPOICMS`, `TEMICMS` e `CODESPECST` do produto **antes** de chamar o `DatasetSP.save`. Serve para testar se o recálculo tributário só dispara quando o save representa uma **mudança real** (vazio → valor). O próprio save restaura os valores-base, então o estado final é o mesmo — mas passando por uma alteração de verdade. Use só em produto de teste.

```jsonc
// 200 — respostas cruas do Sankhya, para inspeção
{
  "sucesso": true,
  "codProd": 17421,
  "empresas": [
    { "codEmp": 1, "resposta": { /* responseBody do Sankhya */ } },
    { "codEmp": 3, "resposta": { /* responseBody do Sankhya */ } }
  ],
  "produto": { /* responseBody do Sankhya */ }
}

// 400 — sem codProd
{ "erro": "Parâmetro 'codProd' é obrigatório." }

// 500 — credencial ausente, save recusado pelo Sankhya, ou erro de banco
{ "erro": "Credenciais do Sankhya (...) não configuradas nas variáveis de ambiente." }

// 502 — falha de comunicação HTTP com o Gateway
{ "erro": "Falha de comunicação com o Sankhya: ..." }
```

---

#### `POST /api/consultar-preco`

Retorna o preço vigente do produto numa tabela de preços, opcionalmente somando o ST.

O ST **só é somado** quando `codTabela = 0` **e** `cobraST = "S"`. Em qualquer outro caso devolve o `vlrvenda` puro. A query pega sempre a `dtvigor` mais recente da tabela.

```jsonc
// Request
{ "codProd": 17420, "codTabela": 0, "cobraST": "S" }   // cobraST default: "N"
```

```jsonc
// 200
{ "sucesso": true, "codProd": 17420, "codTabela": 0, "preco": 289.9 }

// 404
{ "sucesso": false, "mensagem": "Preço não encontrado para os parâmetros informados." }
```

---

#### `POST /api/consultar-estoque`

Estoque do produto na **empresa 1** (fixo).

```jsonc
// Request
{ "codProd": 17420 }
```

```jsonc
// 200 — com registro
{ "sucesso": true, "codProd": 17420, "estoque": 42.0 }

// 200 — sem registro na TGFEST (não é erro: assume estoque zero)
{
  "sucesso": true,
  "codProd": 17420,
  "estoque": 0.0,
  "mensagem": "Produto não encontrado na tabela de estoque (TGFEST) para a empresa 1."
}
```

---

### Logística

#### `POST /api/consultar-ordem-carga`

Retorna as notas e itens de uma ordem de carga — usado na conferência de carregamento.

Só considera operações marcadas como carga (`TGFTOP.AD_CARGA = 'S'`) e **exclui** os tipos de operação `2002` e `2009`. Retorna uma linha **por item**, ou seja, os dados do cabeçalho (nota, parceiro, motorista, placa) se repetem em cada item.

```jsonc
// Request
{ "ordemCarga": 12345, "codEmp": 1 }   // codEmp default: 1
```

```jsonc
// 200
{
  "sucesso": true,
  "totalRegistros": 2,
  "dados": [
    {
      "ordemCarga": 12345, "codEmp": 1, "empresaRazao": "NETO DISTRIBUIDORA LTDA",
      "numNota": 987, "nuNota": 54321, "numeroNota": 54321,
      "codParc": 100, "parceiroRazao": "CLIENTE X LTDA", "nomeCid": "RECIFE",
      "vlrNota": 1500.0,
      "placa": "ABC1D23", "codParcMotorista": 55, "nomeMotorista": "JOÃO",
      "codProd": 17420, "descrProd": "TINTA ACRILICA 18L AZUL IQUINE",
      "referencia": "2990000000015", "referencia2": null, "validaCodBarra": "S",
      "codVol": "LT", "marca": "IQUINE", "qtdEmb": "4",
      "qtdNeg": 10.0, "qtdVol": 10.0, "vlrTot": 1500.0,
      "descrOper": "VENDA", "doca": "01",
      "horaSaida": "2026-07-13 08:30:00", "seqCarga": 1
    }
  ]
}

// 404
{ "sucesso": false, "mensagem": "Nenhuma ordem de carga ou itens encontrados para o código 12345." }
```

> `numeroNota` é um alias de `nuNota` (mesmo valor), mantido por compatibilidade com o app que consome esta rota.

---

#### `POST /api/consultar-cliente`

Dados e endereço do cliente **a partir de uma nota**.

> ⚠️ **Atenção ao nome do campo:** o parâmetro se chama `numnota`, mas o valor esperado é o **`NUNOTA`** (chave interna única da `TGFCAB`), **não** o número impresso da nota (`NUMNOTA`). Passar o número impresso traz o cliente errado ou nenhum.

```jsonc
// Request
{ "numnota": 54321 }
```

```jsonc
// 200
{
  "sucesso": true,
  "dados": {
    "codparc": 100, "nomeparc": "CLIENTE X", "razaosocial": "CLIENTE X LTDA",
    "nomecid": "RECIFE", "uf": "PE",
    "nomeend": "RUA DAS FLORES", "numend": "123", "nomebai": "BOA VIAGEM"
  }
}

// 404
{ "erro": "Cliente não encontrado para a nota informada" }
```

---

### Contagem de estoque

Fluxo do app de contagem: lista as contagens abertas → busca os itens de uma delas → grava o resultado.

#### `GET /api/contagens-pendentes`

Contagens ainda não processadas (`AD_CONTAGEMMARCA.PROCESSADO` nulo ou `'N'`), com a descrição da marca.

Sem parâmetros. As colunas vêm de `SELECT A.*`, ou seja, **acompanham a estrutura da tabela** `AD_CONTAGEMMARCA` (nomes em minúsculo), mais `descricao_marca`.

```jsonc
// 200
{
  "sucesso": true,
  "totalRegistros": 1,
  "dados": [
    { "nucontagem": 7, "codigo": 12, "processado": "N", "descricao_marca": "IQUINE" }
  ]
}
```

---

#### `POST /api/itens-contagem`

Itens de uma contagem. Traz só os marcados como válidos (`VALIDACONTAGEM = 'S'`), acrescidos de `ad_referencia2` e `ad_validabarra` do produto.

Assim como a rota acima, as colunas vêm de `SELECT I.*` e acompanham a estrutura de `AD_CONTAGEMMARCAITE`.

```jsonc
// Request
{ "nuContagem": 7 }
```

```jsonc
// 200
{
  "sucesso": true,
  "totalRegistros": 1,
  "dados": [
    {
      "nucontagem": 7, "codprod": 17420, "estoquecontagem": null,
      "validacontagem": "S", "ad_referencia2": null, "ad_validabarra": "S"
    }
  ]
}
```

---

#### `POST /api/registrar-contagem`

Grava as quantidades contadas e **fecha a contagem** (`PROCESSADO = 'S'`).

**Tudo ou nada:** se qualquer `codProd` da lista não existir naquela contagem, a operação inteira sofre rollback e nada é salvo — a resposta `404` lista os códigos que não bateram.

```jsonc
// Request
{
  "nuContagem": 7,
  "itens": [
    { "codProd": 17420, "estoqueContagem": 38 },
    { "codProd": 17421, "estoqueContagem": 12 }
  ]
}
```

```jsonc
// 200
{ "sucesso": true, "mensagem": "2 item(ns) registrado(s) e contagem 7 marcada como processada." }

// 400 — item malformado
{ "erro": "Item na posição 1 deve conter 'codProd' e 'estoqueContagem'." }

// 404 — produto fora da contagem (nada foi salvo)
{ "erro": "Produtos não encontrados na contagem 7: [17421]. Nenhuma alteração foi salva." }
```

---

### Cobrança

#### `GET /api/cidades`

Todas as cidades (`TSICID`), ordenadas por nome. Sem parâmetros.

```jsonc
{ "sucesso": true, "totalRegistros": 5570,
  "dados": [ { "codCid": 2531, "uf": "PE", "nomeCid": "RECIFE" } ] }
```

#### `GET /api/vendedores`

Todos os vendedores (`TGFVEN`), ordenados por apelido. Sem parâmetros.

```jsonc
{ "sucesso": true, "totalRegistros": 30,
  "dados": [ { "codVend": 5, "apelido": "CARLOS" } ] }
```

#### `GET /api/parceiros`

Todos os parceiros (`TGFPAR`), ordenados por nome. Sem parâmetros.

```jsonc
{ "sucesso": true, "totalRegistros": 12000,
  "dados": [ { "codParc": 100, "nomeParc": "CLIENTE X", "razaoSocial": "CLIENTE X LTDA", "cgcCpf": "12345678000199" } ] }
```

> Estas três rotas retornam a tabela **inteira**, sem paginação — `/api/parceiros` pode devolver payloads grandes.

---

#### `POST /api/receitas-vencidas`

O relatório de inadimplência: títulos e cheques vencidos/pendentes.

**Todos os filtros são opcionais** — sem nenhum, retorna a base inteira de pendências.

| Campo | Tipo | Observação |
|---|---|---|
| `codEmp` | número | Filtra por empresa |
| `codParc` | número | Filtra por parceiro |
| `codVend` | número | Filtra por vendedor |
| `codCid` | número | Filtra pela cidade **do parceiro** |
| `dtInicial` | `"YYYY-MM-DD"` | Intervalo de `DTVENC`. Só aplicado **se `dtFinal` também vier** |
| `dtFinal` | `"YYYY-MM-DD"` | Idem — os dois andam juntos, enviar só um é ignorado |

**O que entra no relatório** (`RECDESP = 1`, sem provisão, sem renegociação):

- Títulos (`CODTIPTIT` 4, 5, 39) vencidos e sem baixa.
- Cheques devolvidos (`CODTIPOPER = 1657`) sem baixa e não acertados.
- Cheques vencidos não acertados, sem compensação, sem baixa **ou** baixados na conta `16` — e que não tenham um cheque devolvido em aberto correspondente.

A coluna `situacao` traduz esses casos em texto: `CHEQUE DEVOLVIDO PENDENTE`, `CHEQUE VENCIDO PENDENTE - BAIXADO NA CONTA 16`, `CHEQUE VENCIDO SEM BAIXA` ou `TITULO VENCIDO SEM PAGAMENTO`.

```jsonc
// Request
{ "codEmp": 1, "codVend": 5, "dtInicial": "2026-01-01", "dtFinal": "2026-07-13" }
```

```jsonc
// 200
{
  "sucesso": true,
  "totalRegistros": 1,
  "dados": [
    {
      "nuFin": 987654, "nuNota": 54321, "numNota": 987, "desdobramento": 1,
      "nossoNum": "00012345", "nuCompens": null, "nuReneg": null,
      "dtNeg": "2026-03-01", "dtVenc": "2026-04-01", "atrasoDias": 103,
      "vlrDesdob": 1500.0, "vlrLiquido": 1500.0, "vlrCheque": null,
      "vlrDesconto": 0.0, "vlrJuros": 0.0,
      "codParc": 100, "nomeParc": "CLIENTE X", "razaoSocial": "CLIENTE X LTDA",
      "cnpjCpf": "12.345.678/0001-99", "telefone": "81999998888",
      "codCid": 2531, "nomeCid": "RECIFE", "uf": "PE",
      "vendedor": "CARLOS",
      "tipoTitulo": "DUPLICATA", "situacao": "TITULO VENCIDO SEM PAGAMENTO",
      "historico": "VENDA", "contaBancaria": "BANCO X",
      "cgcCpfCmc7": null, "nomeEmitente": null,
      "codObsPadrao": null, "observacao": null
    }
  ]
}
```

`cnpjCpf` já vem formatado (`00.000.000/0000-00` ou `000.000.000-00`); `atrasoDias` nunca é negativo. Valores monetários nulos viram `0.0` em desconto/juros, mas permanecem `null` em `vlrDesdob`/`vlrLiquido`/`vlrCheque`.

---

## Constantes hardcoded

Valores fixos dentro do SQL que mudam o resultado e **não são parametrizáveis** hoje. Se a regra de negócio mudar, é aqui que se mexe:

| Constante | Onde | Significado |
|---|---|---|
| `CODPROD 11783` | [cadastrar-produto](app.py#L223) | Produto-modelo clonado (grupo, marca, NCM, impostos) |
| `CODEMP 1` | [cadastrar-produto](app.py#L232), [consultar-estoque](app.py#L514) | Empresa usada para gerar código e ler estoque |
| `CODEMP 3` | [consultar-preco](app.py#L435) | Empresa usada para achar o grupo de ICMS |
| `UFDEST 19` | [consultar-preco](app.py#L441) | UF de destino no cálculo do ST |
| `CODTAB 0` | [consultar-preco](app.py#L416) | Única tabela em que o ST é somado |
| Faixa `299…` | [cadastrar-produto](app.py#L244) | Prefixo das referências (EAN) geradas |
| `CODTIPOPER 2002, 2009` | [consultar-ordem-carga](app.py#L610) | Operações excluídas da carga |
| `CODTIPOPER 1657` | [receitas-vencidas](app.py#L1105) | Operação de cheque devolvido |
| `CODCTABCOINT 16` | [receitas-vencidas](app.py#L1107) | Conta em que cheque baixado ainda é pendência |
| `CODTIPTIT 3 / 4, 5, 39` | [receitas-vencidas](app.py#L1138) | `3` = cheque; demais = títulos |

---

## Tabelas do Sankhya usadas

**Padrão:** `TGFPRO` (produtos), `TGFICP` (composição/fórmula), `TGFPEM` (produto × empresa), `TGFFCP`/`TGFEPR` (tributação), `TGFNUM` (numeração), `TGFTAB`/`TGFEXC` (tabelas de preço), `TGFICM` (ICMS/ST), `TGFEST` (estoque), `TGFCAB`/`TGFITE` (notas), `TGFTOP` (operações), `TGFORD`/`TGFVEI` (ordem de carga/veículos), `TGFPAR` (parceiros), `TGFVEN` (vendedores), `TGFFIN`/`VGFFIN` (financeiro), `TGFTIT` (tipos de título), `TGFOBS` (observações), `TGFMAR` (marcas), `TSICID`/`TSIEND`/`TSIBAI`/`TSIUFS` (endereços), `TSIEMP` (empresas), `TSICTA` (contas bancárias).

**Customizadas (AD\_):** `AD_CONTAGEMMARCA` e `AD_CONTAGEMMARCAITE` (contagem de estoque por marca).

Também é usada a function `SNK_PRECO` no cálculo de ST.

Além do Oracle, a rota [`/api/teste-recalcular-impostos`](#post-apiteste-recalcular-impostos) faz **chamada HTTP externa ao Gateway de APIs do Sankhya** (serviço `DatasetSP.save`, entidades `EmpresaProdutoImpostos` e `Produto`) — nova dependência `requests` e novo modo de falha caso o Gateway esteja fora ou as credenciais estejam erradas.

---

## Limitações conhecidas

Nada disso é bug novo — é o estado atual, documentado para quem for mexer:

- **Sem autenticação.** Qualquer um com acesso de rede chama qualquer rota, inclusive as que gravam (`/api/cadastrar-produto`, `/api/registrar-contagem`). A API depende inteiramente de estar em rede fechada.
- **CORS liberado para qualquer origem** (`CORS(app)` sem restrição).
- **Servidor de desenvolvimento.** O container roda `flask run`, não um WSGI de produção (gunicorn/waitress). Single-threaded e não recomendado para carga real.
- **Uma conexão nova por request**, aberta e fechada a cada chamada — sem pool. Sob concorrência, isso vira gargalo no Oracle.
- **Sem healthcheck** (`/health`) e sem logging estruturado — só `print()` para stdout.
- **Sem paginação** em `/api/parceiros`, `/api/cidades`, `/api/vendedores` e `/api/receitas-vencidas` sem filtro.
- **Sem testes.**

As queries usam bind variables em todos os endpoints, inclusive no SQL dinâmico de `/api/verificar-produto` — não há injeção de SQL.

---

## Manutenção desta documentação

Esta doc é a fonte de verdade para quem consome a API. **Mantenha-a no mesmo commit da mudança de código:**

- Endpoint novo, removido ou renomeado → atualize a seção [Endpoints](#endpoints).
- Mudou payload, resposta ou código de erro → atualize o exemplo correspondente.
- Mudou um valor fixo no SQL (empresa, UF, tipo de operação, produto-modelo) → atualize [Constantes hardcoded](#constantes-hardcoded).
- Resolveu algum item de [Limitações conhecidas](#limitações-conhecidas) → remova-o da lista.
