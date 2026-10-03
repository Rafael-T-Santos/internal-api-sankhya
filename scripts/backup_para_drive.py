"""Copia um backup do televendas para o Google Drive da empresa (cópia FORA do servidor).

O arquivo entra pela entrada padrão — o backup está no host, e o container da
API é quem tem as credenciais do Drive. Chamado pelo ops/backup-televendas.sh:

    docker exec -i api_sankhya python scripts/backup_para_drive.py <nome.sql.gz> < arquivo

Diferente dos anexos da cobrança (drive.enviar_arquivo, que abre o link para
"qualquer pessoa"), aqui o arquivo fica PRIVADO: o backup tem nomes e telefones
de clientes. Vai para uma pasta própria, "backups-televendas", criada pelo app
na primeira vez, e só os 30 mais recentes ficam lá.

Escopo drive.file: o app só enxerga o que ele mesmo criou, então a busca da
pasta e a limpeza dos antigos nunca tocam em outro arquivo da conta.
"""

import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from googleapiclient.http import MediaIoBaseUpload  # noqa: E402

import drive  # noqa: E402

PASTA = "backups-televendas"
MANTER = 30


def pasta_id(svc):
    r = svc.files().list(
        q=f"name = '{PASTA}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
        fields="files(id)",
        spaces="drive",
    ).execute()
    if r.get("files"):
        return r["files"][0]["id"]
    return svc.files().create(
        body={"name": PASTA, "mimeType": "application/vnd.google-apps.folder"}, fields="id"
    ).execute()["id"]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    nome = os.path.basename(sys.argv[1])
    conteudo = sys.stdin.buffer.read()
    if len(conteudo) < 100:
        sys.exit(f"Backup vazio ou truncado ({len(conteudo)} bytes): nada enviado.")

    svc = drive._servico(drive._config())
    pasta = pasta_id(svc)
    midia = MediaIoBaseUpload(io.BytesIO(conteudo), mimetype="application/gzip", resumable=False)
    criado = svc.files().create(body={"name": nome, "parents": [pasta]}, media_body=midia, fields="id").execute()
    # Sem permissions().create: o arquivo fica visível só para a conta dona do Drive.

    antigos = svc.files().list(
        q=f"'{pasta}' in parents and trashed = false",
        orderBy="createdTime desc",
        fields="files(id, name)",
        pageSize=200,
    ).execute().get("files", [])[MANTER:]
    for f in antigos:
        svc.files().delete(fileId=f["id"]).execute()

    print(f"Drive OK {nome} ({len(conteudo)} bytes, id {criado['id']}); {len(antigos)} antigo(s) apagado(s)")


if __name__ == "__main__":
    main()
