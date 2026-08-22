"""Consulta de CNPJ e decisão de `active` para contribuintes de Alagoas.

Combina duas fontes externas:

1. **cnpj.ws** (paga, token por header `x_api_token`) — situação cadastral na Receita
   Federal e a lista de inscrições estaduais com a flag `ativo`.
2. **SEFAZ/AL** (pública, sem autenticação) — situação cadastral da inscrição estadual
   (CACEAL), consultada só quando a cnpj.ws diz que nenhuma IE de AL está ativa.

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
SEFAZ_AL_URL = "https://cadastro.sefaz.al.gov.br/sfz-cadastro-api/api/contribuinte/obterDadosFic/"

# Timeout (segundos) das chamadas HTTP externas.
_TIMEOUT = 30

# Tentativas em caso de 429 (rate limit da cnpj.ws) ou 5xx.
_MAX_TENTATIVAS = 4

# Pausa entre CNPJs num lote, para respeitar o rate limit da cnpj.ws.
_DELAY_LOTE = float(os.environ.get("CNPJWS_DELAY", "0.3"))

# Teto de CNPJs por requisição: cada um pode gastar 1 chamada na cnpj.ws + N na SEFAZ,
# então um lote grande demais estouraria o timeout do cliente HTTP.
_MAX_LOTE = 100

# Situação da Receita que aprova o CNPJ. Comparada sem acento/caixa.
_SITUACAO_APROVADA = "ativa"

# Situação da SEFAZ/AL que reprova a inscrição estadual.
_SITUACAO_SEFAZ_REPROVADA = "INAPTO"

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


def numero_caceal(inscricao_estadual):
    """Converte a IE de AL no número que a SEFAZ espera na URL.

    A cnpj.ws devolve a inscrição completa com 9 dígitos (ex.: "240987047"), mas o
    endpoint da SEFAZ usa só o CACEAL de 8 dígitos, SEM o dígito verificador
    ("24098704") — com os 9 dígitos ele responde 404. Devolve None se não der para
    normalizar.
    """
    if not inscricao_estadual:
        return None
    digitos = re.sub(r"\D", "", str(inscricao_estadual))
    if len(digitos) == 9:
        return digitos[:8]
    if len(digitos) == 8:
        return digitos
    return None


def consultar_sefaz_al(inscricao_estadual):
    """Consulta a situação da IE na SEFAZ/AL.

    Devolve (info, erro) — um dos dois é sempre None. `info` traz
    {"situacaoCadastral", "motivo", "dataAlteracao"}.
    """
    caceal = numero_caceal(inscricao_estadual)
    if not caceal:
        return None, f"Inscrição estadual '{inscricao_estadual}' fora do formato de AL"

    tentativa = 0
    while True:
        tentativa += 1
        try:
            resp = requests.get(SEFAZ_AL_URL + caceal, timeout=_TIMEOUT)
        except requests.RequestException as err:
            if tentativa < _MAX_TENTATIVAS:
                time.sleep(2 * tentativa)
                continue
            return None, f"Falha de conexão com a SEFAZ/AL: {err}"

        if resp.status_code == 200:
            try:
                dados = resp.json()
            except ValueError:
                return None, f"SEFAZ/AL devolveu corpo não-JSON: {resp.text[:200]}"
            recente = dados.get("situacaoCadastralRecente") or {}
            return {
                "situacaoCadastral": recente.get("situacaoCadastral"),
                "motivo": dados.get("motivoSituacaoCadastral"),
                "dataAlteracao": recente.get("dataAlteracaoSituacaoCadastral"),
            }, None

        if resp.status_code == 404:
            return None, f"Inscrição estadual {inscricao_estadual} não encontrada na SEFAZ/AL"

        if resp.status_code >= 500 and tentativa < _MAX_TENTATIVAS:
            time.sleep(2 * tentativa)
            continue

        return None, f"SEFAZ/AL HTTP {resp.status_code}: {resp.text[:200]}"


# --------------------------------------------------------------------------------------
# Regra de negócio
# --------------------------------------------------------------------------------------

def _inscricoes_al(dados):
    """Filtra as inscrições estaduais de AL do payload da cnpj.ws.

    Inscrições de outros estados são ignoradas de propósito: a regra só olha AL.
    """
    est = dados.get("estabelecimento") or {}
    return [
        item
        for item in (est.get("inscricoes_estaduais") or [])
        if ((item.get("estado") or {}).get("sigla") or "").upper() == UF_ALVO
    ]


def avaliar_cnpj(cnpj):
    """Aplica a regra de `active` a um CNPJ já validado (14 dígitos).

    Regra:
    1. `situacao_cadastral` na Receita precisa ser "Ativa" — se não for, reprova na hora
       e nem consulta a SEFAZ.
    2. Filtra as inscrições estaduais de AL (as de outros estados são ignoradas).
       - Nenhuma IE em AL (isento): aprova só pela situação cadastral.
       - Pelo menos uma IE com `ativo = true`: aprova.
       - Nenhuma ativa: consulta cada IE na SEFAZ/AL. Basta UMA "INAPTO" para reprovar
         (e aí para de consultar as demais).

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

    # 2. Inscrições estaduais de AL.
    inscricoes = _inscricoes_al(dados)
    resultado["inscricoesEstaduaisAl"] = [
        {"inscricaoEstadual": i.get("inscricao_estadual"), "ativo": bool(i.get("ativo"))}
        for i in inscricoes
    ]

    if not inscricoes:
        resultado["active"] = True
        resultado["motivo"] = "Sem inscrição estadual em AL (isento); situação cadastral Ativa"
        return resultado

    ativas = [i for i in inscricoes if i.get("ativo")]
    if ativas:
        numeros = ", ".join(str(i.get("inscricao_estadual")) for i in ativas)
        resultado["active"] = True
        resultado["motivo"] = f"Situação cadastral Ativa e IE ativa em AL ({numeros})"
        return resultado

    # Nenhuma IE ativa na cnpj.ws: a SEFAZ dá a palavra final, uma a uma.
    indeterminadas = []
    for item in resultado["inscricoesEstaduaisAl"]:
        info, erro_sefaz = consultar_sefaz_al(item["inscricaoEstadual"])
        if erro_sefaz:
            item["sefaz"] = {"erro": erro_sefaz}
            indeterminadas.append(f"{item['inscricaoEstadual']} ({erro_sefaz})")
            continue

        item["sefaz"] = info
        if (info.get("situacaoCadastral") or "").strip().upper() == _SITUACAO_SEFAZ_REPROVADA:
            # Uma INAPTO já reprova — não consulta as demais.
            motivo_ie = info.get("motivo")
            resultado["active"] = False
            resultado["motivo"] = (
                f"IE {item['inscricaoEstadual']} (AL) está INAPTO na SEFAZ"
                + (f" - {motivo_ie.strip()}" if motivo_ie else "")
            )
            return resultado

    if indeterminadas:
        resultado["active"] = None
        resultado["motivo"] = (
            "Não foi possível confirmar a situação na SEFAZ/AL de: "
            + "; ".join(indeterminadas)
        )
        return resultado

    situacoes = ", ".join(
        f"{i['inscricaoEstadual']}={(i.get('sefaz') or {}).get('situacaoCadastral')}"
        for i in resultado["inscricoesEstaduaisAl"]
    )
    resultado["active"] = True
    resultado["motivo"] = f"Situação cadastral Ativa e nenhuma IE de AL INAPTO na SEFAZ ({situacoes})"
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
