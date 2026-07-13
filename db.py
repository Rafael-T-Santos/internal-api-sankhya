import os
import cx_Oracle


def conectar_oracle():
    """Abre uma conexão com o Oracle usando as credenciais do ambiente.

    Devolve None em caso de falha (o chamador responde 500).
    """
    try:
        user = os.environ.get("DB_USER")
        password = os.environ.get("DB_PASS")
        dsn_str = os.environ.get("DB_DSN")  # Ex: "192.168.255.250:1521/xe"

        if not all([user, password, dsn_str]):
            raise ValueError(
                "Credenciais do banco de dados (DB_USER, DB_PASS, DB_DSN) "
                "não configuradas nas variáveis de ambiente."
            )

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
