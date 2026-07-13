# internal-api-sankhya

API Flask interna que expõe dados do ERP Sankhya (Oracle) via `cx_Oracle`. Todo o código está em `app.py` — um arquivo, sem camadas.

## Regra obrigatória: o README é parte do código

[README.md](README.md) é a fonte de verdade para os sistemas que consomem esta API. **Toda mudança em `app.py` que altere o contrato deve atualizar o README no mesmo commit.** Não deixe para depois e não trate como opcional.

Atualize o README quando:

| Mudança em `app.py` | Seção a atualizar no README |
|---|---|
| Rota nova, removida ou renomeada | `Endpoints` (e o `Índice`) |
| Mudou campo de request, response ou status de erro | O exemplo do endpoint afetado |
| Mudou valor fixo no SQL (CODEMP, UF, CODTIPOPER, produto-modelo 11783…) | `Constantes hardcoded` |
| Nova tabela do Sankhya no SQL | `Tabelas do Sankhya usadas` |
| Resolveu uma limitação (auth, pool, WSGI, testes…) | Remova o item de `Limitações conhecidas` |

Se a mudança for só interna (refatorar uma query sem mudar o JSON de saída), o README não precisa mudar.

## Padrões do código

Ao adicionar um endpoint, siga o formato dos existentes:

- `conectar_oracle()` no início, `conexao.close()` no `finally`, `conexao = None` antes do `try`.
- Sempre **bind variables** (`:PARAM`) — nunca interpole valores em string de SQL.
- Escritas (`INSERT`/`UPDATE`) precisam de `commit()` explícito e `rollback()` nos dois `except`.
- Respostas: `{"sucesso": true, ...}` para registro único, `{"sucesso": true, "totalRegistros": n, "dados": [...]}` para listas, `{"erro": "..."}` com o status adequado nas falhas.
- Chaves do JSON em `camelCase` (exceto onde vêm de `SELECT *`, que saem em minúsculo).

## Ambiente

`DB_USER`, `DB_PASS`, `DB_DSN` vêm do `.env` (não versionado). Sem elas, toda requisição responde 500.

Rodar: `docker compose up -d --build` → `http://localhost:5000`.
