import os
import sys
import time
import threading
from datetime import datetime

from tray_icon import run_tray
from auth import gmailPrincipal, gmailNFE
from gmail_fetcher import processarEmails
from reporter import (
    eventosProcessados,
    eventosIgnorados,
    eventosAvisos,
    ocorrenciasAvisosDia,
    historicoEventos,
    resetarOcorrenciasSeNovoDia,
    escreverRelatorio,
    consolidarRelatorioTMP,
    ultimoRelatorio,
)
from config import INTERVALO, DOWNLOAD_DIR


running = False
stop_event = threading.Event()


def limpar_xmls_baixados():
    if not os.path.isdir(DOWNLOAD_DIR):
        return
    removidos = 0
    for nome in os.listdir(DOWNLOAD_DIR):
        caminho = os.path.join(DOWNLOAD_DIR, nome)
        if os.path.isfile(caminho):
            try:
                os.remove(caminho)
                removidos += 1
            except Exception:
                pass
    if removidos:
        print(f"[Loop] Limpeza inicial: {removidos} arquivo(s) removido(s) de {DOWNLOAD_DIR}.")


def main_loop():
    global running
    running = True
    print("[Loop] Iniciando verificacao automatica.")
    limpar_xmls_baixados()

    while not stop_event.is_set():
        limpar_xmls_baixados()
        resetarOcorrenciasSeNovoDia()
        hora_atual = datetime.now().strftime("%H:%M - %d/%m/%Y")
        print("\n[Loop] Verificando e-mails...")

        eventosProcessados.clear()
        eventosIgnorados.clear()
        eventosAvisos.clear()

        try:
            processarEmails(gmailPrincipal, "Conta Principal")
            print("[Loop] Aguardando 10 segundos antes da proxima conta...")
            time.sleep(10)
            processarEmails(gmailNFE, "Conta NFe")
        except Exception as e:
            if "[WinError 2]" not in str(e):
                print(f"[Loop] Erro: {e}")
                escreverRelatorio(f"[{hora_atual}] Erro: {e}")

        if eventosProcessados or eventosIgnorados or eventosAvisos:
            escreverRelatorio(f"\nRelatorio de {hora_atual}")

            if eventosProcessados:
                escreverRelatorio("Fornecedores processados:")
                print("[Loop] Fornecedores processados:")
                for fornecedor, conta in eventosProcessados:
                    msg = f"- {fornecedor} ({conta})"
                    if msg not in historicoEventos:
                        escreverRelatorio(msg)
                        print(msg)
                        historicoEventos.add(msg)

            if eventosIgnorados:
                escreverRelatorio("\nFornecedores ignorados:")
                print("[Loop] Fornecedores ignorados:")
                for fornecedor, conta in eventosIgnorados:
                    msg = f"- {fornecedor} ({conta})"
                    if msg not in historicoEventos:
                        escreverRelatorio(msg)
                        print(msg)
                        historicoEventos.add(msg)

            if eventosAvisos:
                escreverRelatorio("\nAvisos de notas com problema (acumulado do dia):")
                print("[Loop] Avisos de notas com problema (acumulado do dia):")
                for (mensagem, conta), qtd in ocorrenciasAvisosDia.most_common():
                    msg = f"- {mensagem} ({conta})"
                    if qtd > 1:
                        msg += f" (ocorreu {qtd}x)"
                    escreverRelatorio(msg)
                    print(msg)

            resumo = (
                f"\nResumo: {len(eventosProcessados)} processado(s) / "
                f"{len(eventosIgnorados)} ignorado(s) / "
                f"{len(eventosAvisos)} aviso(s) no ciclo / "
                f"{sum(ocorrenciasAvisosDia.values())} aviso(s) no dia"
            )
            escreverRelatorio(resumo)
            print(resumo)
            escreverRelatorio("Fim do relatorio.\n")
            print("Fim do relatorio.\n")
            consolidarRelatorioTMP()
        else:
            hora_chave = datetime.now().strftime("%H")
            if ultimoRelatorio.get("vazio") != hora_chave:
                texto = f"[{hora_atual}] Nenhuma alteracao realizada."
                print(texto)
                escreverRelatorio(texto)
                consolidarRelatorioTMP()
                ultimoRelatorio["vazio"] = hora_chave

        print(f"[Loop] Aguardando {INTERVALO/60:.0f} minutos para proxima verificacao...")
        for _ in range(int(INTERVALO)):
            if stop_event.is_set():
                break
            time.sleep(1)

    running = False
    print("[Loop] Encerrado.")


def iniciar_verificacao():
    global running
    if not running:
        stop_event.clear()
        t = threading.Thread(target=main_loop, daemon=True)
        t.start()
        print("[Main] Loop principal iniciado.")
    else:
        print("[Main] Loop ja esta em execucao.")


def parar_verificacao():
    global running
    if running:
        print("[Main] Parando loop principal...")
        stop_event.set()
        running = False
    else:
        print("[Main] Nenhum loop ativo para encerrar.")


def on_quit():
    parar_verificacao()
    print("[Main] Encerrando Finance Bot...")
    time.sleep(1)
    sys.exit(0)


if __name__ == "__main__":
    iniciar_verificacao()
    run_tray(on_quit_callback=on_quit, start_callback=iniciar_verificacao)
