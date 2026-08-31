"""Consulta de CNPJ e decisão de `active` para contribuintes de Alagoas.

Combina duas fontes externas:

1. **cnpj.ws** (paga, token por header `x_api_token`) — usada só para a situação
   cadastral na Receita Federal e os dados de identificação (razão social, fantasia).
2. **SEFAZ/AL** (pública, sem autenticação) — busca as inscrições estaduais direto pelo
   CNPJ e a situação de cada uma.

As inscrições estaduais da cnpj.ws **não são usadas**: a flag `ativo` de lá se mostrou
não confiável (desatualizada em relação ao cadastro do estado). Quem manda sobre IE de AL
é a SEFAZ, e a lista vem dela pelo CNPJ — não há mais consulta por CACEAL.

A regra de `active` está em `avaliar_cnpj()` — é a única fonte de verdade do critério e
qualquer consumidor (incluindo o job de lote do projeto enriquecimento-cnpjws) deve
chamar a rota em vez de reimplementá-la.

Credenciais (via .env, ver README):
- CNPJWS_TOKEN -> token da API comercial da cnpj.ws
"""
import os
import re
import time

import requests
from flask import Blueprint, jsonify, request

bp = Blueprint("cnpj", __name__)

CNPJWS_URL = "https://comercial.cnpj.ws/cnpj/"
SEFAZ_AL_LISTA_URL = (
    "https://cadastro.sefaz.al.gov.br/sfz-cadastro-api/api/contribuinte/"
    "obterListaContribuintes/CNPJ/"
)

# Timeout (segundos) das chamadas HTTP externas.
_TIMEOUT = 30

# Tentativas em caso de 429 (rate limit da cnpj.ws) ou 5xx.
_MAX_TENTATIVAS = 4

# Pausa entre CNPJs num lote, para respeitar o rate limit da cnpj.ws.
_DELAY_LOTE = float(os.environ.get("CNPJWS_DELAY", "0.3"))

# Teto de CNPJs por requisição: cada um gasta 1 chamada na cnpj.ws + 1 na SEFAZ, então um
# lote grande demais estouraria o timeout do cliente HTTP.
_MAX_LOTE = 100

# Situação da Receita que aprova o CNPJ. Comparada sem acento/caixa.
_SITUACAO_APROVADA = "ativa"

# Situação da inscrição estadual na SEFAZ/AL que aprova. As demais que aparecem no
# cadastro (BAIXA, INAPTO, DESENQUADRAMENTO...) não aprovam sozinhas, mas também não
# reprovam se houver outra IE ATIVO — costumam ser inscrições históricas do mesmo CNPJ.
_SITUACAO_SEFAZ_ATIVA = "ATIVO"

# Paginação do endpoint de lista da SEFAZ (a URL termina em /{pagina}/{tamanho}).
_SEFAZ_TAMANHO_PAGINA = 50

# Trava de segurança: um CNPJ com mais páginas que isso é caso patológico, não vale
# manter o cliente esperando.
_SEFAZ_MAX_PAGINAS = 5

UF_ALVO = "AL"

# Mensagens comparadas por identidade em vez de substring — mudar o texto não pode
# mudar silenciosamente o comportamento de quem decide em cima delas.
ERRO_NAO_ENCONTRADO = "CNPJ não encontrado na Receita Federal"
ERRO_CNPJ_INVALIDO = "CNPJ inválido (não tem 14 dígitos ou o dígito verificador não confere)"


# --------------------------------------------------------------------------------------
# Validação de CNPJ
# --------------------------------------------------------------------------------------

def limpar_cnpj(valor):
    """Deixa só os dígitos. Devolve None se não for um CNPJ válido.

    Valida os dígitos verificadores, então CNPJ estruturalmente inválido (digitado
    errado, dígitos repetidos) é rejeitado aqui e não gasta uma consulta paga.
    """
    if not valor:
        return None
    digitos = re.sub(r"\D", "", str(valor))
    if len(digitos) != 14 or not _cnpj_dv_valido(digitos):
        return None
    return digitos


