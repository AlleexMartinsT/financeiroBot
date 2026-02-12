"""
Braspress login and invoice fetch helpers.

- Login on Braspress portal using Playwright (when needed)
- Persist cookies per CNPJ
- Query invoice list via HTTP POST
- Extract (fatura, vencimento, valor) from returned HTML
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from config import BRASPRESS_ARCHIVE_DIR, BRASPRESS_CONFIG_PATH


MAIN_URL = "https://www.braspress.com/area-do-cliente/minha-conta/"
FRAME_ORIGIN = "https://blue.braspress.com"

ARCHIVE_DIR = Path(BRASPRESS_ARCHIVE_DIR)
SECRETS_PATH = Path(BRASPRESS_CONFIG_PATH) if BRASPRESS_CONFIG_PATH else Path("")
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def _get_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright as _sync_playwright
        return _sync_playwright
    except Exception as e:
        raise RuntimeError(
            "Playwright/greenlet indisponivel no servidor. "
            "Reinstale dependencias com: "
            "pip install --upgrade pip setuptools wheel ; "
            "pip install --force-reinstall --no-cache-dir playwright greenlet ; "
            "python -m playwright install chromium"
        ) from e


def limpar_cookies_diariamente():
    """Apaga cookies e debug uma vez por dia."""
    last_file = ARCHIVE_DIR / ".last_refresh"
    hoje = datetime.now().strftime("%Y-%m-%d")
    if not last_file.exists():
        last_file.write_text(hoje, encoding="utf-8")
        return

    ultima_data = last_file.read_text(encoding="utf-8").strip()
    if ultima_data == hoje:
        return

    print("[Braspress] Novo dia detectado, limpando cookies e debug antigos...")
    for arquivo in ARCHIVE_DIR.glob("*"):
        if arquivo.suffix.lower() in {".json", ".html"}:
            try:
                arquivo.unlink()
            except Exception as e:
                print(f"[Braspress] Falha ao remover {arquivo.name}: {e}")
    last_file.write_text(hoje, encoding="utf-8")
    print("[Braspress] Limpeza concluida.")


limpar_cookies_diariamente()

if not SECRETS_PATH.exists():
    raise FileNotFoundError(
        f"Arquivo {SECRETS_PATH} nao encontrado.\n"
        "Crie 'braspress_config.json' no diretorio de secrets."
    )

with open(SECRETS_PATH, "r", encoding="utf-8") as f:
    CONFIG_PRIV = json.load(f)

PASSWORD_PADRAO = CONFIG_PRIV["senha"]
CNPJ_EH = CONFIG_PRIV["cnpjs"]["EH"]
CNPJ_MVA = CONFIG_PRIV["cnpjs"]["MVA"]


def path_in_archives(name: str) -> Path:
    return ARCHIVE_DIR / name


COOKIES_FILES = {
    CNPJ_EH: path_in_archives("cookies_EH.json"),
    CNPJ_MVA: path_in_archives("cookies_MVA.json"),
}


def playwright_login(cnpj_login: str):
    """Faz login no portal Braspress usando o CNPJ e salva cookies."""
    cookies_file = COOKIES_FILES.get(cnpj_login, path_in_archives(f"cookies_{cnpj_login}.json"))
    print(f"[*] Iniciando login na Braspress com CNPJ {cnpj_login}...")

    sync_playwright = _get_sync_playwright()
    with sync_playwright() as p:
        if getattr(sys, "frozen", False):
            base_path = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(sys.executable).parent
        else:
            base_path = Path(__file__).resolve().parent

        local_browser_path = base_path / "playwright" / ".local-browsers"
        chrome_exec = next(local_browser_path.rglob("headless_shell.exe"), None) if local_browser_path.exists() else None

        if chrome_exec and chrome_exec.exists():
            print(f"[Braspress] Usando Chromium empacotado: {chrome_exec}")
            browser = p.chromium.launch(headless=True, executable_path=str(chrome_exec))
        else:
            try:
                browser = p.chromium.launch(channel="chromium", headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True)

        context = browser.new_context()
        page = context.new_page()

        print("[*] Acessando pagina de login...")
        page.goto(MAIN_URL, timeout=60000)
        page.wait_for_selector("iframe", timeout=15000)

        frame = None
        for f in page.frames:
            if FRAME_ORIGIN in (f.url or ""):
                frame = f
                break
        if not frame:
            browser.close()
            raise RuntimeError("Frame de login da Braspress nao encontrado.")

        print("[*] Preenchendo login...")
        frame.fill("input[name='login']", cnpj_login)
        frame.fill("input[name='pass']", PASSWORD_PADRAO)
        frame.click("input[type='submit']")
        time.sleep(5)

        try:
            frame.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        html = frame.content().lower()
        if "minhas faturas" not in html:
            browser.close()
            raise RuntimeError(f"Login falhou para CNPJ {cnpj_login}.")

        cookies = context.cookies()
        with open(cookies_file, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2, ensure_ascii=False)

        browser.close()
        print(f"[Braspress] Login concluido para CNPJ {cnpj_login}.")
        return cookies


def extrair_tabela(html: str):
    """Extrai dados de Fatura, Vencimento e Valor do HTML retornado."""
    soup = BeautifulSoup(html, "html.parser")
    dados = []
    for linha in soup.select("table tr"):
        cols = [c.get_text(strip=True) for c in linha.find_all("td")]
        if len(cols) >= 3 and re.search(r"\d", cols[0]):
            dados.append((cols[0], cols[1], cols[2]))
    return dados


def obter_faturas(cnpj_login: str):
    """Usa cookies salvos (ou faz login) e retorna lista de faturas."""
    cookies_file = COOKIES_FILES.get(cnpj_login, path_in_archives(f"cookies_{cnpj_login}.json"))

    if cookies_file.exists():
        try:
            with open(cookies_file, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            print(f"[*] Usando cookies salvos para {cnpj_login}.")
        except Exception:
            cookies = playwright_login(cnpj_login)
    else:
        cookies = playwright_login(cnpj_login)

    session = requests.Session()
    for c in cookies:
        if "blue.braspress.com" in c.get("domain", ""):
            session.cookies.set(c["name"], c["value"])

    url = "https://blue.braspress.com/site/list/fatura"
    print(f"[*] Fazendo POST para {url}")
    resp = session.post(url, data={"titulosAbertos": "true", "fatNumero": ""}, timeout=30)
    html = resp.text

    debug_file = path_in_archives(f"debug_faturas_{cnpj_login}.html")
    debug_file.write_text(html, encoding="utf-8")

    dados = extrair_tabela(html)
    if not dados:
        print(f"[Braspress] Nenhum dado encontrado. Verifique {debug_file.name}")
    return dados


if __name__ == "__main__":
    obter_faturas(CNPJ_EH)
