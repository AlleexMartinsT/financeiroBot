# auth.py
import os
import time
import pickle
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import gspread

from config import (
    SCOPES_SHEET,
    SCOPES_GMAIL,
    CRED_FILE_SHEETS,
    CRED_FILE_GMAIL_PRINCIPAL,
    CRED_FILE_GMAIL_NFE
)

# === Função utilitária para respeitar limites de API ===
def apiCooldown():
    print("Limite da API atingido, aguardando 30 segundos e tentando novamente...")
    time.sleep(30)


# === Autenticação Gmail (com cache de token) ===
def autenticarGmail(cred_file, token_name):
    token_path = f"secrets/{token_name}.pkl"
    creds = None

    # Tenta carregar token salvo
    if os.path.exists(token_path):
        with open(token_path, "rb") as token:
            creds = pickle.load(token)

    # Se não há token ou é inválido, refaz autenticação
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[Auth] Falha ao atualizar token ({token_name}): {e}")
                creds = None

        if not creds:
            print(f"[Auth] Autorizando conta {token_name}...")
            flow = InstalledAppFlow.from_client_secrets_file(cred_file, SCOPES_GMAIL)
            creds = flow.run_local_server(port=0)
            with open(token_path, "wb") as token:
                pickle.dump(creds, token)
            print(f"[Auth] Token salvo em {token_path}")

    return build("gmail", "v1", credentials=creds)


# === Autenticação Google Sheets (conta de serviço) ===
def autenticarSheets(cred_file=CRED_FILE_SHEETS, scopes=SCOPES_SHEET):
    creds_sheets = Credentials.from_service_account_file(cred_file, scopes=scopes)
    return gspread.authorize(creds_sheets)


# === Instancia os serviços ===
gmailPrincipal = autenticarGmail(CRED_FILE_GMAIL_PRINCIPAL, "token_gmailPrincipal")
gmailNFE = autenticarGmail(CRED_FILE_GMAIL_NFE, "token_gmailNFE")
sheetsClient = autenticarSheets()
