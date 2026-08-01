"""Envio de arquivos para o Google Drive da empresa.

O operador escolhe o arquivo no computador dele; quem sobe no Drive é esta API.
Não guardamos o arquivo: o que vai para o banco (`AD_COBRANEXO.URL`) é o link.

Autenticação: a conta é um Gmail comum, então não dá para usar conta de serviço
(ela não tem espaço próprio no Drive e o upload falharia com quota excedida).
Em vez disso, alguém autorizou o app UMA vez pelo navegador — ver
`scripts/autorizar-drive.py` — e guardamos o refresh token no ambiente.

Escopo `drive.file`: acesso apenas aos arquivos que este app cria. Mesmo com o
refresh token em mãos, ninguém lê o resto do Drive da conta por aqui.
"""

import io
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

ESCOPOS = ["https://www.googleapis.com/auth/drive.file"]

# Teto do arquivo. O Drive aguenta muito mais, mas isto aqui é anexo de cobrança
# (boleto, comprovante, print de conversa) — passou disso, é engano ou abuso, e
# o upload ocuparia o processo por minutos.
LIMITE_BYTES = 25 * 1024 * 1024


class DriveNaoConfigurado(Exception):
    """Faltam variáveis de ambiente — o servidor não pode subir arquivo."""


def _config():
    faltando = [
        v
        for v in (
            "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET",
            "GOOGLE_REFRESH_TOKEN",
            "GOOGLE_DRIVE_FOLDER_ID",
        )
        if not os.environ.get(v)
    ]
    if faltando:
        raise DriveNaoConfigurado(
            "Envio de arquivo indisponível: faltam " + ", ".join(faltando) + " no .env"
        )
    return {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
        "pasta": os.environ["GOOGLE_DRIVE_FOLDER_ID"],
    }


def _servico(cfg):
    """Monta um cliente novo a cada envio.

    Poderia ser um só, reaproveitado — mas o servidor atende requisições em
    threads e o cliente do googleapiclient não é seguro para uso concorrente.
    Como anexo é coisa de algumas vezes por dia, o custo de recriar (e de
    renovar o token) não se compara ao de caçar corrupção de estado.
    `static_discovery` evita baixar o descritor da API a cada vez.
    """
    cred = Credentials(
        None,  # sem access token: a biblioteca busca um novo pelo refresh token
        refresh_token=cfg["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        scopes=ESCOPOS,
    )
    return build("drive", "v3", credentials=cred, static_discovery=True)


def enviar_arquivo(nome, tipo_mime, conteudo):
    """Sobe o arquivo e devolve {'id', 'url', 'nome'}.

    O link fica aberto a quem o tiver ("qualquer pessoa com o link"), decisão
    tomada com o usuário: o pessoal precisa abrir o anexo no celular, sem login.
    """
    cfg = _config()
    drive = _servico(cfg)

    midia = MediaIoBaseUpload(
        io.BytesIO(conteudo),
        mimetype=tipo_mime or "application/octet-stream",
        resumable=False,
    )
    arquivo = (
        drive.files()
        .create(
            body={"name": nome, "parents": [cfg["pasta"]]},
            media_body=midia,
            fields="id, webViewLink",
        )
        .execute()
    )

    drive.permissions().create(
        fileId=arquivo["id"],
        body={"role": "reader", "type": "anyone"},
    ).execute()

    return {"id": arquivo["id"], "url": arquivo.get("webViewLink"), "nome": nome}
