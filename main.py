import os
import sys
import time
import threading
from datetime import datetime

from tray_icon import run_tray  # você já possui este arquivo
from auth import gmailPrincipal, gmailNFE
from gmail_fetcher import processarEmails
from reporter import (
    eventosProcessados, eventosIgnorados, historicoEventos,
    escreverRelatorio, consolidarRelatorioTMP, ultimoRelatorio
)
from config import INTERVALO

# Controle de execução global
running = True

def main_loop():
    global running
    while running:
        hora_atual = datetime.now().strftime("%H:%M - %d/%m/%Y")
        print("\nVerificando e-mails...")

        eventosProcessados.clear()
        eventosIgnorados.clear()

        try:
            processarEmails(gmailPrincipal, "Conta Principal")
            print("Aguardando 10 segundos antes da próxima conta...")
            time.sleep(10)
            processarEmails(gmailNFE, "Conta NFe")
        except Exception as e:
            if "[WinError 2]" not in str(e):
                print(f"Erro: {e}")
                escreverRelatorio(f"[{hora_atual}] Erro: {e}")

        # === Relatório de Resumo ===
        if eventosProcessados or eventosIgnorados:
            escreverRelatorio(f"\nRelatório de {hora_atual}")
            
            # Processados 
            if eventosProcessados:
                escreverRelatorio("Fornecedores processados: ")
                print("Fornecedores Processados:")
                for fornecedor, conta in eventosProcessados:
                    msg = f"• {fornecedor} ({conta})"
                    if msg not in historicoEventos:
                        escreverRelatorio(msg)
                        print(msg)
                        historicoEventos.add(msg)
                        
            # Ignorados
            if eventosIgnorados:
                escreverRelatorio("\nFornecedores ignorados:")
                print("\nFornecedores ignorados:")
                for fornecedor, conta in eventosIgnorados:
                    msg = f"• {fornecedor} ({conta})"
                    if msg not in historicoEventos:
                        escreverRelatorio(msg)
                        print(msg)
                        historicoEventos.add(msg)

            resumo = f"\nResumo: {len(eventosProcessados)} processado(s) / {len(eventosIgnorados)} ignorado(s)"
            escreverRelatorio(resumo)
            print(resumo)
            escreverRelatorio("Fim do Relatório. \n")
            print("Fim do relatório. \n")
            consolidarRelatorioTMP()
        else:
            hora_chave = datetime.now().strftime("%H")  # Identifica a hora atual
            if ultimoRelatorio.get("vazio") != hora_chave:
                texto = f"[{hora_atual}] Nenhuma alteração realizada."
                print(texto)
                escreverRelatorio(texto)
                consolidarRelatorioTMP()
                ultimoRelatorio["vazio"] = hora_chave

        print(f"Aguardando {INTERVALO/60:.0f} minutos para próxima verificação...")
        for _ in range(int(INTERVALO)):  # para permitir saída mais suave
            if not running:
                break
            time.sleep(1)
    print("Loop principal encerrado.")

def on_quit():
    """Chamado quando o usuário clica em 'Sair' no tray."""
    global running
    running = False
    print("Encerrando Finance Bot...")
    time.sleep(1)
    sys.exit(0)

if __name__ == "__main__":
    # Executa o loop principal em thread separada
    thread_main = threading.Thread(target=main_loop, daemon=True)
    thread_main.start()

    # Executa o ícone da bandeja (bloqueante)
    run_tray(on_quit)