def _cnpj_dv_valido(cnpj):
    """Confere os dois dígitos verificadores de um CNPJ de 14 dígitos."""
    if cnpj == cnpj[0] * 14:  # todos os dígitos iguais (00000..., 11111...)
        return False

    def dv(base, pesos):
        soma = sum(int(d) * p for d, p in zip(base, pesos))
        resto = soma % 11
        return "0" if resto < 2 else str(11 - resto)

    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = dv(cnpj[:12], pesos1)
    d2 = dv(cnpj[:12] + d1, pesos2)
    return cnpj[12:] == d1 + d2


# --------------------------------------------------------------------------------------
# Clientes HTTP
# --------------------------------------------------------------------------------------

def consultar_cnpjws(cnpj):
    """Consulta a cnpj.ws. Devolve (dados, erro) — um dos dois é sempre None.

    Trata rate limit (429) e erros de servidor (5xx) com novas tentativas.
    """
    token = os.environ.get("CNPJWS_TOKEN")
    if not token:
        raise RuntimeError(
            "Credencial da cnpj.ws (CNPJWS_TOKEN) não configurada nas variáveis de ambiente."
        )

    tentativa = 0
    while True:
        tentativa += 1
        try:
            resp = requests.get(
                CNPJWS_URL + cnpj,
                headers={"x_api_token": token},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as err:
            if tentativa < _MAX_TENTATIVAS:
                time.sleep(2 * tentativa)
                continue
            return None, f"Falha de conexão com a cnpj.ws: {err}"

        if resp.status_code == 200:
            try:
                return resp.json(), None
            except ValueError:
                return None, f"cnpj.ws devolveu corpo não-JSON: {resp.text[:200]}"

        if resp.status_code == 404:
            return None, ERRO_NAO_ENCONTRADO

        if resp.status_code == 429 and tentativa < _MAX_TENTATIVAS:
            time.sleep(_retry_after(resp, padrao=5))
            continue

        if resp.status_code >= 500 and tentativa < _MAX_TENTATIVAS:
            time.sleep(2 * tentativa)
            continue

        return None, f"cnpj.ws HTTP {resp.status_code}: {resp.text[:200]}"


def _retry_after(resp, padrao):
    try:
        return int(resp.headers.get("Retry-After"))
    except (TypeError, ValueError):
        return padrao


def consultar_sefaz_al(cnpj):
    """Lista as inscrições estaduais de AL do CNPJ na SEFAZ.

    Devolve (contribuintes, erro) — um dos dois é sempre None. `contribuintes` é a lista
    crua do campo `content` da SEFAZ, já concatenada de todas as páginas; lista vazia
    significa CNPJ sem cadastro no estado (isento), não falha.

    A busca é feita pelo CNPJ, então um mesmo CNPJ pode devolver vários CACEAIs (o atual
    e os históricos, como uma inscrição baixada por transferência de UF).
    """
    contribuintes = []
    pagina = 0
    while pagina < _SEFAZ_MAX_PAGINAS:
        url = f"{SEFAZ_AL_LISTA_URL}{cnpj}/{pagina}/{_SEFAZ_TAMANHO_PAGINA}"
        dados, erro = _get_sefaz(url)
        if erro:
            return None, erro

        contribuintes.extend(dados.get("content") or [])

        pagina += 1
        if dados.get("last") or pagina >= (dados.get("totalPages") or 1):
            break

    return contribuintes, None


def _get_sefaz(url):
    """GET na SEFAZ/AL com retentativa em 5xx. Devolve (json, erro).

    `404` não é erro: o endpoint responde assim quando o CNPJ não tem cadastro no estado,
    o que é o mesmo que uma lista vazia.
    """
    tentativa = 0
    while True:
        tentativa += 1
        try:
            resp = requests.get(url, timeout=_TIMEOUT)
        except requests.RequestException as err:
            if tentativa < _MAX_TENTATIVAS:
                time.sleep(2 * tentativa)
                continue
            return None, f"Falha de conexão com a SEFAZ/AL: {err}"

        if resp.status_code == 200:
            try:
                return resp.json(), None
            except ValueError:
                return None, f"SEFAZ/AL devolveu corpo não-JSON: {resp.text[:200]}"

        if resp.status_code == 404:
            return {"content": [], "last": True, "totalPages": 1}, None

        if resp.status_code >= 500 and tentativa < _MAX_TENTATIVAS:
            time.sleep(2 * tentativa)
            continue

        return None, f"SEFAZ/AL HTTP {resp.status_code}: {resp.text[:200]}"


def _normalizar_contribuinte(item):
    """Reduz um item da SEFAZ ao que a resposta da rota expõe."""
    identificacao = item.get("identificacao") or {}
    situacao = item.get("situacaoCadastral") or {}
    caceal = item.get("numeroCaceal") or identificacao.get("numeroCaceal")
    situacao_ie = (situacao.get("situacaoCadastralContribuinte") or "").strip().upper()

    return {
        "inscricaoEstadual": _inscricao_completa(caceal, identificacao.get("digitoCaceal")),
        "numeroCaceal": str(caceal) if caceal is not None else None,
        "ativo": situacao_ie == _SITUACAO_SEFAZ_ATIVA,
        "situacaoCadastral": situacao.get("situacaoCadastralContribuinte"),
        "motivo": situacao.get("descricaoMotivoSituacaoCadastral"),
        # O nome do campo é "Castral" mesmo — erro de digitação da API da SEFAZ.
        "dataAlteracao": situacao.get("dataAlteracaoSituacaoCastral"),
    }


def _inscricao_completa(caceal, digito):
    """Monta a IE de 9 dígitos a partir do CACEAL (8) + dígito verificador.

    A SEFAZ devolve os dois separados (`numeroCaceal` e `digitoCaceal`), mas o resto do
    mundo — nota fiscal, cadastro do Sankhya — usa os 9 dígitos juntos.
    """
    if caceal is None:
        return None
    numero = re.sub(r"\D", "", str(caceal))
    if not numero:
        return None
    if digito is None:
        return numero
    return numero + re.sub(r"\D", "", str(digito))


# --------------------------------------------------------------------------------------
# Regra de negócio
# --------------------------------------------------------------------------------------

def avaliar_cnpj(cnpj):
    """Aplica a regra de `active` a um CNPJ já validado (14 dígitos).

    Regra:
    1. `situacao_cadastral` na Receita (cnpj.ws) precisa ser "Ativa" — se não for,
       reprova na hora e nem consulta a SEFAZ.
    2. Busca as inscrições estaduais do CNPJ na SEFAZ/AL:
       - Nenhuma inscrição (isento, de fora do estado, ou só cadastro de pessoa sem
         CACEAL): aprova pela situação cadastral.
       - Pelo menos uma com `situacaoCadastralContribuinte = ATIVO`: aprova. As demais
         (BAIXA, INAPTO...) não atrapalham — são inscrições históricas do mesmo CNPJ.
       - Nenhuma ATIVO: reprova, dizendo a situação de cada uma. Se alguma veio sem
         situação informada, fica indefinido (None) em vez de reprovar.

    Devolve o dict de resposta. `active` é None quando não deu para decidir (fonte
    externa fora do ar), para não reprovar um cliente bom por indisponibilidade.
    """
    dados, erro = consultar_cnpjws(cnpj)
    if erro:
        # "não encontrado na Receita" é uma resposta de negócio (reprova); qualquer outro
        # erro é indisponibilidade da fonte, que fica indeterminado (None).
        return {
            "cnpj": cnpj,
            "active": False if erro == ERRO_NAO_ENCONTRADO else None,
            "motivo": erro,
        }

    est = dados.get("estabelecimento") or {}
    situacao = est.get("situacao_cadastral")

    resultado = {
        "cnpj": cnpj,
        "active": None,
        "motivo": None,
        "razaoSocial": dados.get("razao_social"),
        "nomeFantasia": est.get("nome_fantasia"),
        "situacaoCadastral": situacao,
        "uf": (est.get("estado") or {}).get("sigla"),
        "inscricoesEstaduaisAl": [],
    }

    # 1. Situação na Receita.
    if (situacao or "").strip().lower() != _SITUACAO_APROVADA:
        resultado["active"] = False
        motivo_receita = (est.get("motivo_situacao_cadastral") or {}).get("descricao")
        resultado["motivo"] = (
            f"Situação cadastral na Receita é '{situacao}'"
            + (f" ({motivo_receita.strip()})" if motivo_receita else "")
        )
        return resultado

    # 2. Inscrições estaduais, direto da SEFAZ/AL pelo CNPJ.
    contribuintes, erro_sefaz = consultar_sefaz_al(cnpj)
    if erro_sefaz:
        resultado["active"] = None
        resultado["motivo"] = f"Não foi possível consultar a SEFAZ/AL: {erro_sefaz}"
        return resultado

    # Itens sem CACEAL são cadastro de pessoa sem inscrição estadual: a SEFAZ devolve o
    # CNPJ com endereço em AL, `numeroCaceal: null` e `situacaoCadastralContribuinte:
    # null`, só com o `situacaoCadastralPj`. Não são IE e não entram na regra.
    resultado["inscricoesEstaduaisAl"] = [
        ie
        for ie in (_normalizar_contribuinte(i) for i in contribuintes)
        if ie["numeroCaceal"]
    ]

    if not resultado["inscricoesEstaduaisAl"]:
        resultado["active"] = True
        resultado["motivo"] = (
            f"Sem inscrição estadual em {UF_ALVO} (isento); situação cadastral Ativa"
        )
        return resultado

    ativas = [i for i in resultado["inscricoesEstaduaisAl"] if i["ativo"]]
    if ativas:
        numeros = ", ".join(str(i["inscricaoEstadual"]) for i in ativas)
        resultado["active"] = True
        resultado["motivo"] = (
            f"Situação cadastral Ativa e IE ativa na SEFAZ/{UF_ALVO} ({numeros})"
        )
        return resultado

    # IE com CACEAL mas sem situação: a SEFAZ não informou o dado. Reprovar por ausência
    # de informação seria tratar falha de cadastro como irregularidade — fica indefinido.
    sem_situacao = [i for i in resultado["inscricoesEstaduaisAl"] if not i["situacaoCadastral"]]
    if sem_situacao:
        numeros = ", ".join(str(i["inscricaoEstadual"]) for i in sem_situacao)
        resultado["active"] = None
        resultado["motivo"] = (
            f"SEFAZ/{UF_ALVO} não informou a situação da(s) inscrição(ões) {numeros}"
        )
        return resultado

    situacoes = ", ".join(
        f"{i['inscricaoEstadual']}={i['situacaoCadastral']}"
        + (f" ({i['motivo'].strip()})" if i.get("motivo") else "")
        for i in resultado["inscricoesEstaduaisAl"]
    )
    resultado["active"] = False
    resultado["motivo"] = f"Nenhuma IE de {UF_ALVO} ativa na SEFAZ ({situacoes})"
    return resultado


# --------------------------------------------------------------------------------------
# Rotas
# --------------------------------------------------------------------------------------

def _avaliar_entrada(bruto):
    """Valida e avalia um CNPJ cru (com ou sem pontuação). Sempre devolve um resultado."""
    limpo = limpar_cnpj(bruto)
    if not limpo:
        return {
            "cnpj": str(bruto) if bruto is not None else None,
            "active": None,
            "motivo": ERRO_CNPJ_INVALIDO,
        }
    return avaliar_cnpj(limpo)


def _com_tratamento_de_erro(funcao):
    """Executa `funcao` convertendo as exceções nos erros HTTP padrão do módulo."""
    try:
        return funcao()
    except RuntimeError as err:
        print("Erro de configuração CNPJ:", err)
        return jsonify({"erro": str(err)}), 500
    except requests.RequestException as err:
        print("Erro HTTP consulta CNPJ:", err)
        return jsonify({"erro": f"Falha de comunicação com a fonte externa: {err}"}), 502
    except Exception as err:
        print("Erro Geral:", err)
        return jsonify({"erro": str(err)}), 500


@bp.route("/api/consultar-cnpj/<path:cnpj>", methods=["GET"])
def consultar_cnpj_get(cnpj):
    """Mesma consulta da rota POST, com o CNPJ na URL — para um único CNPJ.

    GET /api/consultar-cnpj/12014916000180

    O conversor é `path:` (e não o `string:` padrão) de propósito: a máscara do CNPJ tem
    uma barra (`12.014.916/0001-80`) e o conversor padrão não casa `/`, o que devolvia
    404 para o formato pontuado. Para lote, use o POST.
    """
    def executar():
        resultado = _avaliar_entrada(cnpj)
        if resultado.get("motivo") == ERRO_CNPJ_INVALIDO:
            return jsonify({"erro": ERRO_CNPJ_INVALIDO}), 400
        return jsonify({"sucesso": True, **resultado})

    return _com_tratamento_de_erro(executar)


@bp.route("/api/consultar-cnpj", methods=["POST"])
def consultar_cnpj():
    """Consulta um CNPJ (ou uma lista) e devolve o `active` conforme a regra de AL.

    Body (um dos dois):
        { "cnpj": "12014916000180" }
        { "cnpjs": ["12014916000180", "..."] }

    Resposta (registro único):
        { "sucesso": true, "cnpj": "...", "active": false, "motivo": "...", ... }

    Resposta (lista):
        { "sucesso": true, "totalRegistros": 2, "dados": [ {...}, {...} ] }

    `active` vem `null` quando a cnpj.ws ou a SEFAZ não responderam — nesse caso o
    `motivo` diz qual fonte falhou e a consulta pode ser repetida depois.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"erro": "Body JSON é obrigatório."}), 400

    if data.get("cnpjs") is not None:
        entrada = data.get("cnpjs")
        if not isinstance(entrada, list):
            return jsonify({"erro": "Parâmetro 'cnpjs' deve ser uma lista."}), 400
        if not entrada:
            return jsonify({"erro": "Parâmetro 'cnpjs' está vazio."}), 400
        if len(entrada) > _MAX_LOTE:
            return jsonify(
                {"erro": f"Máximo de {_MAX_LOTE} CNPJs por requisição (recebidos {len(entrada)})."}
            ), 400
        lote = True
    elif data.get("cnpj") is not None:
        entrada = [data.get("cnpj")]
        lote = False
    else:
        return jsonify({"erro": "Informe 'cnpj' (único) ou 'cnpjs' (lista)."}), 400

    def executar():
        resultados = []
        for i, bruto in enumerate(entrada):
            resultado = _avaliar_entrada(bruto)
            resultados.append(resultado)

            # Pausa entre consultas do lote para respeitar o rate limit da cnpj.ws.
            # CNPJ inválido não chega a chamar a API, então não precisa de pausa.
            consultou = resultado.get("motivo") != ERRO_CNPJ_INVALIDO
            if lote and consultou and i < len(entrada) - 1 and _DELAY_LOTE > 0:
                time.sleep(_DELAY_LOTE)

        if lote:
            return jsonify({
                "sucesso": True,
                "totalRegistros": len(resultados),
                "dados": resultados,
            })

        unico = resultados[0]
        # Num CNPJ só, entrada inválida é erro do chamador (400), não um resultado.
        if unico.get("motivo") == ERRO_CNPJ_INVALIDO:
            return jsonify({"erro": ERRO_CNPJ_INVALIDO}), 400
        return jsonify({"sucesso": True, **unico})

    return _com_tratamento_de_erro(executar)
