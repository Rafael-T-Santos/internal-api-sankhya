import os
import cx_Oracle
from flask import Flask, request, jsonify
from flask_cors import CORS

# --- Configuração da API ---
app = Flask(__name__)
# Permite que o frontend (localhost:8080) acesse a API (localhost:5000)
CORS(app)

# --- Conexão com Oracle (Usando Variáveis de Ambiente) ---
def conectar_oracle():
    try:
        # Pega as credenciais das Variáveis de Ambiente do Windows
        user = os.environ.get("DB_USER")
        password = os.environ.get("DB_PASS")
        dsn_str = os.environ.get("DB_DSN") # Ex: "192.168.255.250:1521/xe"

        if not all([user, password, dsn_str]):
            raise ValueError("Credenciais do banco de dados (DB_USER, DB_PASS, DB_DSN) não configuradas nas variáveis de ambiente.")

        print("Tentando conectar ao Oracle DSN:", dsn_str)
        conexao = cx_Oracle.connect(user=user, password=password, dsn=dsn_str)
        print("Conexão com Oracle bem-sucedida!")
        return conexao
    except cx_Oracle.Error as err:
        print("Erro ao conectar ao Oracle:", err)
        return None
    except ValueError as err:
        print("Erro de configuração:", err)
        return None

# --- Endpoints da API ---

@app.route("/api/verificar-produto", methods=["POST"])
def verificar_produto():
    data = request.get_json()
    if not data:
        return jsonify({"erro": "Nenhum dado recebido"}), 400

    # 1. Extração dos Dados
    ingredientes_busca = []

    # A. Processa Pigmentos (Critério: Código + Quantidade)
    lista_pigmentos = data.get("pigmentos", [])
    for p in lista_pigmentos:
        ingredientes_busca.append({
            "cod": p.get("codigo"),
            "qtd": p.get("quantidade", 0),
            "ignorar_qtd": False # Pigmento precisa bater a quantidade
        })

    # B. Processa Base (Critério: Apenas Código)
    dados_base = data.get("base", {})
    if dados_base and dados_base.get("codigo"):
        ingredientes_busca.append({
            "cod": dados_base.get("codigo"),
            "qtd": None, # Não importa a quantidade
            "ignorar_qtd": True # Flag para o SQL
        })

    qtd_total_ingredientes = len(ingredientes_busca)

    if qtd_total_ingredientes == 0:
        return jsonify({"erro": "Nenhum componente informado."}), 400

    # 2. Construção Dinâmica da SQL
    unions = []
    params = {}
    input_sizes = {}

    for i, item in enumerate(ingredientes_busca):
        key_cod = f"COD_{i}"
        key_qtd = f"QTD_{i}"
        
        # Monta o SELECT (Coluna QTD_ALVO será NULL para a base)
        unions.append(f"""
            SELECT 
                :{key_cod} AS CODMAT, 
                TO_NUMBER(:{key_qtd}, '999999D999999', 'NLS_NUMERIC_CHARACTERS=,.') AS QTD_ALVO
            FROM DUAL
        """)
        
        # Define parâmetros
        params[key_cod] = item["cod"]
        
        if item["ignorar_qtd"]:
            params[key_qtd] = None # Base envia NULL
        else:
            params[key_qtd] = str(item["qtd"]).replace(".", ",") # Pigmento envia valor
        
        # Define tipagem
        input_sizes[key_cod] = cx_Oracle.NUMBER
        input_sizes[key_qtd] = cx_Oracle.STRING

    sql_alvo = " UNION ALL ".join(unions)

    # A Query Final
    sql_final = f"""
    WITH ALVO AS (
        {sql_alvo}
    ),
    MATCHES AS (
        SELECT 
            c.CODPROD AS CODPROD_PAI,
            COUNT(*) AS CONTAGEM_MATCH
        FROM TGFICP c
        JOIN ALVO a ON c.CODMATPRIMA = a.CODMAT
        WHERE 
           -- LÓGICA DE MATCH:
           -- 1. Se QTD_ALVO for NULL (Base), aceita qualquer quantidade (sem filtro extra)
           -- 2. Se QTD_ALVO tiver valor (Pigmento), compara com margem de erro
           (a.QTD_ALVO IS NULL OR ABS(c.QTDMISTURA - a.QTD_ALVO) < 0.001)
        GROUP BY c.CODPROD
    ),
    CONTAGEM_DO_PRODUTO AS (
        -- Garante que o produto não tenha ingredientes a mais
        SELECT CODPROD, COUNT(*) AS QTD_REAL_INGREDIENTES
        FROM TGFICP
        WHERE CODPROD IN (SELECT CODPROD_PAI FROM MATCHES)
        GROUP BY CODPROD
    )
    SELECT p.CODPROD, p.DESCRPROD
    FROM MATCHES m
    JOIN CONTAGEM_DO_PRODUTO cp ON cp.CODPROD = m.CODPROD_PAI
    JOIN TGFPRO p ON p.CODPROD = m.CODPROD_PAI
    WHERE m.CONTAGEM_MATCH = :TOTAL_ITENS_ENVIO
      AND cp.QTD_REAL_INGREDIENTES = :TOTAL_ITENS_ENVIO
    """

    params["TOTAL_ITENS_ENVIO"] = qtd_total_ingredientes
    input_sizes["TOTAL_ITENS_ENVIO"] = cx_Oracle.NUMBER

    # 3. Execução
    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha DB"}), 500

        cursor = conexao.cursor()
        cursor.setinputsizes(**input_sizes)
        
        cursor.execute(sql_final, params)
        resultado = cursor.fetchone()

        if resultado:
            return jsonify({
                "cadastrada": True,
                "codigoProduto": str(resultado[0]),
                "nomeProduto": resultado[1]
            })
        else:
            return jsonify({"cadastrada": False})

    except cx_Oracle.Error as err:
        print("Erro Oracle:", err)
        return jsonify({"erro": f"Erro SQL: {err}"}), 500
    except Exception as e:
        print("Erro Geral:", e)
        return jsonify({"erro": str(e)}), 500
    finally:
        if conexao:
            conexao.close()

