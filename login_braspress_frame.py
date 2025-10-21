"""
login_braspress_frame.py
--------------------------------
• Faz login no iframe blue.braspress.com (com Playwright)
• Captura cookies da sessão autenticada
• Faz POST direto para /site/list/fatura (como faz o JS da página)
• Extrai Fatura, Vencimento e Valor do HTML retornado
• Armazena cookies e logs em braspress_archives/
• Lê CNPJs e senha de secrets/braspress_config.json
• Atualiza cookies e debug automaticamente a cada novo dia
"""

import json
import re
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright
import requests
from bs4 import BeautifulSoup

# === CONFIGURAÇÕES ===
MAIN_URL = "https://www.braspress.com/area-do-cliente/minha-conta/"
FRAME_ORIGIN = "https://blue.braspress.com"

# Diretórios
BASE_DIR = Path(__file__).resolve().parent
ARCHIVE_DIR = BASE_DIR / "braspress_archives"
SECRETS_PATH = BASE_DIR / "secrets" / "braspress_config.json"
ARCHIVE_DIR.mkdir(exist_ok=True)

# === Função: limpeza diária de cookies e debug ===
def limpar_cookies_diariamente():
    """Apaga cookies e debug uma vez por dia."""
    last_file = ARCHIVE_DIR / ".last_refresh"
    hoje = datetime.now().strftime("%Y-%m-%d")

    # Se o arquivo de controle não existe, cria
    if not last_file.exists():
        last_file.write_text(hoje, encoding="utf-8")
        return

    ultima_data = last_file.read_text(encoding="utf-8").strip()

    # Se mudou o dia → apaga cookies e debug
    if ultima_data != hoje:
        print("[Braspress] Novo dia detectado — limpando cookies e debug antigos...")
        for arquivo in ARCHIVE_DIR.glob("*"):
            if arquivo.name.endswith(".json") or arquivo.name.endswith(".html"):
                try:
                    arquivo.unlink()
                except Exception as e:
                    print(f"[Braspress] Falha ao remover {arquivo.name}: {e}")
        last_file.write_text(hoje, encoding="utf-8")
        print("[Braspress] Limpeza concluída.")

# Executa limpeza diária ao iniciar o módulo
limpar_cookies_diariamente()

# === Carrega dados sigilosos ===
if not SECRETS_PATH.exists():
    raise FileNotFoundError(
        f"Arquivo {SECRETS_PATH} não encontrado.\n"
        "Crie o arquivo 'secrets/braspress_config.json' com os CNPJs e senha."
    )

with open(SECRETS_PATH, "r", encoding="utf-8") as f:
    CONFIG_PRIV = json.load(f)

PASSWORD_PADRAO = CONFIG_PRIV["senha"]
CNPJ_EH = CONFIG_PRIV["cnpjs"]["EH"]
CNPJ_MVA = CONFIG_PRIV["cnpjs"]["MVA"]

# Arquivos de cookies por CNPJ
def path_in_archives(name: str) -> Path:
    return ARCHIVE_DIR / name

COOKIES_FILES = {
    CNPJ_EH: path_in_archives("cookies_EH.json"),
    CNPJ_MVA: path_in_archives("cookies_MVA.json")
}

# === Lógica existente (inalterada) ===
def playwright_login(cnpj_login: str):
    """Faz login no portal Braspress usando o CNPJ informado e salva cookies."""
    cookies_file = COOKIES_FILES.get(cnpj_login, path_in_archives(f"cookies_{cnpj_login}.json"))

    print(f"[*] Iniciando login na Braspress com CNPJ {cnpj_login}...")

    with sync_playwright() as p:
        # Detecta caminho correto (empacotado ou não)
        if getattr(sys, 'frozen', False):
            base_path = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(sys.executable).parent
        else:
            base_path = Path(__file__).resolve().parent

        local_browser_path = base_path / "playwright" / ".local-browsers"
        chrome_exec = None
        for sub in local_browser_path.rglob("headless_shell.exe"):
            chrome_exec = sub
            break

        if chrome_exec and chrome_exec.exists():
            print(f"✅ Usando Chromium empacotado: {chrome_exec}")
            browser = p.chromium.launch(headless=True, executable_path=str(chrome_exec))
        else:
            try:
                browser = p.chromium.launch(channel="chromium", headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True)

        context = browser.new_context()
        page = context.new_page()

        print("[*] Acessando página de login...")
        page.goto(MAIN_URL, timeout=60000)
        page.wait_for_selector("iframe", timeout=15000)

        # Localiza frame do blue.braspress.com
        frame = None
        for f in page.frames:
            if FRAME_ORIGIN in (f.url or ""):
                frame = f
                break
        if not frame:
            raise RuntimeError("Frame de login não encontrado.")

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
            raise RuntimeError(f"Login falhou para CNPJ {cnpj_login}.")

        print(f"[+] Login concluído com sucesso ({cnpj_login}).")

        cookies = context.cookies()
        with open(cookies_file, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)

        browser.close()
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
    """Usa cookies (ou faz login) e retorna lista de (fatura, vencimento, valor)."""
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
    print(f"[*] Fazendo POST direto para {url}")
    resp = session.post(url, data={"titulosAbertos": "true", "fatNumero": ""}, timeout=30)
    html = resp.text

    debug_file = path_in_archives(f"debug_faturas_{cnpj_login}.html")
    debug_file.write_text(html, encoding="utf-8")
    print(f"[+] HTML da tabela salvo em {debug_file.name}")

    dados = extrair_tabela(html)
    print(f"\n=== RESULTADOS ({cnpj_login}) ===")
    for fatura, venc, valor in dados:
        print(f"Fatura: {fatura} | Vencimento: {venc} | Valor: {valor}")

    if not dados:
        print(f"[-] Nenhum dado encontrado — verifique {debug_file.name}")

    return dados


if __name__ == "__main__":
    cnpj_teste = CNPJ_EH
    obter_faturas(cnpj_teste)