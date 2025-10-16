import os
import locale
import json
from pathlib import Path

# === CONFIGURAÇÕES ===
DOWNLOAD_DIR = "xmls_baixados"
CONFIG_PRIV_PATH = Path("secrets/config_privado.json")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

INTERVALO = 1800  # Verificação automática a cada X minutos

if not CONFIG_PRIV_PATH.exists():
    raise FileNotFoundError(
        f"Arquivo {CONFIG_PRIV_PATH} não encontrado.\n"
        "Crie o arquivo 'secrets/config_privado.json' com os IDs das planilhas e CNPJs."
    )

with open(CONFIG_PRIV_PATH, "r", encoding="utf-8") as f:
    CONFIG_PRIV = json.load(f)
    
# === Escopos ===
SCOPES_GMAIL = ["https://www.googleapis.com/auth/gmail.modify"]
SCOPES_SHEET = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# === IDs das planilhas ===
SHEET_EH_2025 = CONFIG_PRIV["planilhas"]["EH_2025"]
SHEET_EH_2026 = CONFIG_PRIV["planilhas"]["EH_2026"]
SHEET_MVA_2025 = CONFIG_PRIV["planilhas"]["MVA_2025"]
SHEET_MVA_2026 = CONFIG_PRIV["planilhas"]["MVA_2026"]

# === CNPJs ===
CNPJ_EH = CONFIG_PRIV["cnpjs"]["EH"]
CNPJ_MVA = CONFIG_PRIV["cnpjs"]["MVA"]

# Arquivos de credenciais (coloque-os no mesmo diretório)
CRED_FILE_SHEETS = "secrets/credentials.json"
CRED_FILE_GMAIL_PRINCIPAL = "secrets/credentials_gmail.json"
CRED_FILE_GMAIL_NFE = "secrets/credentials_gmailNFE.json"

# Relatórios
RELATORIO_DIR = "relatorios"
os.makedirs(RELATORIO_DIR, exist_ok=True)

# Locale
try:
    locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')
except Exception:
    # Em alguns sistemas o locale pode não estar disponível — falha silenciosa
    pass