@app.route("/api/cadastrar-produto", methods=["POST"])
def cadastrar_produto():
    data = request.get_json()
    if not data:
        return jsonify({"erro": "Nenhum dado recebido"}), 400

    # --- 1. Preparação dos Dados do Produto Pai ---
    
    cor_nome = data.get("cor", {}).get("nome", "").strip()
    base_nome = data.get("base", {}).get("nome", "").strip()
    tamanho_nome = data.get("tamanho", {}).get("nome", "").strip()
    
    descr_prod = f"TINTA {base_nome} {tamanho_nome} {cor_nome} IQUINE".upper()
    descr_prod = descr_prod[:100] 

    obj_tamanho = data.get("tamanho", {})
    cod_vol = obj_tamanho.get("codVol", "UN") 
    
    try:
        # Pega o valor cru (raw)
        raw_litros = obj_tamanho.get("litros", 0)
        
        # 1. str(raw_litros): Garante que virou texto (caso venha número ou None)
        # 2. .replace(',', '.'): Troca a vírgula brasileira pelo ponto americano
        # 3. float(...): Converte finalmente para número decimal
        ad_litros = float(str(raw_litros).replace(',', '.'))
    except Exception as e:
        ad_litros = 0

    # --- 2. Preparação da Lista de Componentes (Dinâmica) ---
    lista_componentes = []
    
    # Adiciona a Base (Sequência será gerada no loop)
    cod_base = data.get("base", {}).get("codigo")
    if cod_base:
        lista_componentes.append({"cod": cod_base, "qtd": 1})

    # Adiciona os Pigmentos
    pigmentos = data.get("pigmentos", [])
    for p in pigmentos:
        lista_componentes.append({
            "cod": p.get("codigo"), 
            "qtd": p.get("quantidade", 0)
        })

    # Verifica se tem componentes (opcional, mas bom pra evitar produto vazio)
    if not lista_componentes:
        return jsonify({"erro": "O produto precisa ter pelo menos uma base ou componente."}), 400

    # --- SQL 1: CRIAÇÃO DO PRODUTO (TGFPRO) E IMPOSTOS (TGFPEM, FCP, EPR) ---
    # Removemos a parte dos componentes daqui para fazer separado
    sql_header = """
    DECLARE
        v_descrprod     TGFPRO.DESCRPROD%TYPE := :DESCRPROD;
        v_codvol_novo   TGFPRO.CODVOL%TYPE    := :CODVOL;
        v_ad_litros_novo NUMBER               := :AD_LITROS;
        
        v_codprod_base   CONSTANT NUMBER := 11783; 
        v_codprod        TGFPRO.CODPROD%TYPE;

        -- Variáveis auxiliares para SQL Dinâmico
        v_cols_list VARCHAR2(4000);
        v_sel_list  VARCHAR2(4000);
    BEGIN
        -- 1) Gera CODPROD
        SELECT ULTCOD + 1 INTO v_codprod FROM TGFNUM 
        WHERE ARQUIVO = 'TGFPRO' AND CODEMP = 1 FOR UPDATE;
        
        UPDATE TGFNUM SET ULTCOD = v_codprod WHERE ARQUIVO = 'TGFPRO' AND CODEMP = 1;

        -- 2) Insert TGFPRO
        INSERT INTO TGFPRO (
            CODPROD, DESCRPROD, REFERENCIA, CODGRUPOPROD, CODVOL, MARCA, MARGLUCRO, DECVLR, DECQTD, PESOBRUTO, PESOLIQ, ESTMIN, ALERTAESTMIN, PROMOCAO, USOPROD, ATIVO, TEMICMS, NATBCPISCOFINS, 
            CODLOCALPADRAO, USALOCAL,
            PERMCOMPPROD, CODCONFKIT, TIPOKIT, CODESPECST, CODMARCA, CODCTACTBEFD, CALCDIFAL, NCM, GRUPOPIS, GRUPOCOFINS, GRUPOCSSL, CSTIPIENT, CSTIPISAI, REFFORN, GRUPODESCPROD, AD_PERCCOMINTERNO, AD_MOBILIDADE, AD_MARGLUCRORTR, AD_HABEMP3, AD_MARGLUCROVRJ, AD_MARGLUCROVR, AD_LITROS, AD_RETIRAVAREJO, AD_RETIRAATACADO, AD_MEDIDA_ETIQUETA, AD_TIPOPROD, VENCOMPINDIV, TIPLANCNOTA, COMVEND, DESCMAX, TIPGTINNFE, UNIDADE, DTALTER
        )
        WITH base_ref AS (
            SELECT LPAD(NVL(MAX(TO_NUMBER(SUBSTR(referencia, 1, 12))), 299000000000) + 1, 12, '0') AS base12
            FROM tgfpro WHERE REGEXP_LIKE(referencia, '^[0-9]{13}$') AND SUBSTR(referencia, 1, 3) = '299'
        ),
        dig AS (
            SELECT base12,
                   TO_NUMBER(SUBSTR(base12, 1, 1)) d1, TO_NUMBER(SUBSTR(base12, 2, 1)) d2, TO_NUMBER(SUBSTR(base12, 3, 1)) d3,
                   TO_NUMBER(SUBSTR(base12, 4, 1)) d4, TO_NUMBER(SUBSTR(base12, 5, 1)) d5, TO_NUMBER(SUBSTR(base12, 6, 1)) d6,
                   TO_NUMBER(SUBSTR(base12, 7, 1)) d7, TO_NUMBER(SUBSTR(base12, 8, 1)) d8, TO_NUMBER(SUBSTR(base12, 9, 1)) d9,
                   TO_NUMBER(SUBSTR(base12, 10, 1)) d10, TO_NUMBER(SUBSTR(base12, 11, 1)) d11, TO_NUMBER(SUBSTR(base12, 12, 1)) d12
            FROM base_ref
        ),
        soma AS (
            SELECT base12, (d1 + d3 + d5 + d7 + d9 + d11) + 3 * (d2 + d4 + d6 + d8 + d10 + d12) AS soma_total FROM dig
        ),
        cand AS (
            SELECT base12, MOD(MOD(10 - MOD(soma_total, 10), 10) + 1, 10) AS dv_invalido FROM soma
        ),
        ref_final AS (
            SELECT base12 || dv_invalido AS prox_referencia FROM cand
        )
        SELECT 
            v_codprod, v_descrprod, rf.prox_referencia,
            p.CODGRUPOPROD, v_codvol_novo, p.MARCA, p.MARGLUCRO, p.DECVLR, p.DECQTD, p.PESOBRUTO, p.PESOLIQ, p.ESTMIN, p.ALERTAESTMIN, p.PROMOCAO, p.USOPROD, p.ATIVO, p.TEMICMS, p.NATBCPISCOFINS, 
            p.CODLOCALPADRAO, p.USALOCAL,
            p.PERMCOMPPROD, p.CODCONFKIT, p.TIPOKIT, p.CODESPECST, p.CODMARCA, p.CODCTACTBEFD, p.CALCDIFAL, p.NCM, p.GRUPOPIS, p.GRUPOCOFINS, p.GRUPOCSSL, -1, -1,
            (SELECT LPAD(NVL(MAX(TO_NUMBER(r.REFFORN)), 0) + 1, 3, '0') FROM TGFPRO r WHERE r.MARCA = p.MARCA AND REGEXP_LIKE(r.REFFORN, '^[0-9]+$')),
            p.GRUPODESCPROD, 0.5, 'N', p.AD_MARGLUCRORTR, p.AD_HABEMP3, p.AD_MARGLUCROVRJ, p.AD_MARGLUCROVR, v_ad_litros_novo, 
            'S', -- <--- AD_RETIRAVAREJO agora é fixo 'S'
            'S', -- <--- AD_RETIRAATACADO agora é fixo 'S'
            p.AD_MEDIDA_ETIQUETA, p.AD_TIPOPROD, 'S', 'Q', 3, 100, 0, 'CM', SYSDATE
        FROM TGFPRO p CROSS JOIN ref_final rf WHERE p.CODPROD = v_codprod_base;

        -- 3) Insert TGFPEM
        INSERT INTO TGFPEM (
            CODEMP, CODPROD, GRUPOICMS, TEMICMS, TIPSUBST, USOPROD, QTDCST, DIASCST, PERCTOLVARCST, USAIDPALETE, CAT1799SPRES, TEMIPICOMPRA, TEMIPIVENDA, USALOTEDTFAB, USALOTEDTVAL, PERCCMTNAC, PERCCMTFED, PERCCMTEST, PERCCMTIMP, CODESPECST, CODCTACTBEFD, CODLOCALPAD, CALCDIFAL
        )
        SELECT CODEMP, v_codprod, GRUPOICMS, TEMICMS, TIPSUBST, USOPROD, QTDCST, DIASCST, PERCTOLVARCST, USAIDPALETE, CAT1799SPRES, TEMIPICOMPRA, TEMIPIVENDA, USALOTEDTFAB, USALOTEDTVAL, PERCCMTNAC, PERCCMTFED, PERCCMTEST, PERCCMTIMP, CODESPECST, CODCTACTBEFD, CODLOCALPAD, CALCDIFAL
        FROM TGFPEM WHERE CODPROD = v_codprod_base;

        -- 4) CLONAGEM TRIBUTÁRIA CRÍTICA
        -- TGFFCP
        SELECT LISTAGG(COLUMN_NAME, ',') WITHIN GROUP (ORDER BY COLUMN_ID),
               LISTAGG(CASE WHEN COLUMN_NAME = 'CODPROD' THEN ':1' ELSE COLUMN_NAME END, ',') WITHIN GROUP (ORDER BY COLUMN_ID)
        INTO v_cols_list, v_sel_list
        FROM ALL_TAB_COLUMNS WHERE TABLE_NAME = 'TGFFCP' AND OWNER = USER; 
        
        IF v_cols_list IS NULL THEN RAISE_APPLICATION_ERROR(-20001, 'Erro: Colunas TGFFCP não encontradas'); END IF;
        EXECUTE IMMEDIATE 'INSERT INTO TGFFCP (' || v_cols_list || ') SELECT ' || v_sel_list || ' FROM TGFFCP WHERE CODPROD = :2' USING v_codprod, v_codprod_base;

        -- TGFEPR
        SELECT LISTAGG(COLUMN_NAME, ',') WITHIN GROUP (ORDER BY COLUMN_ID),
               LISTAGG(CASE WHEN COLUMN_NAME = 'CODPROD' THEN ':1' ELSE COLUMN_NAME END, ',') WITHIN GROUP (ORDER BY COLUMN_ID)
        INTO v_cols_list, v_sel_list
        FROM ALL_TAB_COLUMNS WHERE TABLE_NAME = 'TGFEPR' AND OWNER = USER;

        IF v_cols_list IS NULL THEN RAISE_APPLICATION_ERROR(-20002, 'Erro: Colunas TGFEPR não encontradas'); END IF;
        EXECUTE IMMEDIATE 'INSERT INTO TGFEPR (' || v_cols_list || ') SELECT ' || v_sel_list || ' FROM TGFEPR WHERE CODPROD = :2' USING v_codprod, v_codprod_base;

        -- 5) Output para o Python
        :OUT_CODPROD := v_codprod;
    END;
    """

    # --- SQL 2: INSERÇÃO DE 1 COMPONENTE (Para ser executado N vezes) ---
    # Traduzimos a PROCEDURE add_comp para um INSERT direto com subselect para CODVOL
    sql_item = """
    INSERT INTO TGFICP (
        CODPROD, VARIACAO, CODLOCAL, CONTROLE, CODETAPA, CODMATPRIMA, QTDMISTURA,
        CODVOL, ATUALESTOQUE, CODLOCALMP, CONTROLEMP, SEQUENCIA, FIXO, OPCIONAL,
        MANTEMQTD, TERCEIROS, TIPTRANSICAO, TRANSICAO, ATUALESTINDIVIDUAL,
        ULOCETPAESTIND, TIPTROCPRODKIT, VARIARCONTROLE
    ) VALUES (
        :CODPROD_PAI, 
        30000, 
        0, 
        ' ', 
        0, 
        :CODMATPRIMA, 
        :QTDMISTURA,
        (SELECT CODVOL FROM TGFPRO WHERE CODPROD = :CODMATPRIMA), -- Busca CODVOL do componente
        'N', 
        31000, 
        ' ', 
        :SEQUENCIA, 
        'N', 'N', 'N', 'N', 'A', 'N', 'N', 'N', 'K', 'N'
    )
    """

    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha de conexão"}), 500

        cursor = conexao.cursor()
        
        # --- PASSO 1: Executa Cabeçalho e Impostos ---
        out_codprod = cursor.var(cx_Oracle.NUMBER)
        params_header = {
            "DESCRPROD": descr_prod,
            "CODVOL": cod_vol,
            "AD_LITROS": ad_litros,
            "OUT_CODPROD": out_codprod
        }
        
        cursor.execute(sql_header, params_header)
        
        # Recupera o ID gerado (ainda não comitado)
        novo_codprod = out_codprod.getvalue()
        if not novo_codprod:
            raise Exception("Erro ao gerar CODPROD: valor retornado nulo.")
        
        novo_codprod = int(novo_codprod) # Garante inteiro

        # --- PASSO 2: Loop para Inserir Componentes ---
        # Itera sobre a lista criada no início, gerando sequencia 1, 2, 3...
        for i, comp in enumerate(lista_componentes, start=1):
            params_item = {
                "CODPROD_PAI": novo_codprod,
                "CODMATPRIMA": comp["cod"],
                "QTDMISTURA": comp["qtd"],
                "SEQUENCIA": i
            }
            cursor.execute(sql_item, params_item)

        # --- PASSO 3: Commit Final ---
        # Se chegou até aqui, o produto e todos os N componentes foram processados sem erro
        conexao.commit()

        return jsonify({
            "sucesso": True,
            "codigo": str(novo_codprod),
            "nomeProduto": descr_prod,
            "mensagem": f"Produto cadastrado com {len(lista_componentes)} componentes."
        })

    except cx_Oracle.Error as err:
        if conexao:
            conexao.rollback() # Limpa tudo se der erro
        error_obj, = err.args
        print("Erro Oracle:", error_obj.message)
        return jsonify({"erro": f"Erro no Banco: {error_obj.message}"}), 500
        
    except Exception as e:
        if conexao:
            conexao.rollback()
        print("Erro Geral:", e)
        return jsonify({"erro": str(e)}), 500
        
    finally:
        if conexao:
            conexao.close()

