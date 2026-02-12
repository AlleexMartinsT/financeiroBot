import os
import sys
import time
import argparse
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import auth
from gmail_fetcher import processarEmails
from panel_web import start_control_panel
import runtime_status
from auto_updater import AutoUpdater
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
from settings_manager import load_settings


running = False
stop_event = threading.Event()
_auto_updater = None


def _is_transient_api_error(exc: Exception) -> bool:
    txt = str(exc or "").lower()
    keys = (
        "timed out",
        "timeout",
        "connection reset",
        "ssl",
        "wrong_version_number",
        "temporar",
    )
    return any(k in txt for k in keys)


def _processar_conta_com_recuperacao(account: str, origem: str):
    service = auth.get_gmail_service(account)
    try:
        processarEmails(service, origem)
        return
    except Exception as e:
        if not _is_transient_api_error(e):
            raise
        print(f"[Loop] Falha temporaria em {origem}. Tentando reconectar servico e repetir...")
        time.sleep(2)
        service = auth.get_gmail_service(account, force_refresh=True)
        processarEmails(service, origem)


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


def _setup_auto_updater():
    global _auto_updater
    cfg = load_settings()
    _auto_updater = AutoUpdater(
        repo_dir=Path(__file__).resolve().parent,
        enabled=bool(cfg.get("auto_update_enabled", True)),
        interval_minutes=int(cfg.get("auto_update_interval_minutes", 5)),
        remote=str(cfg.get("auto_update_remote", "origin")),
        branch=str(cfg.get("auto_update_branch", "main")),
    )
    _auto_updater.start()


def _restart_process(reason: str):
    print(f"[Updater] {reason}. Reiniciando processo...")
    try:
        args = [sys.executable] + sys.argv
        subprocess.Popen(args, cwd=str(Path(__file__).resolve().parent))
    finally:
        os._exit(0)


def _check_and_restart_if_update():
    if _auto_updater and _auto_updater.consume_restart_request():
        _restart_process("Atualizacao aplicada")


def main_loop():
    global running
    running = True
    print("[Loop] Iniciando verificação automatica.")
    limpar_xmls_baixados()
    runtime_status.set_account_status("principal", "waiting", "Aguardando ciclo.")
    runtime_status.set_account_status("nfe", "waiting", "Aguardando ciclo.")

    while not stop_event.is_set():
        _check_and_restart_if_update()
        runtime_status.clear_next_cycle()
        limpar_xmls_baixados()
        resetarOcorrenciasSeNovoDia()
        hora_atual = datetime.now().strftime("%H:%M - %d/%m/%Y")
        print("\n[Loop] Verificando e-mails...")

        eventosProcessados.clear()
        eventosIgnorados.clear()
        eventosAvisos.clear()

        runtime_status.set_account_status("principal", "running", "Executando leitura da conta principal...")
        try:
            _processar_conta_com_recuperacao("principal", "Conta Principal")
            runtime_status.set_account_status("principal", "ok", "Funcionando.")
        except Exception as e:
            runtime_status.set_account_status("principal", "error", str(e))
            if "[WinError 2]" not in str(e):
                print(f"[Loop] Erro (principal): {e}")
                escreverRelatorio(f"[{hora_atual}] Erro (principal): {e}")

        _check_and_restart_if_update()
        print("[Loop] Aguardando 10 segundos antes da proxima conta...")
        time.sleep(10)

        runtime_status.set_account_status("nfe", "running", "Executando leitura da conta NFe...")
        try:
            _processar_conta_com_recuperacao("nfe", "Conta NFe")
            runtime_status.set_account_status("nfe", "ok", "Funcionando.")
        except Exception as e:
            runtime_status.set_account_status("nfe", "error", str(e))
            if "[WinError 2]" not in str(e):
                print(f"[Loop] Erro (NFe): {e}")
                escreverRelatorio(f"[{hora_atual}] Erro (NFe): {e}")

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

        cfg = load_settings()
        interval_min = int(cfg.get("loop_interval_minutes", max(1, int(INTERVALO / 60))))
        interval_sec = max(60, interval_min * 60)
        print(f"[Loop] Aguardando {interval_sec/60:.0f} minutos para proxima verificacao...")
        runtime_status.set_next_cycle(int(interval_sec))
        remaining = int(interval_sec)
        while remaining > 0:
            _check_and_restart_if_update()
            reset_sec = runtime_status.consume_next_cycle_reset()
            if reset_sec is not None:
                remaining = int(reset_sec)
                runtime_status.set_next_cycle(int(remaining))
                print(f"[Loop] Proxima verificacao reagendada para {max(1, int(remaining/60))} minuto(s).")
                continue
            if stop_event.is_set():
                break
            time.sleep(1)
            remaining -= 1
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
        print("[Main] Loop ja esta em execuÃ§Ã£o.")


def parar_verificacao():
    global running
    if running:
        print("[Main] Parando loop principal...")
        stop_event.set()
        running = False
    else:
        print("[Main] Nenhum loop ativo para encerrar.")


def on_quit():
    if _auto_updater:
        _auto_updater.stop()
    parar_verificacao()
    print("[Main] Encerrando Finance Bot...")
    time.sleep(1)
    sys.exit(0)


def _parse_args():
    parser = argparse.ArgumentParser(description="FinanceBot")
    parser.add_argument("--server", action="store_true", help="Executa em modo servidor (sem tray)")
    parser.add_argument("--no-browser", action="store_true", help="Nao abre navegador no start")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    cfg = load_settings()
    panel_host = str(cfg.get("panel_bind_host", "0.0.0.0"))
    panel_port = int(cfg.get("panel_port", 8765))
    open_browser = not args.no_browser and not args.server
    panel_url = start_control_panel(host=panel_host, port=panel_port, open_browser=open_browser)
    print(f"[Main] Painel web: {panel_url}")
    _setup_auto_updater()
    print("[Main] Aguardando 5 segundos para iniciar a verificacao...")
    time.sleep(5)
    iniciar_verificacao()
    if args.server:
        print("[Main] Modo servidor ativo. Tray desativado.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            on_quit()
    else:
        from tray_icon import run_tray

        run_tray(on_quit_callback=on_quit, start_callback=iniciar_verificacao, panel_url=panel_url)



