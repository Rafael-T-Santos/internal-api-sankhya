"""Autorização única do Google Drive — rodar UMA vez, na SUA máquina.

O servidor não tem navegador, e o Google exige que um humano aprove o acesso
uma primeira vez. Este script faz essa aprovação e devolve um **refresh token**:
a credencial de longa duração que a API vai usar daí em diante, sozinha.

    pip install google-auth-oauthlib google-api-python-client
    python scripts/autorizar-drive.py --client-id XXX --client-secret YYY

Ele abre o navegador, você entra com a conta da empresa (a dona da pasta
Cobrancas), aprova, e no fim ele:
  1. imprime o refresh token;
  2. TESTA de verdade: sobe um arquivo na pasta, marca como "qualquer pessoa
     com o link", mostra o link e apaga o arquivo.

O passo 2 existe porque o escopo `drive.file` dá acesso só aos arquivos que o
próprio app cria. Se ele não conseguir enxergar uma pasta criada à mão pelo
navegador, é melhor descobrir agora — o script diz o que fazer nesse caso.

NÃO cole a saída deste script em conversa nenhuma: o refresh token vale como
senha da conta para o que o escopo permite. Ele vai direto para o .env do
servidor.
"""

import argparse
import io
import sys

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseUpload
except ImportError:
    sys.exit(
        "Faltam bibliotecas. Rode:\n"
        "    pip install google-auth-oauthlib google-api-python-client"
    )

# Escopo estreito de propósito: dá acesso só aos arquivos que ESTE app criar,
# nunca ao resto do Drive da conta. Também é o que evita a verificação pesada
# do Google, exigida pelos escopos amplos.
ESCOPOS = ["https://www.googleapis.com/auth/drive.file"]

# Pasta "Cobrancas" (o trecho do link depois de /folders/).
PASTA_PADRAO = "1ftScgC28bhC7QrFMvp7PKT21g2yt6H6v"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--client-secret", required=True)
    ap.add_argument("--pasta", default=PASTA_PADRAO, help="ID da pasta no Drive")
    args = ap.parse_args()

    config = {
        "installed": {
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(config, ESCOPOS)
    # prompt="consent" força o Google a devolver o refresh_token: sem isso, numa
    # segunda autorização ele manda só o access token (que dura 1 hora) e a
    # credencial de longa duração não aparece.
    cred = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    if not cred.refresh_token:
        sys.exit(
            "O Google não devolveu refresh token. Revogue o acesso do app em\n"
            "https://myaccount.google.com/permissions e rode de novo."
        )

    print("\n" + "=" * 70)
    print("REFRESH TOKEN (vai para o .env do servidor, não cole em chat):\n")
    print(cred.refresh_token)
    print("=" * 70)

    print("\nTestando a pasta de verdade…")
    drive = build("drive", "v3", credentials=cred)
    arquivo_id = None
    try:
        midia = MediaIoBaseUpload(
            io.BytesIO(b"teste de upload da API de cobranca"),
            mimetype="text/plain",
            resumable=False,
        )
        criado = (
            drive.files()
            .create(
                body={"name": "_teste-cobranca.txt", "parents": [args.pasta]},
                media_body=midia,
                fields="id, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        arquivo_id = criado["id"]

        drive.permissions().create(
            fileId=arquivo_id,
            body={"role": "reader", "type": "anyone"},
            supportsAllDrives=True,
        ).execute()

        print("  OK — upload e compartilhamento funcionaram.")
        print(f"  Link gerado: {criado.get('webViewLink')}")
        print(f"\n  Use esta pasta no .env:  GOOGLE_DRIVE_FOLDER_ID={args.pasta}")

    except HttpError as err:
        if err.resp.status in (403, 404):
            print(f"  A pasta {args.pasta} não é acessível com este escopo.")
            print("  Isso é esperado quando a pasta foi criada à mão no navegador:")
            print("  o escopo drive.file só enxerga o que o próprio app cria.")
            print("\n  Criando uma pasta pelo app para usar no lugar…")
            nova = (
                drive.files()
                .create(
                    body={
                        "name": "Cobrancas - Anexos",
                        "mimeType": "application/vnd.google-apps.folder",
                    },
                    fields="id, webViewLink",
                )
                .execute()
            )
            print(f"  Pasta criada: {nova.get('webViewLink')}")
            print(f"\n  Use esta pasta no .env:  GOOGLE_DRIVE_FOLDER_ID={nova['id']}")
            print("  (ela aparece no Drive da conta; pode arrastar para onde quiser)")
        else:
            raise
    finally:
        if arquivo_id:
            drive.files().delete(fileId=arquivo_id, supportsAllDrives=True).execute()
            print("\n  Arquivo de teste apagado.")


if __name__ == "__main__":
    main()