@app.route("/api/consultar-preco", methods=["POST"])
def consultar_preco():
    data = request.get_json()
    
    # Parâmetros esperados na requisição
    cod_prod = data.get("codProd")
    cod_tabela = data.get("codTabela")   # Ex: 0, 1...
    cobra_st = data.get("cobraST", "N")  # Ex: 'S' ou 'N'

    if not cod_prod or cod_tabela is None:
        return jsonify({"erro": "Parâmetros 'codProd' e 'codTabela' são obrigatórios."}), 400

    # Sua Query exata
    sql = """
    SELECT 
        t.codtab,
        t.dtvigor,
        t.nutab,
        e.codprod,
        CASE 
            WHEN t.codtab = 0 AND :COBRA_ST = 'S' THEN 
                e.vlrvenda +
                (
                  (SNK_PRECO(0, pro.codprod) * (1 + (icm.marglucro/100)) * ((icm.aliqsubtrib/100) + (icm.percstfcpint/100))) 
                  - (SNK_PRECO(0, pro.codprod) * ((icm.aliqsubtrib/100) + (icm.percstfcpint/100)))
                )
            ELSE 
                e.vlrvenda
        END AS vlrvenda_final

    FROM tgftab t
    JOIN tgfexc e ON e.nutab = t.nutab
    
    -- produto
    JOIN tgfpro pro ON pro.codprod = e.codprod
    
    -- pegar GRUPOICMS do produto por empresa
    JOIN tgfpeM pem 
         ON pem.codprod = pro.codprod
        AND pem.codemp  = 3
        
    -- pegar MVA, ALIQST, FCP
    JOIN tgfICM icm
         ON pem.grupoicms       = icm.codrestricao2
        AND pem.codemp         = icm.codrestricao
        AND icm.ufdest         = 19   -- ajuste se precisar

    WHERE e.codprod = :CODPROD
      AND e.vlrvenda IS NOT NULL
      AND t.codtab = :CODTAB
      AND t.dtvigor = (
        SELECT MAX(t2.dtvigor)
        FROM tgftab t2
        JOIN tgfexc e2 ON e2.nutab = t2.nutab
        WHERE t2.codtab = t.codtab
        AND e2.codprod = :CODPROD
        AND e2.vlrvenda IS NOT NULL
)
    """

    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500

        cursor = conexao.cursor()
        
        # Mapeando os parâmetros do JSON para o SQL
        params = {
            "CODPROD": cod_prod,
            "CODTAB": cod_tabela,
            "COBRA_ST": cobra_st
        }

        cursor.execute(sql, params)
        resultado = cursor.fetchone()

        if resultado:
            # resultado[4] é a coluna vlrvenda_final (índice começa em 0)
            preco_final = resultado[4]
            
            return jsonify({
                "sucesso": True,
                "codProd": resultado[3],
                "codTabela": resultado[0],
                "preco": float(preco_final) if preco_final is not None else 0.0
            })
        else:
            return jsonify({
                "sucesso": False, 
                "mensagem": "Preço não encontrado para os parâmetros informados."
            }), 404

    except cx_Oracle.Error as err:
        print("Erro Oracle:", err)
        return jsonify({"erro": f"Erro de Banco de Dados: {err}"}), 500
    except Exception as e:
        print("Erro Geral:", e)
        return jsonify({"erro": str(e)}), 500
    finally:
        if conexao:
            conexao.close()

