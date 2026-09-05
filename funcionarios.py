"""Rotas do módulo de Funcionários (folha de pagamento do Sankhya).

Arquivo novo em vez de mais um bloco no app.py: o monolito já serve produtos,
logística, estoque e conferência, e a folha é um domínio à parte (tabelas TFP*).

Fonte: a consulta validada com o RH (`docs/consulta_tfpfun.txt`, pasta não
versionada). O SELECT abaixo é o mesmo, com `CODFUNC` acrescentado (é a chave do
funcionário junto com `CODEMP`; sem ele o consumidor não tem como identificar o
registro) e os filtros opcionais aplicados por fora, sempre em bind variables.
"""

import re

from flask import Blueprint, jsonify, request

from db import conectar_oracle

bp = Blueprint("funcionarios", __name__)

# ---------------------------------------------------------------------------
# Regra de AFASTADO (TFPHIS.AFASTAMENTO)
#
# O funcionário está afastado quando tem uma ocorrência (TFPOCO) vigente hoje
# cujo histórico (TFPHIS) é de afastamento. São dois grupos de códigos:
#
#   - afastamento de fato ('A','D','G','M','S','Y','W','2','9'): vale sozinho.
#   - códigos condicionais ('E','J','K','O','P','R','T','U','V','Z','5','7'):
#     só contam quando o histórico está marcado com REDUZDIASTRAB = 'S'. Sem
#     essa marca são ocorrências que não tiram o funcionário do trabalho.
#
# Ocorrência vigente = DTINICOCOR <= hoje e (DTFINALOCOR nula ou >= hoje).
# SITUACAO <> '1' na TFPFUN tem precedência: quem não está na situação 1 sai
# como INATIVO, mesmo com ocorrência aberta.
#
# As duas listas são fixas no SQL — ver "Constantes hardcoded" no README.
# ---------------------------------------------------------------------------

SELECT_FUNCIONARIOS = """
SELECT * FROM (
    SELECT
        F.CODFUNC                            AS COD_FUNC,
        F.NOMEFUNC                           AS NOME,
        F.CPF                                AS CPF,
        F.MATRICULA                          AS MATRICULA,

        F.CODEMP                             AS COD_EMPRESA,
        EMP.RAZAOSOCIAL                      AS EMPRESA_FILIAL,

        CASE
            WHEN F.SITUACAO <> '1' THEN
                'INATIVO'

            WHEN EXISTS (
                SELECT 1
                  FROM TFPOCO OCO
                  JOIN TFPHIS HIS
                    ON HIS.CODHISTOCOR = OCO.CODHISTOCOR
                 WHERE OCO.CODEMP  = F.CODEMP
                   AND OCO.CODFUNC = F.CODFUNC
                   AND OCO.DTINICOCOR <= TRUNC(SYSDATE)
                   AND (
                        OCO.DTFINALOCOR IS NULL
                        OR OCO.DTFINALOCOR >= TRUNC(SYSDATE)
                   )
                   AND (
                        HIS.AFASTAMENTO IN (
                            'A','D','G','M','S','Y','W','2','9'
                        )
                        OR (
                            HIS.AFASTAMENTO IN (
                                'E','J','K','O','P','R','T','U','V','Z','5','7'
                            )
                            AND HIS.REDUZDIASTRAB = 'S'
                        )
                   )
            ) THEN
                'AFASTADO'

            ELSE
                'ATIVO'
        END                                  AS STATUS,

        F.DTADM                              AS DATA_ADMISSAO,
        F.DTDEM                              AS DATA_DEMISSAO,

        F.CODCARGO                           AS COD_CARGO,
        CAR.DESCRCARGO                       AS CARGO,

        F.CODDEP                             AS COD_SETOR,
        DEP.DESCRDEP                         AS SETOR,

        F.CODCARGAHOR                        AS COD_JORNADA,
        CGH.DESCRCARGAHOR                    AS JORNADA,
        F.HORASSEM                           AS HORAS_SEMANAIS,

        F.SALBASE                            AS SALARIO_BASE,

        CAST(NULL AS DATE)                   AS DATA_VIGENCIA_SALARIO,

        F.DTALTER                            AS ULTIMA_ATUALIZACAO

    FROM TFPFUN F

    LEFT JOIN TSIEMP EMP
           ON EMP.CODEMP = F.CODEMP

    LEFT JOIN TFPCAR CAR
           ON CAR.CODCARGO = F.CODCARGO

    LEFT JOIN TFPDEP DEP
           ON DEP.CODDEP = F.CODDEP

    LEFT JOIN TFPCGH CGH
           ON CGH.CODCARGAHOR = F.CODCARGAHOR
)
"""

ORDEM = " ORDER BY COD_EMPRESA, NOME"

STATUS_VALIDOS = ("ATIVO", "AFASTADO", "INATIVO")

