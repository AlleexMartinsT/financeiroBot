import os
import sys
import time
import threading
from datetime import datetime

from tray_icon import run_tray  # ícone e menu
from auth import gmailPrincipal, gmailNFE
from gmail_fetcher import processarEmails
from reporter import (
    eventosProcessados, eventosIgnorados, historicoEventos,
    escreverRelatorio, consolidarRelatorioTMP, ultimoRelatorio
)
from config import INTERVALO

# =========================
# CONTROLE GLOBAL
# =========================

running = False        # indica se o loop principal está ativo
stop_event = threading.Event()  # usado para parar o loop com segurança

# =========================
# LOOP PRINCIPAL
# =========================
def main_loop():
    global running
    running = True
    print("[Loop] Iniciando verificação automática.")

    while not stop_event.is_set():
        hora_atual = datetime.now().strftime("%H:%M - %d/%m/%Y")
        print("\n[Loop] Verificando e-mails...")

        eventosProcessados.clear()
        eventosIgnorados.clear()

        try:
            processarEmails(gmailPrincipal, "Conta Principal")
            print("[Loop] Aguardando 10 segundos antes da próxima conta...")
            time.sleep(10)
            processarEmails(gmailNFE, "Conta NFe")
        except Exception as e:
            if "[WinError 2]" not in str(e):
                print(f"[Loop] Erro: {e}")
                escreverRelatorio(f"[{hora_atual}] Erro: {e}")

        # === Relatório de Resumo ===
        if eventosProcessados or eventosIgnorados:
            escreverRelatorio(f"\nRelatório de {hora_atual}")
            
            # Processados 
            if eventosProcessados:
                escreverRelatorio("Fornecedores processados: ")
                print("[Loop] Fornecedores Processados:")
                for fornecedor, conta in eventosProcessados:
                    msg = f"• {fornecedor} ({conta})"
                    if msg not in historicoEventos:
                        escreverRelatorio(msg)
                        print(msg)
                        historicoEventos.add(msg)
                        
            # Ignorados
            if eventosIgnorados:
                escreverRelatorio("\nFornecedores ignorados:")
                print("[Loop] Fornecedores ignorados:")
                for fornecedor, conta in eventosIgnorados:
                    msg = f"• {fornecedor} ({conta})"
                    if msg not in historicoEventos:
                        escreverRelatorio(msg)
                        print(msg)
                        historicoEventos.add(msg)

            resumo = f"\nResumo: {len(eventosProcessados)} processado(s) / {len(eventosIgnorados)} ignorado(s)"
            escreverRelatorio(resumo)
            print(resumo)
            escreverRelatorio("Fim do Relatório.\n")
            print("Fim do relatório.\n")
            consolidarRelatorioTMP()
        else:
            hora_chave = datetime.now().strftime("%H")
            if ultimoRelatorio.get("vazio") != hora_chave:
                texto = f"[{hora_atual}] Nenhuma alteração realizada."
                print(texto)
                escreverRelatorio(texto)
                consolidarRelatorioTMP()
                ultimoRelatorio["vazio"] = hora_chave

        # === Espera até próxima verificação ===
        print(f"[Loop] Aguardando {INTERVALO/60:.0f} minutos para próxima verificação...")
        for _ in range(int(INTERVALO)):
            if stop_event.is_set():
                break
            time.sleep(1)

    running = False
    print("[Loop] Encerrado.")


# =========================
# CONTROLE DE EXECUÇÃO
# =========================
def iniciar_verificacao():
    """Inicia o loop principal em thread separada (chamado pelo tray)."""
    global running
    if not running:
        stop_event.clear()
        t = threading.Thread(target=main_loop, daemon=True)
        t.start()
        print("[Main] Loop principal iniciado.")
    else:
        print("[Main] Loop já está em execução.")


def parar_verificacao():
    """Interrompe o loop principal."""
    global running
    if running:
        print("[Main] Parando loop principal...")
        stop_event.set()
        running = False
    else:
        print("[Main] Nenhum loop ativo para encerrar.")


def on_quit():
    """Chamado quando o usuário clica em 'Sair' no tray."""
    parar_verificacao()
    print("[Main] Encerrando Finance Bot...")
    time.sleep(1)
    sys.exit(0)

# =========================
# EXECUÇÃO PRINCIPAL
# =========================
if __name__ == "__main__":
    # Passa callbacks para o tray (para permitir controle)
    run_tray(on_quit_callback=on_quit, start_callback=iniciar_verificacao)