@app.route("/api/consultar-estoque", methods=["POST"])
def consultar_estoque():
    data = request.get_json()
    
    # Validação do input
    cod_prod = data.get("codProd")
    
    if not cod_prod:
        return jsonify({"erro": "Parâmetro 'codProd' é obrigatório."}), 400

    # SQL conforme solicitado
    sql = """
    SELECT codprod, estoque 
    FROM tgfest 
    WHERE codemp = 1 
      AND codprod = :P_codprod
    """

    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500

        cursor = conexao.cursor()
        
        # Executa a query passando o parâmetro
        cursor.execute(sql, {"P_codprod": cod_prod})
        resultado = cursor.fetchone()

        if resultado:
            return jsonify({
                "sucesso": True,
                "codProd": resultado[0],
                "estoque": float(resultado[1]) # Garante que seja numérico para o JSON
            })
        else:
            # Se não encontrar registro na TGFEST para a empresa 1, 
            # assumimos que o produto existe mas não tem estoque controlado (ou é zero)
            return jsonify({
                "sucesso": True,
                "codProd": cod_prod,
                "estoque": 0.0,
                "mensagem": "Produto não encontrado na tabela de estoque (TGFEST) para a empresa 1."
            })

    except cx_Oracle.Error as err:
        print("Erro Oracle:", err)
        return jsonify({"erro": f"Erro de Banco de Dados: {err}"}), 500
    except Exception as e:
        print("Erro Geral:", e)
        return jsonify({"erro": str(e)}), 500
    finally:
        if conexao:
            conexao.close()

