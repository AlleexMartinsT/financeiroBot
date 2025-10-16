# auth.py
import time
from google.oauth2.service_account import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import gspread

from config import SCOPES_SHEET, SCOPES_GMAIL, CRED_FILE_SHEETS, CRED_FILE_GMAIL_PRINCIPAL, CRED_FILE_GMAIL_NFE

# === Função utilitária para respeitar limites de API ===
def apiCooldown():
    print("Limite da API atingido, aguardando 30 segundos e tentando novamente...")
    time.sleep(30)

# === Autenticação Gmail (OAuth pessoal) ===
def autenticarGmail(cred_file, scopes=SCOPES_GMAIL):
    flow = InstalledAppFlow.from_client_secrets_file(cred_file, scopes)
    creds = flow.run_local_server(port=0)
    return build("gmail", "v1", credentials=creds)

# === Autenticação Google Sheets (Conta de serviço) ===
def autenticarSheets(cred_file=CRED_FILE_SHEETS, scopes=SCOPES_SHEET):
    creds_sheets = Credentials.from_service_account_file(cred_file, scopes=scopes)
    return gspread.authorize(creds_sheets)

# === Instanciando serviços na importação (comportamento igual ao código original) ===
# Note: essas chamadas abrem o fluxo OAuth no navegador quando executadas.
gmailPrincipal = autenticarGmail(CRED_FILE_GMAIL_PRINCIPAL)
gmailNFE = autenticarGmail(CRED_FILE_GMAIL_NFE)
sheetsClient = autenticarSheets()
