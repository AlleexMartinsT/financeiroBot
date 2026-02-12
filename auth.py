import os
import time
import pickle
import threading
from pathlib import Path

from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import gspread

import runtime_status
from config import (
    SCOPES_SHEET,
    SCOPES_GMAIL,
    CRED_FILE_SHEETS,
    CRED_FILE_GMAIL_PRINCIPAL,
    CRED_FILE_GMAIL_NFE,
    TOKENS_DIR,
)


# === Funcao utilitaria para respeitar limites de API ===
def apiCooldown():
    seconds = 30
    print(f"Limite da API atingido, aguardando {seconds} segundos e tentando novamente...")
    try:
        runtime_status.begin_api_cooldown(seconds)
    except Exception:
        pass
    try:
        time.sleep(seconds)
    finally:
        try:
            runtime_status.end_api_cooldown()
        except Exception:
            pass


# === Autenticacao Gmail (com cache de token) ===
def autenticarGmail(cred_file, token_name, force_new=False):
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    token_path = Path(TOKENS_DIR) / f"{token_name}.pkl"
    creds = None

    if force_new and token_path.exists():
        try:
            token_path.unlink()
            print(f"[Auth] Token removido para nova autenticacao: {token_path}")
        except Exception:
            pass

    if token_path.exists():
        with open(token_path, "rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[Auth] Falha ao atualizar token ({token_name}): {e}")
                creds = None

        if not creds:
            print(f"[Auth] Autorizando conta {token_name}...")
            flow = InstalledAppFlow.from_client_secrets_file(str(cred_file), SCOPES_GMAIL)
            creds = flow.run_local_server(port=0)
            with open(token_path, "wb") as token:
                pickle.dump(creds, token)
            print(f"[Auth] Token salvo em {token_path}")

    return build("gmail", "v1", credentials=creds)


_gmail_lock = threading.Lock()
gmailPrincipal = None
gmailNFE = None


def _gmail_params(conta: str):
    conta_norm = (conta or "").strip().lower()
    if conta_norm == "principal":
        return CRED_FILE_GMAIL_PRINCIPAL, "token_gmailPrincipal", "gmailPrincipal"
    if conta_norm == "nfe":
        return CRED_FILE_GMAIL_NFE, "token_gmailNFE", "gmailNFE"
    raise ValueError("Conta invalida. Use 'principal' ou 'nfe'.")


def get_gmail_service(conta: str, force_refresh: bool = False, force_new_token: bool = False):
    """Retorna servico Gmail inicializado sob demanda para a conta solicitada."""
    global gmailPrincipal, gmailNFE
    _, _, attr = _gmail_params(conta)

    with _gmail_lock:
        current = gmailPrincipal if attr == "gmailPrincipal" else gmailNFE
        if force_refresh:
            current = None

        if current is None:
            cred_file, token_name, _ = _gmail_params(conta)
            current = autenticarGmail(cred_file, token_name, force_new=force_new_token)
            if attr == "gmailPrincipal":
                gmailPrincipal = current
            else:
                gmailNFE = current

        return current


def ensure_gmail_services():
    get_gmail_service("principal")
    get_gmail_service("nfe")


def reautenticarGmail(conta: str):
    """
    Refaz autenticacao Gmail para a conta informada:
    - 'principal'
    - 'nfe'
    """
    conta_norm = (conta or "").strip().lower()
    get_gmail_service(conta_norm, force_refresh=True, force_new_token=True)
    return conta_norm


# === Autenticacao Google Sheets (conta de servico) ===
def autenticarSheets(cred_file=CRED_FILE_SHEETS, scopes=SCOPES_SHEET):
    creds_sheets = Credentials.from_service_account_file(str(cred_file), scopes=scopes)
    return gspread.authorize(creds_sheets)


# === Instancia os servicos ===
sheetsClient = autenticarSheets()
