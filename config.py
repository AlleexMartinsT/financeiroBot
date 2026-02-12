import os
import sys
import locale
import json
import shutil
from pathlib import Path

APP_NAME = "FinanceBot"
APPDATA_BASE = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
SECRETS_APPDATA_DIR = APPDATA_BASE / "secrets"
LOCAL_SECRETS_DIR = Path("secrets")

APPDATA_BASE.mkdir(parents=True, exist_ok=True)
SECRETS_APPDATA_DIR.mkdir(parents=True, exist_ok=True)

# Runtime directories in AppData\\Roaming\\FinanceBot
DOWNLOAD_DIR = str(APPDATA_BASE / "xmls_baixados")
RELATORIO_DIR = str(APPDATA_BASE / "relatorios")
TOKENS_DIR = SECRETS_APPDATA_DIR
BRASPRESS_ARCHIVE_DIR = APPDATA_BASE / "braspress_archives"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(RELATORIO_DIR, exist_ok=True)
BRASPRESS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

INTERVALO = 1800  # Verificacao automatica a cada X minutos


def _candidatos_secrets() -> list[Path]:
    candidatos = [LOCAL_SECRETS_DIR]

    # Pasta ao lado do executavel (PyInstaller onedir)
    try:
        candidatos.append(Path(sys.executable).resolve().parent / "secrets")
    except Exception:
        pass

    # Pasta ao lado do arquivo atual (execucao via script)
    try:
        candidatos.append(Path(__file__).resolve().parent / "secrets")
    except Exception:
        pass

    # Pasta interna do bundle (PyInstaller onefile/onedir)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidatos.append(Path(meipass) / "secrets")

    unicos = []
    vistos = set()
    for p in candidatos:
        k = str(p)
        if k not in vistos:
            vistos.add(k)
            unicos.append(p)
    return unicos


def _resolver_secret(nome_arquivo: str, obrigatorio: bool = True) -> Path:
    appdata_path = SECRETS_APPDATA_DIR / nome_arquivo

    # Sempre preferir e persistir em AppData\Roaming
    if appdata_path.exists():
        return appdata_path

    # Primeiro start: tenta copiar de alguma origem disponivel
    for base in _candidatos_secrets():
        origem = base / nome_arquivo
        if origem.exists():
            try:
                shutil.copy2(origem, appdata_path)
                return appdata_path
            except Exception:
                # Se nao conseguir copiar, ao menos permite continuar com a origem
                return origem

    if obrigatorio:
        fontes = ", ".join(str(p) for p in _candidatos_secrets())
        raise FileNotFoundError(
            f"Arquivo {nome_arquivo} nao encontrado em {SECRETS_APPDATA_DIR}. "
            f"Fontes verificadas: {fontes}"
        )

    return appdata_path


CONFIG_PRIV_PATH = _resolver_secret("config_privado.json")

with open(CONFIG_PRIV_PATH, "r", encoding="utf-8") as f:
    CONFIG_PRIV = json.load(f)

# === Escopos ===
SCOPES_GMAIL = ["https://www.googleapis.com/auth/gmail.modify"]
SCOPES_SHEET = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# === IDs das planilhas ===
SHEET_EH_2025 = CONFIG_PRIV["planilhas"]["EH_2025"]
SHEET_EH_2026 = CONFIG_PRIV["planilhas"]["EH_2026"]
SHEET_MVA_2025 = CONFIG_PRIV["planilhas"]["MVA_2025"]
SHEET_MVA_2026 = CONFIG_PRIV["planilhas"]["MVA_2026"]

# === CNPJs ===
CNPJ_EH = CONFIG_PRIV["cnpjs"]["EH"]
CNPJ_MVA = CONFIG_PRIV["cnpjs"]["MVA"]

# Arquivos de credenciais
CRED_FILE_SHEETS = str(_resolver_secret("credentials.json"))
CRED_FILE_GMAIL_PRINCIPAL = str(_resolver_secret("credentials_gmail.json"))
CRED_FILE_GMAIL_NFE = str(_resolver_secret("credentials_gmailNFE.json"))
BRASPRESS_CONFIG_PATH = str(_resolver_secret("braspress_config.json", obrigatorio=False))

# Locale
try:
    locale.setlocale(locale.LC_ALL, "pt_BR.UTF-8")
except Exception:
    pass