@app.route("/api/consultar-ordem-carga", methods=["POST"])
def consultar_ordem_carga():
    data = request.get_json()
    
    if not data:
        return jsonify({"erro": "Nenhum dado recebido"}), 400

    # Pega o parâmetro ordemCarga do JSON enviado pelo cliente
    ordem_carga = data.get("ordemCarga")
    if not ordem_carga:
        return jsonify({"erro": "Parâmetro 'ordemCarga' é obrigatório."}), 400

    # Deixamos o codEmp dinâmico também (por padrão será 1 se não for enviado)
    cod_emp = data.get("codEmp", 1)

    # Query com as variáveis de Bind (:CODEMP e :ORDEMCARGA)
    sql = """
    WITH base AS (
        SELECT
            cab.ordemcarga,
            cab.codemp,
            emp.razaosocial AS empresa_razao,
            cab.numnota,
            cab.nunota,
            cab.codparc,
            parc_cab.razaosocial AS parceiro_razao,
            cid.nomecid,
            cab.vlrnota,
            vei.placa,
            motorista.codparc AS codparc_motorista,
            motorista.nomeparc AS nome_motorista,
            top.descroper,
            ord.ad_doca AS doca,
            ord.horasaida AS horasaida,
            ord.seqcarga as seqcarga
        FROM tgfcab cab
        LEFT JOIN tsiemp emp
               ON emp.codemp = cab.codemp
        LEFT JOIN tgfpar parc_cab
               ON parc_cab.codparc = cab.codparc
        LEFT JOIN tsicid cid
               ON cid.codcid = parc_cab.codcid
        LEFT JOIN tgford ord
               ON ord.ordemcarga = cab.ordemcarga
        LEFT JOIN tgfvei vei
               ON vei.codveiculo = ord.codveiculo
        LEFT JOIN tgfpar motorista
               ON motorista.codparc = ord.codparcmotorista
        INNER JOIN tgftop top
                ON top.codtipoper = cab.codtipoper
               AND top.dhalter    = cab.dhtipoper
        WHERE ord.codemp = :CODEMP
          AND cab.ordemcarga = :ORDEMCARGA
          AND top.ad_carga = 'S'
          AND top.codtipoper NOT IN (2002, 2009)
    ),
    itens AS (
        SELECT
            b.ordemcarga,
            b.nunota,
            ite.codprod,
            pro.descrprod,
            pro.AD_VALIDABARRA,
            pro.referencia,
            ite.codvol,
            pro.marca,
            NVL(TO_CHAR(pro.ad_qtd_vol), ' ') AS qtd_emb,
            SUM(ite.qtdneg) AS qtdneg,
            SUM(ite.qtdvol) AS qtdvol,
            SUM(ite.vlrtot) AS vlrtot
        FROM base b
        LEFT JOIN tgfite ite
               ON ite.nunota = b.nunota
        LEFT JOIN tgfpro pro
               ON pro.codprod = ite.codprod
        GROUP BY
            b.ordemcarga,
            b.nunota,
            ite.codprod,
            pro.descrprod,
            pro.referencia,
            pro.AD_VALIDABARRA,
            ite.codvol,
            pro.marca,
            NVL(TO_CHAR(pro.ad_qtd_vol), ' ')
    )
    SELECT
        b.ordemcarga,
        b.codemp,
        b.empresa_razao,            
        b.numnota,
        b.nunota,
        b.codparc,
        b.parceiro_razao AS parceiro,
        b.nomecid,
        b.vlrnota,
        b.placa,
        b.codparc_motorista,
        b.nome_motorista AS motorista,
        i.codprod,
        i.descrprod,
        i.referencia,      
        i.AD_VALIDABARRA AS valida_cod_barra,
        i.codvol,
        i.marca,
        i.qtd_emb,
        b.nunota AS numero_nota,
        i.qtdneg,
        i.qtdvol,
        i.vlrtot,
        b.descroper,
        b.doca,
        b.horasaida,
        b.seqcarga
    FROM base b
    LEFT JOIN itens i
           ON i.nunota = b.nunota
    ORDER BY
        b.nunota,
        b.parceiro_razao,
        i.marca,
        i.codprod
    """

    conexao = None
    try:
        conexao = conectar_oracle()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500

        cursor = conexao.cursor()
        
        # Executa passando os parâmetros dinâmicos
        cursor.execute(sql, {"ORDEMCARGA": ordem_carga, "CODEMP": cod_emp})
        
        # Usa fetchall() porque uma Ordem de Carga retorna várias linhas (vários itens)
        resultados = cursor.fetchall()

        if not resultados:
            return jsonify({
                "sucesso": False,
                "mensagem": f"Nenhuma ordem de carga ou itens encontrados para o código {ordem_carga}."
            }), 404

        # Transforma o resultado (tupla) em uma lista de dicionários para o JSON ficar legível
        lista_itens = []
        for row in resultados:
            lista_itens.append({
                "ordemCarga": row[0],
                "codEmp": row[1],
                "empresaRazao": row[2],
                "numNota": row[3],
                "nuNota": row[4],
                "codParc": row[5],
                "parceiroRazao": row[6],
                "nomeCid": row[7],
                "vlrNota": float(row[8]) if row[8] is not None else 0.0,
                "placa": row[9],
                "codParcMotorista": row[10],
                "nomeMotorista": row[11],
                "codProd": row[12],
                "descrProd": row[13],
                "referencia": row[14],
                "validaCodBarra": row[15],
                "codVol": row[16],
                "marca": row[17],
                "qtdEmb": row[18].strip() if row[18] else "",
                "numeroNota": row[19],
                "qtdNeg": float(row[20]) if row[20] is not None else 0.0,
                "qtdVol": float(row[21]) if row[21] is not None else 0.0,
                "vlrTot": float(row[22]) if row[22] is not None else 0.0,
                "descrOper": row[23],
                "doca": row[24],
                "horaSaida": row[25].strftime('%Y-%m-%d %H:%M:%S') if row[25] else None, 
                "seqCarga": row[26]

            })

        return jsonify({
            "sucesso": True,
            "totalRegistros": len(lista_itens),
            "dados": lista_itens
        })

    except cx_Oracle.Error as err:
        print("Erro Oracle:", err)
        return jsonify({"erro": f"Erro de Banco de Dados: {err}"}), 500
    except Exception as e:
        print("Erro Geral:", e)
        return jsonify({"erro": str(e)}), 500
    finally:
        if conexao:
            conexao.close()
