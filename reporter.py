import os
from datetime import datetime
from collections import Counter

from config import RELATORIO_DIR as relatorioDir

# Variaveis de relatorio de sessao
eventosProcessados = []
eventosIgnorados = []
eventosAvisos = []
historicoEventos = set()
historicoAvisos = set()
ocorrenciasAvisosDia = Counter()
diaOcorrencias = datetime.now().strftime("%Y-%m-%d")

ultimoRelatorio = {"Conta Principal": None, "Conta NFe": None}


def resetarOcorrenciasSeNovoDia():
    global diaOcorrencias
    hoje = datetime.now().strftime("%Y-%m-%d")
    if hoje != diaOcorrencias:
        diaOcorrencias = hoje
        ocorrenciasAvisosDia.clear()
        historicoEventos.clear()
        historicoAvisos.clear()


def limparRelatoriosAntigos():
    agora = datetime.now()
    for arquivo in os.listdir(relatorioDir):
        caminho = os.path.join(relatorioDir, arquivo)
        if os.path.isfile(caminho):
            modificacao = datetime.fromtimestamp(os.path.getmtime(caminho))
            if (agora - modificacao).days > 7:
                os.remove(caminho)


def obterArquivoRelatorio():
    dataHoje = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(relatorioDir, f"relatorio_{dataHoje}.txt")


def obterArquivoRelatorioTmp():
    return obterArquivoRelatorio() + ".tmp"


def escreverRelatorio(texto):
    arquivoRelatorio = obterArquivoRelatorio()
    try:
        with open(arquivoRelatorio, "a", encoding="utf-8") as f:
            f.write(texto + "\n")
    except PermissionError:
        with open(obterArquivoRelatorioTmp(), "a", encoding="utf-8") as f:
            f.write(texto + "\n")


def registrarEvento(tipo, fornecedor, conta):
    if fornecedor.strip() in ["-", ""]:
        return
    if any(x in fornecedor.upper() for x in [
        "ELETRONICA HORIZONTE COMERCIO DE PRODUTOS ELETRONICOS LTDA",
        "MVA COMERCIO DE PRODUTOS ELETRONICOS LTDA EPP"
    ]):
        return

    if tipo == "processado":
        eventosProcessados.append((fornecedor, conta))
    elif tipo == "ignorado":
        eventosIgnorados.append((fornecedor, conta))


def registrarAviso(mensagem, conta="Conta Principal"):
    if not mensagem:
        return
    chave = (mensagem.strip(), conta)
    eventosAvisos.append(chave)
    ocorrenciasAvisosDia[chave] += 1


def consolidarRelatorioTMP():
    """Move conteudo do .tmp diario para o .txt quando possivel."""
    arquivo_tmp = obterArquivoRelatorioTmp()
    arquivo_txt = obterArquivoRelatorio()
    if os.path.exists(arquivo_tmp):
        try:
            with open(arquivo_tmp, "r", encoding="utf-8") as f_temp:
                dados_temp = f_temp.read()
            with open(arquivo_txt, "a", encoding="utf-8") as f_rel:
                f_rel.write(dados_temp)
            os.remove(arquivo_tmp)
            print("Relatorio temporario consolidado com sucesso.")
        except PermissionError:
            pass