# Filtros numéricos aceitos na querystring -> coluna da consulta.
FILTROS_NUMERICOS = (
    ("codEmp", "COD_EMPRESA"),
    ("codFunc", "COD_FUNC"),
    ("codDep", "COD_SETOR"),
    ("codCargo", "COD_CARGO"),
)


def _erro(err, codigo=500):
    print("Erro:", err)
    return jsonify({"erro": str(err)}), codigo


def _txt(valor):
    """Texto do banco: espaços em branco viram None (mesma razão da cobrança)."""
    if valor is None:
        return None
    limpo = str(valor).strip()
    return limpo or None


def _cpf(valor):
    """CPF pode vir como NUMBER, e aí os zeros à esquerda somem no caminho."""
    texto = _txt(valor)
    if texto is None:
        return None
    return texto.zfill(11) if texto.isdigit() else texto


def _data(valor):
    return valor.strftime("%Y-%m-%d") if valor else None


def _num(valor):
    return float(valor) if valor is not None else None


def _int(valor):
    return int(valor) if valor is not None else None


def _inteiro(nome):
    """Lê um filtro numérico da querystring. Devolve (valor, mensagem de erro)."""
    bruto = request.args.get(nome)
    if bruto in (None, ""):
        return None, None
    try:
        return int(bruto), None
    except ValueError:
        return None, f"Parâmetro '{nome}' deve ser um número inteiro."


@bp.route("/api/funcionarios", methods=["GET"])
def funcionarios():
    filtros = []
    binds = {}

    for nome_param, coluna in FILTROS_NUMERICOS:
        valor, erro = _inteiro(nome_param)
        if erro:
            return jsonify({"erro": erro}), 400
        if valor is not None:
            filtros.append(f"{coluna} = :{nome_param.upper()}")
            binds[nome_param.upper()] = valor

    status = (request.args.get("status") or "").strip().upper()
    if status:
        if status not in STATUS_VALIDOS:
            return (
                jsonify(
                    {"erro": "Parâmetro 'status' deve ser ATIVO, AFASTADO ou INATIVO."}
                ),
                400,
            )
        filtros.append("STATUS = :STATUS")
        binds["STATUS"] = status

    busca = (request.args.get("busca") or "").strip()
    if busca:
        # TO_CHAR em CPF/MATRICULA: nas bases da folha esses campos aparecem ora
        # como texto, ora como número — sem o cast o LIKE depende de conversão
        # implícita (e do NLS) para funcionar.
        condicoes = [
            "UPPER(NOME) LIKE '%' || UPPER(:BUSCA) || '%'",
            "UPPER(TO_CHAR(MATRICULA)) LIKE '%' || UPPER(:BUSCA) || '%'",
        ]
        binds["BUSCA"] = busca

        # CPF é comparado só por dígito, dos dois lados:
        #   - na coluna, porque o CPF pode estar como NUMBER (e aí o zero à
        #     esquerda some no TO_CHAR) ou como texto com máscara;
        #   - no termo digitado, porque quem busca costuma colar o CPF pontuado.
        # Sem isso, procurar "01234567890" não achava o funcionário cujo `cpf`
        # a própria resposta devolve como "01234567890".
        digitos = re.sub(r"\D", "", busca)
        if digitos:
            condicoes.insert(
                1,
                "LPAD(REGEXP_REPLACE(TO_CHAR(CPF), '[^0-9]'), 11, '0')"
                " LIKE '%' || :BUSCACPF || '%'",
            )
            binds["BUSCACPF"] = digitos

        filtros.append("(" + " OR ".join(condicoes) + ")")

    sql = SELECT_FUNCIONARIOS
    if filtros:
        sql += " WHERE " + " AND ".join(filtros)
    sql += ORDEM

    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500

        cursor = conexao.cursor()
        cursor.execute(sql, binds)
        dados = [
            {
                "codFunc": _int(r[0]),
                "nome": _txt(r[1]),
                "cpf": _cpf(r[2]),
                "matricula": _txt(r[3]),
                "codEmpresa": _int(r[4]),
                "empresa": _txt(r[5]),
                "status": _txt(r[6]),
                "dataAdmissao": _data(r[7]),
                "dataDemissao": _data(r[8]),
                "codCargo": _int(r[9]),
                "cargo": _txt(r[10]),
                "codSetor": _int(r[11]),
                "setor": _txt(r[12]),
                "codJornada": _int(r[13]),
                "jornada": _txt(r[14]),
                "horasSemanais": _num(r[15]),
                "salarioBase": _num(r[16]),
                # Sempre null: a vigência do salário mora no histórico salarial,
                # que ainda não foi mapeado. O campo existe para o consumidor não
                # precisar trocar de contrato quando ele entrar.
                "dataVigenciaSalario": _data(r[17]),
                "ultimaAtualizacao": _data(r[18]),
            }
            for r in cursor.fetchall()
        ]
        return jsonify({"sucesso": True, "totalRegistros": len(dados), "dados": dados})
    except Exception as e:
        return _erro(e)
    finally:
        if conexao:
            conexao.close()
