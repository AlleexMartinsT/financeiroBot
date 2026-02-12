"""
Tray application for Finance Bot.
"""

import os
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw
from plyer import notification

from config import RELATORIO_DIR
import auth
from gmail_fetcher import processarEmails
from reporter import escreverRelatorio


def create_icon(color: str = "blue"):
    color_map = {
        "blue": (0, 128, 255, 255),
        "green": (0, 200, 0, 255),
        "red": (220, 0, 0, 255),
    }
    rgb = color_map.get(color, (0, 128, 255, 255))
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill=rgb)
    return image


def notificar(titulo, mensagem):
    try:
        notification.notify(title=titulo, message=mensagem, timeout=5)
    except Exception as e:
        print(f"[Tray] Falha ao exibir notificacao: {e}")


def run_tray(on_quit_callback, start_callback=None, panel_url=None):
    icon = pystray.Icon("FinanceBot", title="Finance Bot")
    status_lock = threading.Lock()
    status_color = {"value": "blue"}

    def atualizar_cor(cor):
        with status_lock:
            status_color["value"] = cor
        icon.icon = create_icon(cor)
        icon.visible = True

    def executar_verificacao():
        try:
            atualizar_cor("green")
            notificar("Finance Bot", "Iniciando verificacao manual...")
            print("\n[Manual] Verificacao solicitada pelo usuario.")
            processarEmails(auth.get_gmail_service("principal"), "Conta Principal")
            time.sleep(5)
            processarEmails(auth.get_gmail_service("nfe"), "Conta NFe")
            atualizar_cor("blue")
            notificar("Finance Bot", "Verificacao concluida com sucesso.")
            print("[Tray] Verificacao concluida.")
        except Exception as e:
            msg = f"Erro durante verificacao: {e}"
            print(f"[Tray] {msg}")
            traceback.print_exc()
            escreverRelatorio(f"[Tray] {msg}")
            atualizar_cor("red")
            notificar("Finance Bot", msg)
            time.sleep(10)
            atualizar_cor("blue")

    def verificar_agora(icon, item):
        if start_callback:
            print("[Tray] Iniciando loop principal via callback.")
            atualizar_cor("green")
            threading.Thread(target=start_callback, daemon=True).start()
        else:
            threading.Thread(target=executar_verificacao, daemon=True).start()

    def abrir_relatorios(icon, item):
        caminho = Path(RELATORIO_DIR).resolve()
        if not caminho.exists():
            os.makedirs(caminho, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(caminho)
        elif sys.platform == "darwin":
            os.system(f"open '{caminho}'")
        else:
            os.system(f"xdg-open '{caminho}'")

    def abrir_painel(icon, item):
        if panel_url:
            webbrowser.open(panel_url)
        else:
            notificar("Finance Bot", "Painel web nao configurado.")

    def sair(icon, item):
        notificar("Finance Bot", "Encerrando o aplicativo...")
        icon.visible = False
        icon.stop()
        on_quit_callback()

    menu = pystray.Menu(
        pystray.MenuItem("Verificar agora", verificar_agora),
        pystray.MenuItem("Abrir painel", abrir_painel),
        pystray.MenuItem("Abrir relatorios", abrir_relatorios),
        pystray.MenuItem("Sair", sair),
    )

    icon.icon = create_icon("blue")
    icon.menu = menu
    print("[Tray] Icone iniciado. Clique com o botao direito para opcoes.")
    icon.run()


if __name__ == "__main__":
    def sair_manual():
        print("Encerrando manualmente.")
        sys.exit(0)

    run_tray(sair_manual)
