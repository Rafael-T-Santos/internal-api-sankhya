# internal-api-sankhya

API interna em Flask que expõe dados do ERP **Sankhya** (Oracle) para os sistemas da Neto Distribuidora.

Cada endpoint abre sua própria conexão com o Oracle, executa uma query e devolve JSON. Não há ORM, não há camada de serviço, não há autenticação de rede.

| Arquivo | O que tem |
|---|---|
| [app.py](app.py) | Produtos, logística, contagem de estoque, conferência de entrada — e o registro dos blueprints |
| [cobranca.py](cobranca.py) | Blueprint da Cobrança: listas de apoio, inadimplência, Visão 360°, login do operador e a régua de chamadas |
| [cnpj.py](cnpj.py) | Blueprint do CNPJ: situação cadastral na Receita + inscrições estaduais na SEFAZ/AL |
| [funcionarios.py](funcionarios.py) | Blueprint dos Funcionários: quadro da folha (tabelas `TFP*`) com cargo, setor, jornada e status |
| [drive.py](drive.py) | Envio dos anexos de cobrança para o Google Drive da empresa |
| [impostos.py](impostos.py) | Recálculo de impostos via Gateway do Sankhya (`autenticar_sankhya` é reaproveitada pela cobrança) |
| [db.py](db.py) | `conectar_oracle()` — única função de conexão |

---

## Índice

- [Como rodar](#como-rodar)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Convenções de resposta](#convenções-de-resposta)
- [Endpoints](#endpoints)
  - [Produtos](#produtos)
  - [Logística](#logística)
  - [Contagem de estoque](#contagem-de-estoque)
  - [Conferência de entrada](#conferência-de-entrada)
  - [CNPJ / situação do contribuinte](#cnpj--situação-do-contribuinte)
  - [Funcionários](#funcionários)
  - [Cobrança](#cobrança)
  - [Cobrança — operador](#cobrança--operador)
  - [Cobrança — régua de chamadas (escrita)](#cobrança--régua-de-chamadas-escrita)
- [Testes](#testes)
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

Para o recálculo de impostos via API Sankhya (usado no [`/api/cadastrar-produto`](#post-apicadastrar-produto) e na rota de teste [`/api/teste-recalcular-impostos`](#post-apiteste-recalcular-impostos)) também são necessárias:

| Variável | Descrição | Origem |
|---|---|---|
| `SANKHYA_X_TOKEN` | Token de vínculo do Gateway | Tela **Configurações de Gateway** do Sankhya Om |
| `SANKHYA_CLIENT_ID` | Identificador da aplicação | **Portal do Desenvolvedor** |
| `SANKHYA_CLIENT_SECRET` | Segredo da aplicação | **Portal do Desenvolvedor** |
| `SANKHYA_API_BASE` | Base do Gateway (opcional) | Default `https://api.sankhya.com.br`; use `https://api.sandbox.sankhya.com.br` para testar |

> Os três primeiros precisam ser da **mesma aplicação** — misturar Client ID/Secret de uma aplicação com o X-Token de outra é o erro mais comum (`Token e Appkey não associados`).

Para a consulta de CNPJ ([`/api/consultar-cnpj`](#post-apiconsultar-cnpj)):

| Variável | Descrição |
|---|---|
| `CNPJWS_TOKEN` | Token da API comercial da [cnpj.ws](https://cnpj.ws) (enviado no header `x_api_token`) |
| `CNPJWS_DELAY` | Pausa em segundos entre CNPJs de um lote (opcional, default `0.3`) |

> A cnpj.ws é **paga por consulta**. A rota valida o dígito verificador antes de chamar, então CNPJ digitado errado não gasta crédito. A consulta à SEFAZ/AL é pública e não precisa de credencial.

Para a sessão do operador na régua de chamadas:

| Variável | Descrição |
|---|---|
| `COBRANCA_SECRET` | Chave que assina os tokens de sessão. Qualquer string longa e aleatória — ex.: `python -c "import secrets;print(secrets.token_urlsafe(48))"` |

**Opcional, mas defina no servidor.** Sem ela cada processo sorteia o próprio segredo ao subir, e todo `docker compose up` desloga os operadores no meio do expediente.

Para o envio de anexos ao Google Drive ([`drive.py`](drive.py)):

| Variável | Descrição |
|---|---|
| `GOOGLE_CLIENT_ID` | ID do cliente OAuth (tipo *App para computador*) |
| `GOOGLE_CLIENT_SECRET` | Segredo do mesmo cliente |
| `GOOGLE_REFRESH_TOKEN` | Credencial de longa duração, obtida uma única vez |
| `GOOGLE_DRIVE_FOLDER_ID` | Pasta de destino (o trecho do link depois de `/folders/`) |

A conta do Drive é um **Gmail comum**, não Workspace. Isso descarta conta de serviço: ela não tem espaço próprio no Drive e o upload falharia com `storageQuotaExceeded`. O caminho é autorizar o app uma vez pelo navegador e guardar o *refresh token*:

```
pip install google-auth-oauthlib google-api-python-client
python scripts/autorizar-drive.py --client-id XXX --client-secret YYY
```

O script abre o navegador, pede a aprovação, imprime o refresh token e **testa a pasta de verdade** (sobe, compartilha, mostra o link e apaga).

Dois detalhes que quebram isso silenciosamente:

- **A tela de permissão OAuth precisa estar "Em produção".** Em "Testes", o Google expira o refresh token em **7 dias** — os anexos param de funcionar na semana seguinte, sem erro óbvio.
- O escopo é `drive.file`: acesso **só aos arquivos que este app cria**, nunca ao resto do Drive da conta. Se um dia a pasta de destino mudar para uma criada à mão no navegador, o app pode não enxergá-la — rode o script de novo apontando para ela e veja o que ele responde.

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
7. **Recálculo tributário via API Sankhya** (fora da transação, após o `commit`): gravar
   imposto direto no banco **não** dispara o motor de cálculo do Sankhya, então o produto
   nasceria com imposto inconsistente. Para corrigir, a rota limpa os campos-chave
   (`GRUPOICMS`, `TEMICMS`, `CODESPECST` em `TGFPEM`/`TGFPRO`) e reprocessa via
   `DatasetSP.save` (entidades `EmpresaProdutoImpostos`, uma vez por empresa, e `Produto`) —
   o que força o ERP a recalcular. Requer as variáveis `SANKHYA_*` (ver
   [Variáveis de ambiente](#variáveis-de-ambiente)). Detalhes do mecanismo na rota de teste
   [`/api/teste-recalcular-impostos`](#post-apiteste-recalcular-impostos).

Se esse recálculo falhar (Gateway fora, credencial, timeout), o cadastro **não** é
derrubado: a rota **restaura** os valores clonados no banco (produto volta ao estado
"clonado, sem recálculo") e devolve `sucesso: true` com um campo extra `avisoImpostos`.

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

// 200 — produto criado, mas o recálculo tributário via Sankhya falhou
{
  "sucesso": true,
  "codigo": "17421",
  "nomeProduto": "TINTA ACRÍLICA 18L AZUL SERENO IQUINE",
  "mensagem": "Produto cadastrado com 2 componentes.",
  "avisoImpostos": "Recálculo automático de impostos falhou; produto criado com valores clonados (sem recálculo). Refazer o recálculo manualmente."
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

### Conferência de entrada

Recebimento de mercadorias. Mesmo desenho da contagem de estoque: lista o que há para conferir → busca os itens de uma conferência → grava o resultado.

A conferência é **cega**: o conferente conta o que está na doca sem ver a quantidade da nota, e só descobre se bateu ou não. Por isso `qtd_esperada` sai apenas em [`/api/itens-conferencia-entrada`](#post-apiitens-conferencia-entrada), que é **servidor-para-servidor** — quem chama é o backend do Check My Load, que guarda o valor e nunca o repassa ao aplicativo.

Estados de `AD_CONF_ENT_CAB.STATUS`:

```
EM_CONFERENCIA ──▶ AGUARDANDO_LIBERACAO ──▶ CONCLUIDA_COM_DIVERGENCIA
               └─▶ CONCLUIDA_SEM_DIVERGENCIA
CANCELADA a qualquer momento.
```

#### `GET /api/conferencias-entrada-pendentes`

Conferências ainda abertas (`STATUS` em `EM_CONFERENCIA` ou `AGUARDANDO_LIBERACAO`), com o nome do fornecedor vindo de `TGFPAR`.

Sem parâmetros. `dtprevista` sai como texto `YYYY-MM-DD`, não como data serializada — o `jsonify` do Flask escreve `DATE` em RFC 822 (`"Wed, 02 Sep 2026 00:00:00 GMT"`), e isso lido em UTC-3 vira o dia anterior na tela.

```jsonc
// 200
{
  "sucesso": true,
  "totalRegistros": 1,
  "dados": [
    {
      "nuconf": 1, "nunota": 55001, "codemp": 1, "numnota": 12345,
      "codparc": 42, "fornecedor": "FORTLEV INDUSTRIA",
      "dtprevista": "2026-09-02", "qtdvolumes": 30, "status": "EM_CONFERENCIA"
    }
  ]
}
```

---

#### `POST /api/itens-conferencia-entrada`

Itens de uma conferência, com a quantidade esperada congelada no lançamento.

`SEQCONF` e `DESCRPROD_SNAP` saem com alias `sequencia` e `descrprod`, para o consumidor não depender do nome físico da coluna.

```jsonc
// Request
{ "nuConf": 1 }
```

```jsonc
// 200
{
  "sucesso": true,
  "totalRegistros": 1,
  "dados": [
    {
      "nuconf": 1, "sequencia": 1, "sequencia_orig": 1, "codprod": 1001,
      "descrprod": "Caixa d'água 500L", "marca": "FORTLEV", "unidade": "UN",
      "ean13": "7891234567895", "ean14": "17891234567892",
      "fator_ean14": 12, "qtd_esperada": 36
    }
  ]
}
```

---

#### `POST /api/registrar-conferencia-entrada`

Grava o resultado da conferência e move o cabeçalho para o status final. `DHFIM` só é carimbado quando o status começa com `CONCLUIDA` — uma devolução para nova conferência volta a `EM_CONFERENCIA` e limpa o carimbo, senão o tempo de conferência contaria um fim que não aconteceu.

**Tudo ou nada:** se qualquer `seqConf` não existir naquela conferência, a operação inteira sofre rollback — a resposta `404` lista as sequências que não bateram. O mesmo vale se o `nuConf` não existir.

`statusItem` é opcional; omitido, o valor atual da coluna é preservado. `observacao` é opcional e sobrescreve.

```jsonc
// Request
{
  "nuConf": 1,
  "status": "CONCLUIDA_COM_DIVERGENCIA",
  "itens": [
    { "seqConf": 1, "codProd": 1001, "qtdConferida": 36, "statusItem": "OK" },
    { "seqConf": 2, "codProd": 1002, "qtdConferida": 8, "statusItem": "DIVERGENTE",
      "observacao": "Faltaram 2, embalagem violada" }
  ]
}
```

```jsonc
// 200
{ "sucesso": true, "mensagem": "2 item(ns) registrado(s) e conferência 1 marcada como CONCLUIDA_COM_DIVERGENCIA." }

// 400 — status fora da lista
{ "erro": "Status 'FINALIZADA' inválido. Use um de: EM_CONFERENCIA, AGUARDANDO_LIBERACAO, CONCLUIDA_SEM_DIVERGENCIA, CONCLUIDA_COM_DIVERGENCIA, CANCELADA." }

// 400 — item malformado
{ "erro": "Item na posição 1 deve conter 'seqConf' e 'qtdConferida'." }

// 404 — sequência fora da conferência (nada foi salvo)
{ "erro": "Sequências não encontradas na conferência 1: [9]. Nenhuma alteração foi salva." }
```

---

### CNPJ / situação do contribuinte

#### `POST /api/consultar-cnpj`

Decide se um CNPJ está **apto a operar em Alagoas** (`active`), cruzando a Receita Federal (via [cnpj.ws](https://cnpj.ws)) com o cadastro da SEFAZ/AL. Implementado em [`cnpj.py`](cnpj.py) — a regra mora só ali, não a reimplemente no consumidor.

**Regra de `active`:**

1. `situacao_cadastral` na Receita (cnpj.ws) precisa ser **`Ativa`**. Se não for, reprova na hora e **nem chega a consultar a SEFAZ**.
2. Busca as inscrições estaduais **direto na SEFAZ/AL pelo CNPJ** — um CNPJ pode ter vários CACEAIs (o atual e os históricos):
   - **Nenhuma inscrição** (isento, empresa de fora do estado, ou só cadastro de pessoa sem CACEAL) → aprova só pela situação cadastral.
   - **Pelo menos uma com `situacaoCadastralContribuinte = ATIVO`** → aprova. As outras (`BAIXA`, `INAPTO`, `DESENQUADRAMENTO`...) **não atrapalham** — são inscrições antigas do mesmo CNPJ.
   - **Todas encerradas** (`BAIXA` ou `DESENQUADRAMENTO`), quantas forem → aprova. O contribuinte deixou de precisar da IE — encerrou por não ser mais obrigado a ela (`ESTABELECIMENTO NAO OBRIGADO`) ou saiu do regime que a exigia — e segue regular na Receita. Não há inscrição irregular alguma.
   - **Nenhuma `ATIVO` nos demais casos** → reprova, e o `motivo` lista a situação de cada uma. Na prática é ter alguma `INAPTO`: ali a SEFAZ **suspendeu** a inscrição (omissão de declaração, por exemplo), o contribuinte não a encerrou — e uma só basta para reprovar, mesmo que as outras estejam encerradas.
   - **Alguma inscrição sem situação informada** (CACEAL preenchido, `situacaoCadastralContribuinte` nulo) → `active: null`. Dado que a SEFAZ não informou não vira reprovação.

> **As inscrições estaduais da cnpj.ws não são usadas.** A flag `ativo` de lá se mostrou não confiável (desatualizada em relação ao cadastro do estado) — da cnpj.ws vêm só a situação na Receita e os dados de identificação. Quem manda sobre IE de AL é a SEFAZ.

> **Detalhe do payload da SEFAZ:** a inscrição vem partida em `numeroCaceal` (8 dígitos) + `digitoCaceal`. A resposta desta API devolve os dois: `inscricaoEstadual` com os **9 dígitos** juntos (o formato usado na nota fiscal e no Sankhya) e `numeroCaceal` com os 8. Itens com `numeroCaceal: null` são cadastro de pessoa sem inscrição — a SEFAZ devolve o CNPJ com endereço em AL, `situacaoCadastralContribuinte: null` e só o `situacaoCadastralPj` preenchido. São descartados, e um CNPJ que só tenha itens assim conta como **isento** (ex.: `00816565000141`).

Fontes consultadas:

| Fonte | Endpoint | Autenticação |
|---|---|---|
| cnpj.ws | `GET https://comercial.cnpj.ws/cnpj/{cnpj}` | header `x_api_token` (paga) |
| SEFAZ/AL | `GET https://cadastro.sefaz.al.gov.br/sfz-cadastro-api/api/contribuinte/obterListaContribuintes/CNPJ/{cnpj}/{pagina}/{tamanho}` | nenhuma |

```jsonc
// Request — um CNPJ (aceita com ou sem pontuação)
{ "cnpj": "12.014.916/0001-80" }

// Request — lote (máximo 100 por requisição)
{ "cnpjs": ["12014916000180", "..."] }
```

```jsonc
// 200 — registro único. Reprova pela `INAPTO`: a `DESENQUADRAMENTO` sozinha aprovaria
{
  "sucesso": true,
  "cnpj": "12014916000180",
  "active": false,
  "motivo": "Nenhuma IE de AL ativa na SEFAZ (248173391=DESENQUADRAMENTO (MOTIVO GERAL), 240987047=INAPTO (OMISSAO DE DECLARACAO))",
  "razaoSocial": "GIVONILDO GUEDES DOS SANTOS",
  "nomeFantasia": null,
  "situacaoCadastral": "Ativa",
  "uf": "AL",
  "inscricoesEstaduaisAl": [
    { "inscricaoEstadual": "248173391", "numeroCaceal": "24817339", "ativo": false,
      "situacaoCadastral": "DESENQUADRAMENTO", "motivo": "MOTIVO GERAL",
      "dataAlteracao": "2009-03-09 15:10:23.0" },
    { "inscricaoEstadual": "240987047", "numeroCaceal": "24098704", "ativo": false,
      "situacaoCadastral": "INAPTO", "motivo": "OMISSAO DE DECLARACAO",
      "dataAlteracao": "2023-03-21 15:44:50.0" }
  ]
}

// 200 — CNPJ com IE baixada + IE ativa: a ativa manda, o histórico não reprova
{
  "sucesso": true,
  "cnpj": "51134604000161",
  "active": true,
  "motivo": "Situação cadastral Ativa e IE ativa na SEFAZ/AL (241404738)",
  "razaoSocial": "51.134.604 MARLUCE FRANCA BARROS",
  "situacaoCadastral": "Ativa",
  "uf": "AL",
  "inscricoesEstaduaisAl": [
    { "inscricaoEstadual": "240641353", "numeroCaceal": "24064135", "ativo": false,
      "situacaoCadastral": "BAIXA", "motivo": "TRANSFERENCIA PARA OUTRA UNIDADE DA FEDERACAO",
      "dataAlteracao": "2024-07-01 09:53:26.0" },
    { "inscricaoEstadual": "241404738", "numeroCaceal": "24140473", "ativo": true,
      "situacaoCadastral": "ATIVO", "motivo": "INSCRICAO REGULAR",
      "dataAlteracao": "2024-12-11 17:35:34.0" }
  ]
}

// 200 — CNPJ com as IEs todas encerradas: aprova (deixou de precisar da inscrição)
{
  "sucesso": true,
  "cnpj": "58391811000140",
  "active": true,
  "motivo": "Situação cadastral Ativa e IE de AL encerrada (241402506=BAIXA (ESTABELECIMENTO NAO OBRIGADO))",
  "razaoSocial": "E F GONCALVES LTDA",
  "nomeFantasia": "GONCALVES CONSTRUCOES",
  "situacaoCadastral": "Ativa",
  "uf": "AL",
  "inscricoesEstaduaisAl": [
    { "inscricaoEstadual": "241402506", "numeroCaceal": "24140250", "ativo": false,
      "situacaoCadastral": "BAIXA", "motivo": "ESTABELECIMENTO NAO OBRIGADO",
      "dataAlteracao": "2025-03-07 08:27:57.0" }
  ]
}

// 200 — lote (um objeto por CNPJ, no mesmo formato acima)
{ "sucesso": true, "totalRegistros": 2, "dados": [ { ... }, { ... } ] }

// 400 — body ausente, CNPJ inválido, lote acima de 100
{ "erro": "CNPJ inválido (não tem 14 dígitos ou o dígito verificador não confere)" }

// 500 — CNPJWS_TOKEN não configurado
{ "erro": "Credencial da cnpj.ws (CNPJWS_TOKEN) não configurada nas variáveis de ambiente." }
```

**`active` tem três valores, não dois:**

| Valor | Significado |
|---|---|
| `true` | Passou na regra |
| `false` | Reprovado — `motivo` diz qual regra falhou (inclui CNPJ não encontrado na Receita) |
| `null` | **Indeterminado**: a cnpj.ws ou a SEFAZ não responderam, o CNPJ é inválido, ou a SEFAZ não informou a situação de uma inscrição. Não é reprovação — repita a consulta depois |

**Campos de cada item de `inscricoesEstaduaisAl`:**

| Campo | Origem na SEFAZ |
|---|---|
| `inscricaoEstadual` | `numeroCaceal` + `digitoCaceal` (9 dígitos) |
| `numeroCaceal` | `numeroCaceal` (8 dígitos, sem o DV) |
| `ativo` | `true` só quando `situacaoCadastralContribuinte` é `ATIVO`. É a situação **daquela inscrição**, não o veredito do CNPJ — um CNPJ com todas as IEs `BAIXA`/`DESENQUADRAMENTO` sai com `ativo: false` em todas e mesmo assim fica `active: true` |
| `situacaoCadastral` | `situacaoCadastralContribuinte` (`ATIVO`, `BAIXA`, `INAPTO`, `DESENQUADRAMENTO`...) |
| `motivo` | `descricaoMotivoSituacaoCadastral` |
| `dataAlteracao` | `dataAlteracaoSituacaoCastral` (o nome com erro de digitação é da SEFAZ) |

> O `null` existe de propósito: devolver `false` quando a SEFAZ está fora do ar bloquearia clientes bons por indisponibilidade. **Quem consome precisa tratar os três casos** — um `if (!active)` trata indisponibilidade como reprovação.

#### `GET /api/consultar-cnpj/{cnpj}`

Mesma regra, mesma resposta e mesmos códigos de erro do `POST` acima — só muda a forma de mandar o CNPJ. Serve para **um CNPJ por vez**; para lote, use o `POST`.

```
GET /api/consultar-cnpj/12014916000180
GET /api/consultar-cnpj/12.014.916/0001-80
```

Aceita com ou sem máscara. A rota usa o conversor `path:` do Flask (e não o `string:` padrão) porque a máscara do CNPJ tem uma **barra** — com o conversor padrão, o formato pontuado devolvia `404` antes de chegar na função.

O corpo da resposta é idêntico ao do `POST /api/consultar-cnpj` com `{ "cnpj": "..." }`, incluindo o `400` de CNPJ inválido.

---

### Funcionários

#### `GET /api/funcionarios`

O quadro de funcionários da folha (`TFPFUN`), com empresa, cargo, setor, jornada e salário-base. Ordenado por empresa e depois por nome.

**Todos os filtros são opcionais** e vão na querystring — sem nenhum, devolve o quadro inteiro (ativos, afastados e inativos).

| Parâmetro | Tipo | Observação |
|---|---|---|
| `codEmp` | número | Filtra por empresa/filial |
| `codFunc` | número | Filtra um funcionário; a chave real é `codEmp` + `codFunc` |
| `codDep` | número | Filtra por setor (departamento) |
| `codCargo` | número | Filtra por cargo |
| `status` | texto | `ATIVO`, `AFASTADO` ou `INATIVO` (case-insensitive) |
| `busca` | texto | Trecho do nome, do CPF ou da matrícula. O CPF é comparado só por dígito — aceita com ou sem máscara e com ou sem o zero à esquerda |

**Como o `status` é calculado** (não existe essa coluna no Sankhya):

- `INATIVO` — `TFPFUN.SITUACAO <> '1'`. Tem precedência sobre o resto.
- `AFASTADO` — tem ocorrência (`TFPOCO`) vigente hoje (`DTINICOCOR <= hoje` e `DTFINALOCOR` nula ou `>= hoje`) cujo histórico (`TFPHIS`) é de afastamento: código em `A D G M S Y W 2 9`, **ou** código em `E J K O P R T U V Z 5 7` com `REDUZDIASTRAB = 'S'` (sem essa marca, a ocorrência não tira o funcionário do trabalho).
- `ATIVO` — o resto.

```
GET /api/funcionarios?codEmp=1&status=ATIVO
GET /api/funcionarios?busca=MARIA
```

```jsonc
// 200
{
  "sucesso": true,
  "totalRegistros": 87,
  "dados": [
    {
      "codFunc": 42,
      "nome": "MARIA DA SILVA",
      "cpf": "01234567890",
      "matricula": "000042",
      "codEmpresa": 1,
      "empresa": "NETO DISTRIBUIDORA LTDA",
      "status": "ATIVO",
      "dataAdmissao": "2019-03-11",
      "dataDemissao": null,
      "codCargo": 12,
      "cargo": "AUXILIAR ADMINISTRATIVO",
      "codSetor": 3,
      "setor": "ADMINISTRATIVO",
      "codJornada": 1,
      "jornada": "44 HORAS SEMANAIS",
      "horasSemanais": 44,
      "salarioBase": 2100.0,
      "dataVigenciaSalario": null,
      "ultimaAtualizacao": "2026-08-27"
    }
  ]
}
```

```jsonc
// 400 — status fora da lista
{ "erro": "Parâmetro 'status' deve ser ATIVO, AFASTADO ou INATIVO." }
```

```jsonc
// 400 — filtro numérico com texto
{ "erro": "Parâmetro 'codEmp' deve ser um número inteiro." }
```

Notas de contrato:

- `dataVigenciaSalario` **vem sempre `null`**. O campo existe porque a vigência mora no histórico salarial, que ainda não foi mapeado — quando entrar, o contrato não muda.
- `cpf` sai com 11 dígitos, zeros à esquerda preservados (na base ele pode estar como número).
- A `busca` por CPF normaliza os dois lados (`LPAD(REGEXP_REPLACE(...))` na coluna, só os dígitos no termo), então `01234567890`, `1234567890` e `123.456.789-00` acham o mesmo funcionário. Nome e matrícula continuam sendo `LIKE` de texto, sem normalização.
- Datas saem em `"YYYY-MM-DD"`; campos de texto em branco viram `null`.
- Sem paginação: devolve tudo que casar com os filtros.

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

- Títulos (`CODTIPTIT` 2, 4, 5, 39) em aberto, sem baixa.
- Títulos ativos gerados por renegociação, mesmo com tipo fora dessa lista (PIX, cartão).
- Cheques (`CODTIPTIT = 3`), em três situações — a regra vive nas CTEs `CTE_CHEQUES`:
  - **pendente** (`CHQ_NORMAL`): já baixado na conta `16`, mas ainda não resolvido;
  - **em aberto** (`CHQ_ABERTO`): ainda sem baixa nenhuma no financeiro. Não filtra `AD_ACERTADO` (na prática esses cheques vêm com `'S'`) e aceita cheque sem registro na `TGFCHQ` — aí o número sai da nota e a data efetiva é o `DTVENC`;
  - **devolvido** (`DEV_1657`): entrou pela TOP `1657` e continua sem baixa.
  Cheque que já tem devolução correspondente não entra duas vezes: quem vale é a devolução.

A coluna `situacao` traduz esses casos em texto: `CHEQUE PENDENTE`, `CHEQUE EM ABERTO`, `CHEQUE DEVOLVIDO`, `TÍTULO RENEGOCIADO VENCIDO SEM PAGAMENTO` ou `TÍTULO VENCIDO SEM PAGAMENTO`. Para cheque, a data que vale é o "bom para" (`TGFCHQ.DATACHEQUE`), e `atrasoDias` é calculado sobre ela.

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

#### `POST /api/cobranca/cliente`

Identificação + KPIs do cliente para o topo da Visão 360°. Body: `{ "codParc": 100 }`. Devolve `404` se o parceiro não existe.

```jsonc
{ "sucesso": true, "dados": {
  "codParc": 100, "nomeParc": "CLIENTE X", "razaoSocial": "CLIENTE X LTDA",
  "cgcCpf": "12345678000199", "telefone": "81999998888", "email": null,
  "limiteCredito": 5000.0, "ativo": "S", "nomeCid": "RECIFE", "uf": "PE",
  "vendedor": "CARLOS", "pontualidade": 99.0, "titulosPagos12m": 497,
  "titulosAtraso12m": 452, "atrasoMedioDias": 1.8,
  "pontualidadeAtualizadaEm": "2026-08-10",
  "pontualidadeFonte": "SANKHYA_LIMCREDANALISE" } }
```

`pontualidade` **não é calculada aqui**: vem de `AD_LIMCREDANALISE.PCT_PAGO_EM_DIA` (`VERSAO_MODELO = 'V1'`), materializada pela procedure `PRC_ATUALIZA_LIMCREDANALISE`. É a mesma que a análise de crédito do BI exibe. Recalcular por conta própria perde os atrasos regularizados por renegociação e trata entrada de cheque na conta `16` como pagamento definitivo.

**Não é a proporção de títulos pagos no prazo.** O exemplo acima é real: 452 dos 497 títulos foram pagos com atraso — 9% por contagem — e o indicador é 99, porque o atraso médio é de 1,8 dia. Ele acompanha `ATRASO_MEDIO_DIAS`. Por isso `atrasoMedioDias` vai junto: o percentual sozinho é lido como "sempre paga em dia".

`pontualidade` é `null` (e não `0`) só quando não há base nenhuma — nem título pago, nem em atraso. Zero **com** base é fato e vem como `0`: cliente sem nenhum pagamento e com atraso médio de 573 dias existe, e esconder o zero dele apagaria o pior pagador da carteira.

`pontualidadeAtualizadaEm` é o `DH_PROCESSAMENTO` da linha. A procedure não reprocessa todo mundo todo dia — em 2026-08-10 as linhas iam de 27/05 a 10/08 — então a data importa.

#### `POST /api/cobranca/extrato`

Todos os títulos em aberto do cliente — **vencidos e a vencer**. Body: `{ "codParc": 100 }`. Mesmas regras de cheque de `/api/receitas-vencidas` (para cheque, a data que vale é o "bom para", `TGFCHQ.DATACHEQUE`).

---

### Cobrança — operador

#### `POST /api/cobranca/login`

Autentica o operador e **abre a sessão**. Body: `{ "usuario": "<NOMEUSU>", "senha": "..." }`; `401` com credencial inválida.

```jsonc
{ "sucesso": true, "codUsu": 7, "nomeUsu": "FULANO",
  "token": "eyJjb2RVc3UiOjd9.hR2k…", "expiraEmHoras": 12 }
```

**A senha não é conferida no Oracle.** Quem valida é o próprio Sankhya, pelo serviço `MobileLoginSP.login` chamado via Gateway (o hash da `TSIUSU` é proprietário e reproduzi-lo aqui seria frágil). Validada a senha, o `CODUSU` é resolvido na `TSIUSU` por `UPPER(NOMEUSU)`.

O **token** é assinado com HMAC-SHA256 e carrega `codUsu`, `nomeUsu` e a expiração — não há tabela de sessão nem dicionário em memória. É ele que autoriza as rotas de escrita da régua (ver abaixo).

#### `GET /api/cobranca/operadores`

Lista `CODUSU`/`NOMEUSU` da `TSIUSU`, sem filtro de status — um usuário já desativado ainda precisa ser resolvido para exibir "quem ligou" numa chamada antiga.

---

### Cobrança — régua de chamadas (escrita)

Grava em três tabelas customizadas: `AD_COBRCHAMADA` (cabeçalho), `AD_COBRCHAMADAITEM` (títulos da chamada — **a régua vive aqui**) e `AD_COBRANEXO` (links).

**Como o PK é gerado:** por **sequences dedicadas** (`SEQ_AD_COBRCHAMADA`, `SEQ_AD_COBRCHAMADAITEM`, `SEQ_AD_COBRANEXO`) + `RETURNING`. Não é o padrão `TGFNUM` usado em [cadastrar-produto](#post-apicadastrar-produto): o "autoincremento" marcado no Sankhya só age em INSERTs feitos pela camada dele (DynaForm/DatasetSP), e essas tabelas não estão registradas na `TGFNUM` — gravando direto no Oracle, o PK viria nulo.

**Regras que moram na API** (o React não decide nada disso):

| Regra | Implementação |
|---|---|
| Régua conta **por título**, não por cliente | `ORDEM` fica no item (`NUFIN`), não no cabeçalho |
| Chamada **receptiva não conta** na régua | Só `SENTIDO = PROATIVA` + `SITUACAO = FINALIZADA` incrementa `ORDEM` |
| Trava "em chamada" | Derivada: existe item cuja chamada está `EM_ANDAMENTO` **e** `DHEXPIRA > SYSDATE`. Sem campo em `TGFFIN` |
| Trava expira sozinha (15 min) | Toda consulta de trava filtra por `DHEXPIRA` — modal abandonado libera o título |
| Jurídico na 3ª chamada é **opcional** | A API só marca `podeJuridico` quando `ordemAtual >= 3`; o envio é manual |

Domínios aceitos: `sentido` = `PROATIVA`/`RECEPTIVA`; `status` = `ATENDEU`/`CAIXA_POSTAL`/`RECUSOU`/`AGENDOU`/`INFORMOU_PAGTO`; `desfecho` = `ACORDO`/`SEM_ACORDO`/`EM_ABERTO`/`PAGAMENTO_INFORMADO`. Valor fora do domínio → `400`.

**`ACORDO` é renegociação formal**, não promessa de pagamento. Promessa vive no `dhAgenda` da chamada. A distinção importa porque `ACORDO` é o único freio do funil do jurídico (`podeJuridico` exclui quem tem `ultimoDesfecho = ACORDO`).

> ### Todas as rotas de escrita exigem sessão
>
> `Authorization: Bearer <token do login>`. Sem token, com token adulterado ou vencido → **`401`**.
>
> **O `codUsu` não é aceito no corpo.** Quem ligou sai do token, não do que o cliente HTTP diz ser. Antes disso, qualquer um na rede registrava chamada em nome de outra pessoa — inaceitável para uma trilha que justifica negativação.
>
> O token também é aceito como `?token=<...>` na query string. Isso existe por um motivo só: `navigator.sendBeacon`, usado pelo app para cancelar a chamada quando o operador fecha a aba, **não consegue enviar cabeçalhos**.

> As colunas foram criadas quase todas **nullable** no Sankhya. Quem impõe a obrigatoriedade é esta API, não o banco.

#### `POST /api/cobranca/chamadas/iniciar`

Abre a chamada (rascunho `EM_ANDAMENTO`) e **adquire a trava** dos títulos.

```jsonc
// Request  (+ header Authorization: Bearer <token>)
{ "codParc": 100, "nufins": [987654, 987655], "sentido": "PROATIVA" }
```

```jsonc
// 201
{ "sucesso": true, "codChamada": 41, "codParc": 100, "sentido": "PROATIVA",
  "dhInicio": "2026-08-01 09:12:00", "dhExpira": "2026-08-01 09:27:00",
  "nufins": [987654, 987655] }
```

```jsonc
// 409 — alguém já está em chamada com o título
{ "erro": "Título já está em chamada por FULANO.",
  "nufinsTravados": [ { "nufin": 987654, "codChamada": 40, "codParc": 100,
                        "codUsu": 9, "nomeUsu": "FULANO",
                        "desde": "2026-08-01 09:05:00",
                        "expiraEm": "2026-08-01 09:20:00" } ] }
```

Outros erros: `404` título inexistente, `400` título de outro cliente ou mais de 200 títulos numa chamada.

**Concorrência:** antes de checar a trava, a rota faz `SELECT ... FROM TGFFIN ... FOR UPDATE WAIT 5` nos títulos. Sem isso, dois operadores que clicassem no mesmo instante passariam os dois pela checagem (nenhum enxerga o INSERT não commitado do outro). Esgotar a espera (`ORA-00054`) ou dar deadlock (`ORA-00060`) devolve `409`, não `500`.

#### `PUT /api/cobranca/chamadas/{id}/finalizar`

Fecha a chamada, grava o desfecho **por título** e calcula a régua.

```jsonc
// Request — dhAgenda é obrigatório quando status = AGENDOU
{ "status": "ATENDEU", "resumo": "Prometeu pagar sexta.",
  "dhAgenda": null,
  "itens": [ { "nufin": 987654, "desfecho": "ACORDO" },
             { "nufin": 987655, "desfecho": "SEM_ACORDO" } ] }
```

```jsonc
// 200 — ordem = null quando a chamada é RECEPTIVA
{ "sucesso": true, "codChamada": 41, "sentido": "PROATIVA", "status": "ATENDEU",
  "itens": [ { "nufin": 987654, "ordem": 3, "desfecho": "ACORDO" },
             { "nufin": 987655, "ordem": 1, "desfecho": "SEM_ACORDO" } ] }
```

`409` se a chamada já está `FINALIZADA`/`CANCELADA`; `400` se algum `nufin` do payload não pertence à chamada. **Trava expirada não impede finalizar** — a expiração serve para liberar o título para outros, não para descartar o que o operador digitou.

#### `POST /api/cobranca/chamadas/{id}/cancelar`

Descarta a chamada (`SITUACAO = CANCELADA`, `DHFIM = SYSDATE`) e libera a trava. **Idempotente**: cancelar uma chamada já cancelada devolve `200`, porque o front cancela no fechar do modal e de novo no unload da aba.

#### `PUT /api/cobranca/chamadas/{id}/renovar`

Heartbeat do modal aberto: empurra `DHEXPIRA` por mais 15 min → `{ "dhExpira": "..." }`. Devolve `409` se a trava já expirou **e** outro operador assumiu algum título no intervalo — renovar não rouba a trava de quem chegou depois.

#### `POST /api/cobranca/chamadas/{id}/anexos/arquivo`

Sobe um **arquivo do computador do operador** para o Google Drive da empresa e guarda o link na chamada. `multipart/form-data` com o campo `arquivo` (obrigatório) e `descricao` (opcional).

```jsonc
// 201
{ "sucesso": true, "codAnexo": 12, "codChamada": 41,
  "descricao": "boleto.pdf",
  "url": "https://drive.google.com/file/d/1RmZ…/view?usp=drivesdk" }
```

Limite de **25 MB** (`drive.LIMITE_BYTES`) → `413`. Sem as variáveis do Drive no ambiente → `503`. Falha do lado do Google → `502`.

O arquivo vai para o Drive **antes** do `INSERT`. Se fosse ao contrário, um erro no meio deixaria uma linha no banco apontando para um arquivo que não existe; nesta ordem, o pior caso é um arquivo órfão no Drive — que não quebra a tela de ninguém.

O nome no Drive recebe o prefixo `chamada-{id}-`, para quem abrir a pasta meses depois conseguir ligar o arquivo ao atendimento.

#### `POST /api/cobranca/chamadas/{id}/anexos`

Anexa um **link já existente**, sem subir nada. É a rota antiga; o app usa a de cima.

```jsonc
// Request → 201 { "codAnexo": 12, ... }
{ "url": "https://drive.google.com/…", "descricao": "Boleto renegociado" }
```

A `url` precisa começar com `http://` ou `https://` (o app abre o link direto; aceitar `javascript:` seria execução de script vinda de um campo de texto). `409` se a chamada foi cancelada.

#### `GET /api/cobranca/chamadas?codParc=100[&limite=100]`

Histórico do cliente: cabeçalho + `itens` (com `ordem` e `desfecho`) + `anexos`, mais recente primeiro. `limite` entre 1 e 500 (padrão 100).

#### `GET /api/cobranca/locks[?nufins=1,2,3]`

Títulos em chamada **neste momento** (alimenta o badge "em chamada por..."). Sem `nufins`, devolve todas as travas ativas — são poucas por natureza (uma por título aberto num modal), então o filtro é aplicado em memória e a tela não precisa mandar centenas de `NUFIN` na query string.

#### `GET /api/cobranca/regua[?codParc=100]`

Posição de cada título na régua (badge 1ª/2ª/3ª chamada).

```jsonc
{ "sucesso": true, "totalRegistros": 1, "dados": [
  { "nufin": 987654, "codParc": 100, "ordemAtual": 3, "dhUltima": "2026-08-01 09:20:00",
    "ultimoDesfecho": "SEM_ACORDO", "codChamada": 41, "podeJuridico": true } ] }
```

`codParc` é **opcional**: sem ele vem a carteira inteira, para a tela de títulos vencidos montar os badges de todos os clientes de uma vez. Isso não devolve a base toda — só entram títulos que **já tiveram** chamada proativa finalizada.

#### `POST /api/cobranca/pagamento-informado`

Registra que o cliente **informou** o pagamento de um ou mais títulos. Plano completo em `docs/PAGAMENTO-INFORMADO.md` do repositório do front.

```jsonc
// Request  (+ header Authorization: Bearer <token>)
{ "codParc": 100, "nufins": [987654, 987655], "obs": "mandou comprovante no zap" }
```

```jsonc
// 201
{ "sucesso": true, "codChamada": 190, "codParc": 100,
  "nufins": [987654, 987655], "dhInformado": "2026-08-15 10:22:31" }
```

> **É "informou", nunca "pago".** A baixa é do financeiro e sai no Sankhya depois — é ela que faz o título deixar a carteira (`DHBAIXA IS NULL` em `SELECT_RECEITAS`). Medido em 15/08: de ~57 títulos que a operadora registrou como pagos no resumo, **só 1 tinha baixa**. A janela entre pagar e sumir da tela é de vários dias, e é ela que faz alguém ligar de novo para quem já pagou.

Implementação, e o porquê de cada escolha:

| Escolha | Motivo |
|---|---|
| **Sem tabela nova** | Vira uma chamada `RECEPTIVA` já `FINALIZADA` com `STATUS = INFORMOU_PAGTO` e `DESFECHO = PAGAMENTO_INFORMADO` nos itens. O cliente de fato entrou em contato, então o registro é honesto |
| **Não anda na régua** | Receptiva nunca incrementa `ORDEM` — marcar pagamento não empurra ninguém ao jurídico |
| **Não adquire trava** | Não é uma ligação. Travar criaria um "em chamada" falso e um `409` numa ação que precisa ser de dois cliques. Duas pessoas marcando o mesmo título é inofensivo: a leitura usa o registro mais recente |
| **Comprovante sobe depois** | A rota de anexo precisa de um `CODCHAMADA` existente, então aqui a ordem é inversa à do modal de chamada. Como o comprovante é opcional, falha no upload não invalida o registro — mas a tela **tem** de avisar |

O marcador é lido por `SQL_PAGTO_INFORMADO`, uma consulta **independente** — ela não toca na CTE `REGUA`, que é `PROATIVA`-only por definição. Sai anexado à linha do título em `/api/cobranca/extrato`, no campo `pagamentoInformado`:

```jsonc
{ "nuFin": 987654, /* ... */
  "pagamentoInformado": { "dhInformado": "2026-08-15 10:22:31", "codUsu": 43, "nomeUsu": "FABIANA" } }
```

**Não existe conciliação, por decisão de produto.** Nada cobra o financeiro se a baixa demorar: o app informa, o Sankhya é a fonte da verdade. O badge carrega a data justamente para que um marcador velho pareça velho sozinho.

#### `GET /api/cobranca/pagamentos-informados[?codParc=100]`

Os mesmos marcadores em forma de lista, para a tela de **títulos vencidos** montar o badge de todos os clientes de uma vez.

```jsonc
{ "sucesso": true, "totalRegistros": 1, "dados": [
  { "nufin": 987654, "dhInformado": "2026-08-15 10:22:31", "codUsu": 43, "nomeUsu": "FABIANA" } ] }
```

`codParc` é opcional. Sem ele vêm todos — são poucos por natureza (só existe marcador em título que alguém marcou à mão), então a tela cruza em memória em vez de mandar centenas de `NUFIN` na query string. Mesmo raciocínio do `/locks`. Leitura pura: não exige sessão.

### Cobrança — painel da gerência

#### `GET /api/cobranca/painel[?codVend=10&codCid=5]`

Uma linha por **cliente que já foi trabalhado** — o painel é da **cobrança**, não da carteira. Quem nunca recebeu uma chamada **não aparece**: para olhar a dívida crua existe `/api/receitas-vencidas`. Responde "o que está acontecendo na cobrança": quem já foi contatado, quem parou no meio, quem tem retorno marcado e quem esgotou a régua.

A base é `AD_COBRCHAMADA` (quem foi trabalhado) e a carteira entra por `LEFT JOIN`. Consequência desejada: cliente trabalhado que **quitou** continua aparecendo, com dívida zero e `situacao: "SEM_DIVIDA"`, em vez de sumir da tela como se o trabalho não tivesse existido.

```jsonc
{ "sucesso": true, "totalRegistros": 412, "dados": [
  { "codParc": 11107, "nomeParc": "FARMACIA N.S. DAS CANDEIAS", "cgcCpf": "12.345.678/0001-90",
    "qtdTitulos": 5, "valorTotal": 8420.55, "maiorAtrasoDias": 96,
    "estagio": 3, "titulosSemContato": 2, "porOrdem": { "1": 2, "2": 0, "3": 1 },
    "ultimoDesfecho": "SEM_ACORDO", "qtdChamadas": 4,
    "ultimoContatoEm": "2026-08-01 09:20:00", "ultimoContatoPor": "RAFAEL",
    "proximoRetornoEm": null, "proximoRetornoPor": null,
    "retornoAtrasadoDe": "2026-08-05 14:00:00",
    "emChamadaAgora": false,
    "titulosPagamentoInformado": 2, "pagamentoInformadoEm": "2026-08-15 10:22:31",
    "situacao": "RETORNO_ATRASADO", "podeJuridico": true } ] }
```

`estagio` é a **maior** ordem entre os títulos do cliente; `porOrdem` traz a quebra, porque um cliente pode ter títulos em estágios diferentes e o número único sozinho mentiria.

`situacao` é exclusiva e sai nesta precedência: `SEM_DIVIDA` → `RETORNO_ATRASADO` → `AGENDADO` → `ACORDO` → `EM_ANDAMENTO`. `RETORNO_ATRASADO` = havia retorno marcado no passado e **nenhuma** chamada finalizada depois dele — é o estado mais acionável do painel.

Não existe `SEM_CONTATO`: por construção todo cliente aqui já foi trabalhado. `estagio: 0` é o cliente que só teve chamada **receptiva** — houve contato, mas a régua (que só conta proativa) não começou.

`podeJuridico` é **sinalizador separado**, não situação: um cliente pode estar agendado *e* na 3ª chamada, e transformar isso em situação exclusiva esconderia um dos dois. Significa apenas **elegibilidade** (`estagio >= 3` sem acordo) — não existe encaminhamento ao jurídico no sistema.

`titulosPagamentoInformado` é sinalizador pelo mesmo motivo (o cliente pode ter avisado que pagou *e* estar com retorno atrasado). Conta só títulos **que ainda estão na carteira**: quando a baixa sai, o título deixa a carteira e o sinal se apaga sozinho, sem ninguém limpar marcador nenhum.

Nos valores só entram títulos **vencidos** (`ATRASO_DIAS > 0`). O `/receitas-vencidas` devolve também os que ainda vão vencer — de propósito, porque aquela tela usa os filtros de data para recortar o período.

### Cobrança — visão 360° por vendedor

Duas leituras da **mesma** carteira vencida, agrupada de dois jeitos. Plano: `dashboard-cobranca/docs/VENDEDOR-360.md`.

Diferença de fundo para o painel: aqui a base é a **carteira** e as chamadas entram por `LEFT JOIN` — o oposto do painel, e de propósito. Mostrar quem está **fora do radar** da cobrança é o motivo destas rotas existirem.

⚠️ O vendedor sai do **título** (`FIN.CODVEND`), não do cadastro do cliente. Cliente que comprou com dois vendedores aparece nos dois, cada um somando só os títulos dele — então o total de um cliente aqui pode ser **menor** que o da Visão 360° dele, que mostra tudo. É a mesma leitura do filtro de vendedor do `/receitas-vencidas`.

#### `GET /api/cobranca/vendedores-resumo`

Uma linha por vendedor, sem filtro (são poucas dezenas). `codVend: 0` é a linha "SEM VENDEDOR": título sem vendedor no financeiro — dívida real, só sem dono.

```jsonc
{ "sucesso": true, "totalRegistros": 18, "dados": [
  { "codVend": 12, "apelido": "MARCOS", "qtdClientes": 47, "qtdClientesTrabalhados": 9,
    "qtdTitulos": 152, "valorTotal": 284310.77, "maiorAtrasoDias": 812,
    "aging": { "d1a30":    { "qtd": 21, "valor": 44120.10 },
               "d31a90":   { "qtd": 30, "valor": 61200.00 },
               "d91a180":  { "qtd": 18, "valor": 38990.55 },
               "d181a365": { "qtd": 25, "valor": 52000.12 },
               "dMais365": { "qtd": 58, "valor": 88000.00 } } } ] }
```

#### `GET /api/cobranca/vendedor-360[?codVend=12]`

`codVend` é **opcional**: sem ele vem a carteira vencida **inteira**, que é o consolidado "todos os vendedores" da tela de entrada. Esse consolidado tem de vir do banco, e não de somar as linhas do `/vendedores-resumo` no navegador — quem compra com dois vendedores tem título nos dois, e somar os `qtdClientes` contaria o mesmo cliente duas vezes. Aqui a base é o cliente, e cada um aparece uma vez só.

Uma linha por cliente daquele vendedor — os mesmos campos do `/painel`, mais `aging` por cliente. O bloco `vendedor` traz os totais, somados das mesmas linhas de `dados`: assim o cabeçalho e o gráfico da tela nunca discordam da lista.

```jsonc
{ "sucesso": true, "totalRegistros": 47,
  "vendedor": { "codVend": 12, "apelido": "MARCOS", "qtdClientes": 47, "qtdTitulos": 152,
                "valorTotal": 284310.77, "maiorAtrasoDias": 812, "aging": { } },
  "dados": [ { "codParc": 3097, "nomeParc": "CARVALHO E NASCIMENTO", "situacao": "SEM_CONTATO",
               "aging": { } } ] }
```

Aqui `situacao` **inclui `SEM_CONTATO`** — cliente com dívida e nenhuma chamada (nem finalizada nem em andamento; `CANCELADA` não conta, chamada aberta e fechada sem registrar não é contato). No painel esse estado não existe, porque lá todo cliente já foi trabalhado por construção.

As faixas do `aging` são as mesmas nas duas rotas (`<= 30`, `31-90`, `91-180`, `181-365`, `> 365` dias) e ficam escritas uma vez só no SQL: réguas diferentes fariam os dois gráficos discordarem sobre a mesma dívida.

---

## Testes

Um só, e cobre apenas a régua de chamadas — que é a única parte da API que **escreve** no banco por regra de negócio (trava de concorrência, cálculo da régua, transação).

```powershell
.\tests\smoke-chamadas.ps1                        # cliente 11107, operador 25
.\tests\smoke-chamadas.ps1 -CodParc 12366 -CodUsu 25
```

**Rode depois de todo deploy que mexa em `cobranca.py`.** Ele bate na API real: não existe banco de teste, então grava registros de verdade em `AD_COBRCHAMADA`/`ITEM`/`ANEXO` para um cliente real — escolha um que possa ser sujado. No fim ele imprime o SQL de limpeza já com os `CODCHAMADA` gerados.

Cobre sequences gerando PK, trava de 15 minutos, conflito `409` com `nufinsTravados`, **corrida real** (dois `/iniciar` disparados em paralelo, esperando um `201` e um `409`), cancelamento idempotente, renovação, anexo gravado e lido de volta do CLOB, URL `javascript:` recusada, `ORDEM` por título com desfechos independentes, finalização dupla e chamada receptiva que não conta na régua.

Não cobre a expiração dos 15 minutos — não dá para esperar num teste. Para conferir à mão, com uma chamada `EM_ANDAMENTO` aberta:

```sql
UPDATE AD_COBRCHAMADA SET DHEXPIRA = SYSDATE - 1 WHERE CODCHAMADA = <id>;
COMMIT;
```

Depois disso `GET /api/cobranca/locks` tem que voltar vazio.

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
| `_TRAVA_MINUTOS = 15` | [cobranca.py](cobranca.py) | Duração da trava "em chamada"; modal abandonado libera o título depois disso |
| `_MAX_TITULOS_CHAMADA = 200` | [cobranca.py](cobranca.py) | Teto de títulos por chamada — um "selecionar tudo" acidental não trava a carteira inteira |
| `WAIT 5` | [chamadas/iniciar](cobranca.py) | Espera máxima pelo lock em `TGFFIN`; estourar vira `409`, não `500` |
| `SITUACAO = '1'` | [funcionarios.py](funcionarios.py) | Situação da `TFPFUN` que conta como empregado na casa; qualquer outra vira `INATIVO` |
| Códigos `A D G M S Y W 2 9` | [funcionarios.py](funcionarios.py) | `TFPHIS.AFASTAMENTO` que já é afastamento por si só |
| Códigos `E J K O P R T U V Z 5 7` | [funcionarios.py](funcionarios.py) | `TFPHIS.AFASTAMENTO` que só afasta com `REDUZDIASTRAB = 'S'` |

---

## Tabelas do Sankhya usadas

**Padrão:** `TGFPRO` (produtos), `TGFICP` (composição/fórmula), `TGFPEM` (produto × empresa), `TGFFCP`/`TGFEPR` (tributação), `TGFNUM` (numeração), `TGFTAB`/`TGFEXC` (tabelas de preço), `TGFICM` (ICMS/ST), `TGFEST` (estoque), `TGFCAB`/`TGFITE` (notas), `TGFTOP` (operações), `TGFORD`/`TGFVEI` (ordem de carga/veículos), `TGFPAR` (parceiros), `TGFVEN` (vendedores), `TGFFIN`/`VGFFIN` (financeiro), `TGFTIT` (tipos de título), `TGFOBS` (observações), `TGFMAR` (marcas), `TSICID`/`TSIEND`/`TSIBAI`/`TSIUFS` (endereços), `TSIEMP` (empresas), `TSICTA` (contas bancárias).

Na cobrança entram ainda `TGFCHQ` (cheques) e `TSIUSU` (usuários/operadores).

**Folha (`TFP*`):** `TFPFUN` (funcionários), `TFPCAR` (cargos), `TFPDEP` (setores/departamentos), `TFPCGH` (cargas horárias/jornadas), `TFPOCO` (ocorrências do funcionário) e `TFPHIS` (históricos de ocorrência, onde mora o código de afastamento).

**Customizadas (AD\_):** `AD_CONTAGEMMARCA` e `AD_CONTAGEMMARCAITE` (contagem de estoque por marca); `AD_CONF_ENT_CAB` e `AD_CONF_ENT_ITE` (conferência de entrada), com a sequence `AD_SEQ_CONF_ENT`; `AD_COBRCHAMADA`, `AD_COBRCHAMADAITEM` e `AD_COBRANEXO` (régua de chamadas), com as sequences `SEQ_AD_COBRCHAMADA`, `SEQ_AD_COBRCHAMADAITEM` e `SEQ_AD_COBRANEXO`.

Também é usada a function `SNK_PRECO` no cálculo de ST.

Além do Oracle, as rotas [`/api/cadastrar-produto`](#post-apicadastrar-produto) e [`/api/teste-recalcular-impostos`](#post-apiteste-recalcular-impostos) fazem **chamada HTTP externa ao Gateway de APIs do Sankhya** (serviço `DatasetSP.save`, entidades `EmpresaProdutoImpostos` e `Produto`) — nova dependência `requests` e novo modo de falha caso o Gateway esteja fora ou as credenciais estejam erradas. No cadastro, essa falha não derruba a operação (ver `avisoImpostos`).

---

## Limitações conhecidas

Nada disso é bug novo — é o estado atual, documentado para quem for mexer:

- **Sem autenticação, exceto na régua de chamadas.** As rotas de escrita da cobrança exigem token de sessão; todo o resto (inclusive `/api/cadastrar-produto` e `/api/registrar-contagem`, que gravam) segue aberto a qualquer um com acesso de rede. A API continua dependendo de estar em rede fechada.
- **A sessão cai quando o container reinicia**, a menos que `COBRANCA_SECRET` esteja definida — sem ela, cada processo sorteia o próprio segredo na subida e os tokens antigos deixam de valer.
- **CORS liberado para qualquer origem** (`CORS(app)` sem restrição).
- **Servidor de desenvolvimento.** O container roda `flask run`, não um WSGI de produção (gunicorn/waitress). Single-threaded e não recomendado para carga real.
- **Uma conexão nova por request**, aberta e fechada a cada chamada — sem pool. Sob concorrência, isso vira gargalo no Oracle.
- **Sem healthcheck** (`/health`) e sem logging estruturado — só `print()` para stdout.
- **Sem paginação** em `/api/parceiros`, `/api/cidades`, `/api/vendedores`, `/api/funcionarios` e `/api/receitas-vencidas` sem filtro.
- **O nome `/api/receitas-vencidas` mente, e o campo `situacao` também.** As condições `FIN.DTVENC < TRUNC(SYSDATE)` estão comentadas em `SELECT_RECEITAS` **de propósito**: a tela que consome este endpoint mostra títulos a vencer também, e usa os filtros de data para recortar o período. O endpoint devolve, portanto, todo título em aberto (hoje: 8.068 no total, sendo 1.246 vencidos e 6.822 a vencer). O problema real não é o filtro, é a nomenclatura — e principalmente o campo `situacao`, que rotula como `TÍTULO VENCIDO SEM PAGAMENTO` títulos que **ainda não venceram**. Isso é dado incorreto, não só nome ruim. Renomeação de endpoint/tela/rótulo está planejada.
- **Quase sem testes.** Só a régua de chamadas tem cobertura ([tests/smoke-chamadas.ps1](#testes)); os outros 4 domínios não têm nenhuma.

As queries usam bind variables em todos os endpoints, inclusive no SQL dinâmico de `/api/verificar-produto` — não há injeção de SQL.

---

## Manutenção desta documentação

Esta doc é a fonte de verdade para quem consome a API. **Mantenha-a no mesmo commit da mudança de código:**

- Endpoint novo, removido ou renomeado → atualize a seção [Endpoints](#endpoints).
- Mudou payload, resposta ou código de erro → atualize o exemplo correspondente.
- Mudou um valor fixo no SQL (empresa, UF, tipo de operação, produto-modelo) → atualize [Constantes hardcoded](#constantes-hardcoded).
- Resolveu algum item de [Limitações conhecidas](#limitações-conhecidas) → remova-o da lista.
