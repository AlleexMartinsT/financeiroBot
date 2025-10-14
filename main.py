import os
import base64
import time
import xml.etree.ElementTree as ET
from datetime import datetime
import locale

import gspread
from google.oauth2.service_account import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# === CONFIGURAÇÕES ===
DOWNLOAD_DIR = "xmls_baixados"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
INTERVALO = 600 # Verificação automática a cada 5 minutos
emailsProcessados = set()

# === Escopos ===
SCOPES_GMAIL = ["https://www.googleapis.com/auth/gmail.modify"]
SCOPES_SHEET = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')

# === IDs das planilhas ===
SHEET_EH_2025 = "1EyBMQ9FjkQjw133OehersWtAXqgQq8sog7I-h6iEY3c"
SHEET_EH_2026 = "1SewYK2Y-qUB5Wi1xG3Glbh_1wuJ6RXycCeI9K3uThQE"
SHEET_MVA_2025 = "1n1rf18R03LJORAt9xsaWOSwvGRMAJcps_KNxm5lqda8"
SHEET_MVA_2026 = "1pyGP_yoB93SBOS3M3eAxmd05QYaXf6UE3tjhQZWRiSA"

# === CNPJs das suas empresas ===
CNPJ_EH = "34636193000193"
CNPJ_MVA = "18471209000107"

# === AUTENTICAÇÃO Gmail (OAuth pessoal) ===

def apiCooldown():
    print("Limite da API atingida, aguardando 2 minutos e tentando novamente...")
    time.sleep(120)
    
def autenticarGmail(cred_file):
    flow = InstalledAppFlow.from_client_secrets_file(cred_file, SCOPES_GMAIL)
    creds = flow.run_local_server(port=0)
    return build("gmail", "v1", credentials=creds)

# Autenticar as duas contas
gmailPrincipal = autenticarGmail("credentials_gmail.json")
gmailNFE = autenticarGmail("credentials_gmailNFE.json")

# === AUTENTICAÇÃO Google Sheets (Conta de serviço) ===
credsSheets = Credentials.from_service_account_file("credentials.json", scopes=SCOPES_SHEET)
sheetsClient = gspread.authorize(credsSheets)

planilhasCache = {}

def getPlanilha(chave):
    if chave in planilhasCache:
        return planilhasCache[chave]
    planilhasID = {
        "EH_2025": SHEET_EH_2025,
        "EH_2026": SHEET_EH_2026,
        "MVA_2025": SHEET_MVA_2025,
        "MVA_2026": SHEET_MVA_2026
    }
    for _ in range(3):
        try:
            planilha = sheetsClient.open_by_key(planilhasID[chave])
            planilhasCache[chave] = planilha
            return planilha
        except gspread.exceptions.APIError as e:
            if "429" in str(e):
                apiCooldown()
                continue
            else:
                raise e
    print(f"Falha ao abrir a planilha {chave} após múltiplas tentativas.")
    return None

# === Função para escolher planilha pelo CNPJ + ano ===
def escolherPlanilha(cnpjDest, ano):
    if cnpjDest == CNPJ_EH:
        empresa = "EH"
    elif cnpjDest == CNPJ_MVA:
        empresa = "MVA"
    else:
        return None, None
    chave = f"{empresa}_{ano}"
    return getPlanilha(chave), empresa

# === RELATÓRIO ===
relatorioDir = "relatorios"
os.makedirs(relatorioDir, exist_ok=True)
eventosProcessados = []
eventosIgnorados = []
historicoEventos = set()

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

def escreverRelatorio(texto):
    arquivoRelatorio = obterArquivoRelatorio()
    try:
        with open(arquivoRelatorio, "a", encoding="utf-8") as f:
            f.write(texto + "\n")
    except PermissionError:
        with open(arquivoRelatorio + ".tmp", "a", encoding="utf-8") as f:
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

def consolidarRelatorioTMP():
    """Move conteúdo do .tmp para o .txt quando possível."""
    if os.path.exists(RELATORIO_TEMP):
        try:
            with open(RELATORIO_TEMP, "r", encoding="utf-8") as f_temp:
                dados_temp = f_temp.read()
            with open(RELATORIO_TXT, "a", encoding="utf-8") as f_rel:
                f_rel.write(dados_temp)
            os.remove(RELATORIO_TEMP)
            print("Relatório temporário consolidado com sucesso.")
        except PermissionError:
            pass

def extrairFornecedor(file_path):
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        ns = {
            'nfe': 'http://www.portalfiscal.inf.br/nfe',
            'cte': 'http://www.portalfiscal.inf.br/cte'
        }

        # Busca o emitente de forma segura
        emit = root.find('.//nfe:emit', ns)
        if emit is None:
            emit = root.find('.//cte:emit', ns)

        if emit is not None:
            nome = emit.find('nfe:xNome', ns)
            if nome is None:
                nome = emit.find('cte:xNome', ns)

            if nome is not None and nome.text:
                return nome.text.strip().upper()

        return "DESCONHECIDO"

    except Exception:
        return "DESCONHECIDO"

# === Processar NF-e ===
def processarNFE(root, filePath):
    ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
    emit = root.find('.//nfe:emit', ns)
    dest = root.find('.//nfe:dest', ns)
    ide = root.find('.//nfe:ide', ns)
    total = root.find('.//nfe:ICMSTot', ns)
    duplicatas = root.findall('.//nfe:cobr/nfe:dup', ns)

    fornecedor = emit.find('nfe:xNome', ns).text if emit is not None else "-"
    fornecedor = f"{fornecedor} (Bot)"
    nfNum = ide.find('nfe:nNF', ns).text if ide is not None else "-"
    valorTotal = float(total.find('nfe:vNF', ns).text) if total is not None else 0.0
    cnpjDest = dest.find('nfe:CNPJ', ns).text if dest is not None else ""

    parcelas = []
    
    if cnpjDest not in [CNPJ_EH, CNPJ_MVA]:
        print(f"NF {nfNum} ignorada — nota de saída ({filePath})")
        registrarEvento("ignorado", fornecedor, "Conta Principal")
        return
    
    for dup in duplicatas:
        vencimento = dup.find('nfe:dVenc', ns).text if dup.find('nfe:dVenc', ns) is not None else "-"
        valor = float(dup.find('nfe:vDup', ns).text) if dup.find('nfe:vDup', ns) is not None else 0.0
        parcelas.append((nfNum, vencimento, valor))
        
    qtdParcelas = len(parcelas) 

    for i, (num, vencimento, valor) in enumerate(parcelas, start=1):
        try:
            dataVencimento = datetime.strptime(vencimento, "%Y-%m-%d")
        except:
            continue

        ano = dataVencimento.year
        planilha, empresa = escolherPlanilha(cnpjDest, ano)
        if not planilha:
            print(f"CNPJ {cnpjDest} não corresponde a nenhuma planilha ({filePath})")
            continue

        nomeAba = dataVencimento.strftime("%b/%Y").capitalize()
        try:
            for _ in range(3):
                try:
                    aba = planilha.worksheet(nomeAba)
                    break
                except gspread.exceptions.APIError as e:
                    if "429" in str(e):
                        apiCooldown()
                        continue
                    else:
                        raise e
            else:
                print(f"Não foi possível acessar a aba {nomeAba} após múltiplas tentativas.")
                return
        except gspread.exceptions.WorksheetNotFound:
            aba = planilha.add_worksheet(title=nomeAba, rows="100", cols="9")
            aba.append_row(["Vencimento", "Descrição", "NF", "Valor Total", "Qtd Parcelas", "Parcela", "Valor Parcela", "Valor Pago", "Status"])

        for _ in range(3):
            try:
                dados = aba.get_all_values()
                break
            except gspread.exceptions.APIError as e:
                if "429" in str(e):
                    apiCooldown()
                    continue
                else:
                    raise e
        else:
            print("Falha ao obter dados da aba após múltiplas tentativas.")
            return

        dadosValidos = [linha for linha in dados if len(linha) >= 3 and linha[0] and linha[2] and "Vencimento" not in linha[0]]
        duplicado = any(
            num == linha[2].strip() and dataVencimento.strftime("%d/%m/%Y") == linha[0].strip()
            for linha in dadosValidos
        )
        if duplicado:
            print(f"NF {num} ({dataVencimento.strftime('%d/%m/%Y')}) já existe em {empresa} {ano} / {nomeAba}")
            continue

        novaLinha = [
            dataVencimento.strftime("%d/%m/%Y"),
            fornecedor,
            num,
            f"R$ {valorTotal:,.2f}",
            qtdParcelas,
            f"{i}ª Parcela",
            f"R$ {valor:,.2f}",
            "",
            ""
        ]
        
        for _ in range(3):
            try:
                aba.append_row(novaLinha, value_input_option="USER_ENTERED")
                break
            except gspread.exceptions.APIError as e:
                if "429" in str(e):
                    apiCooldown()
                    continue
                else:
                    raise e
                       
        print(f"Inserido: {empresa} {ano} | {nomeAba} | Parcela {i}/{qtdParcelas} - {fornecedor} - {num}")
        registrarEvento("processado", fornecedor, "Conta Principal")
        
# === Função auxiliar: cria ou obtém o ID do rótulo "XML Processado" ===
def getLabelID(gmail_service, label_name="XML Processado"):
    """Obtém o ID do rótulo ou cria caso não exista."""
    labels = gmail_service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"].lower() == label_name.lower():
            return label["id"]

    # Cria o rótulo se não existir
    novoLabel = gmail_service.users().labels().create(
        userId="me",
        body={"name": label_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
    ).execute()
    print(f"Rótulo criado: {label_name}")
    return novoLabel["id"]

# === Atualização: processar CT-e (com filtro de Fornecedores) ===
def processarCTE(root, filePath):
    ns = {'cte': 'http://www.portalfiscal.inf.br/cte'}
    emit = root.find('.//cte:emit', ns)
    dest = root.find('.//cte:dest', ns)
    ide = root.find('.//cte:ide', ns)
    total = root.find('.//cte:vPrest/cte:vTPrest', ns)
    entrega = root.find('.//cte:compl/cte:Entrega/cte:comData/cte:dProg', ns)

    fornecedor = emit.find('cte:xNome', ns).text if emit is not None else "-"
    if any(x in fornecedor.upper() for x in ["BRASPRESS", "DOMINIO"]):
        print(f"Ignorado CT-e de transportadora ({filePath})")
        try:
            os.remove(filePath)
        except:
            pass
        return

    fornecedor = f"{fornecedor} (Bot)"
    nfNum = ide.find('cte:nCT', ns).text if ide is not None else "-"
    valorTotal = float(total.text) if total is not None else 0.0
    cnpjDest = dest.find('cte:CNPJ', ns).text if dest is not None else ""

    vencimento = entrega.text if entrega is not None else None

    if cnpjDest not in [CNPJ_EH, CNPJ_MVA]:
        print(f"CT-e {nfNum} ignorado — nota de saída ({filePath})")
        registrarEvento("ignorado", fornecedor, "Conta NFe")
        os.remove(filePath)
        return

    if not vencimento:
        print(f"CT-e {nfNum} sem data de vencimento, pulando.")
        os.remove(filePath)
        return

    try:
        dataVencimento = datetime.strptime(vencimento, "%Y-%m-%d")
    except:
        os.remove(filePath)
        return

    ano = dataVencimento.year
    planilha, empresa = escolherPlanilha(cnpjDest, ano)
    if not planilha:
        print(f"CNPJ {cnpjDest} não corresponde a nenhuma planilha ({filePath})")
        os.remove(filePath)
        return

    nomeAba = dataVencimento.strftime("%b/%Y").capitalize()
    try:
        aba = planilha.worksheet(nomeAba)
    except gspread.exceptions.WorksheetNotFound:
        aba = planilha.add_worksheet(title=nomeAba, rows="100", cols="9")
        aba.append_row(["Vencimento", "Descrição", "CT-e", "Valor Total", "Qtd Parcelas", "Parcela", "Valor Parcela", "Valor Pago", "Status"])

    dados = aba.get_all_values()
    duplicado = any(
        nfNum == linha[2].strip() and dataVencimento.strftime("%d/%m/%Y") == linha[0].strip()
        for linha in dados if len(linha) >= 3
    )

    if duplicado:
        print(f"CT-e {nfNum} ({dataVencimento.strftime('%d/%m/%Y')}) já existe em {empresa} {ano} / {nomeAba}")
        os.remove(filePath)
        return

    novaLinha = [
        dataVencimento.strftime("%d/%m/%Y"),
        fornecedor,
        nfNum,
        f"R$ {valorTotal:,.2f}",
        1,
        "1ª Parcela",
        f"R$ {valorTotal:,.2f}",
        "",
        ""
    ]
    
    aba.append_row(novaLinha, value_input_option="USER_ENTERED")
    print(f"Inserido: {empresa} {ano} | {nomeAba} | Parcela 1/1 - {fornecedor} - {nfNum}")
    registrarEvento("processado", fornecedor, "Conta NFe")

    # Apagar XML após processar
    try:
        os.remove(filePath)
        print(f"XML removido: {filePath}")
    except:
        pass
    time.sleep(1.0)
   
# === Função principal para decidir o tipo do XML ===
def processarXML(filePath):
    tree = ET.parse(filePath)
    root = tree.getroot()
    tag = root.tag.lower()
    if tag.endswith("nfeproc"):
        processarNFE(root, filePath)
    elif tag.endswith("cteproc"):
        processarCTE(root, filePath)
    else:
        print(f"Tipo de XML desconhecido ({filePath})")

# === Buscar e baixar novos XMLs ===
def processarEmails(gmail_service, origemNome):
    global emailsProcessados

    labelID = getLabelID(gmail_service, "XML Processado")
    results = gmail_service.users().messages().list(
        userId="me",
        q="has:attachment filename:xml in:inbox -in:sent -in:drafts",
        maxResults=15
    ).execute()

    messages = results.get("messages", [])
    print(f"({origemNome}) {len(messages)} e-mails com XML encontrados")

    emailsSemXML = 0
    xmlsProcessadosTOTAL = 0

    for msg in messages:
        msgID = msg["id"]
        if msgID in emailsProcessados:
            continue

        try:
            message = gmail_service.users().messages().get(
                userId="me",
                id=msgID,
                format="full"
            ).execute()
        except Exception as e:
            if "[WinError 2]" in str(e):
                continue
            print(f"({origemNome}) Erro ao acessar e-mail: {e}")
            continue

        def buscarPartes(partes):
            encontrados = []
            for p in partes:
                if p.get("parts"):
                    encontrados.extend(buscarPartes(p["parts"]))
                elif p.get("filename", "").lower().endswith(".xml") and "attachmentId" in p.get("body", {}):
                    encontrados.append(p)
            return encontrados

        parts = message.get("payload", {}).get("parts", [])
        anexosXML = buscarPartes(parts)

        if not anexosXML:
            emailsSemXML += 1
            continue

        xmlsEncontrados = 0
        for part in anexosXML:
            filename = part.get("filename")
            attachID = part["body"].get("attachmentId")

            if not filename or not attachID:
                continue

            filePath = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.exists(filePath):
                continue

            try:
                attachment = gmail_service.users().messages().attachments().get(
                    userId="me", messageId=msgID, id=attachID
                ).execute()

                data = attachment.get("data")
                fileData = base64.urlsafe_b64decode(data.encode("UTF-8"))
                with open(filePath, "wb") as f:
                    f.write(fileData)

                # Filtros — transportadoras e CNPJs próprios
                if any(x in filename.upper() for x in ["BRASPRESS", "DOMINIO"]):
                    os.remove(filePath)
                    continue
                fornecedor_xml = extrairFornecedor(filePath)
                if fornecedor_xml in ["ELETRONICA HORIZONTE COMERCIO DE PRODUTOS ELETRONICOS LTDA",
                                      "MVA COMERCIO DE PRODUTOS ELETRONICOS LTDA EPP"]:
                    os.remove(filePath)
                    continue

                print(f"XML salvo: {filePath}")
                processarXML(filePath)
                xmlsEncontrados += 1
                xmlsProcessadosTOTAL += 1

                try:
                    os.remove(filePath)
                except FileNotFoundError:
                    pass
                time.sleep(1)

            except Exception as e:
                if "[WinError 2]" in str(e):
                    continue
                print(f"({origemNome}) Erro ao processar anexo: {e}")
                continue

        if xmlsEncontrados > 0:
            gmail_service.users().messages().modify(
                userId="me",
                id=msgID,
                body={
                    "removeLabelIds": ["UNREAD"],
                    "addLabelIds": [labelID]
                }
            ).execute()
            emailsProcessados.add(msgID)
        else:
            emailsSemXML += 1

    if xmlsProcessadosTOTAL > 0:
        print(f"({origemNome}) {xmlsProcessadosTOTAL} XML(s) processado(s).")
    elif emailsSemXML < len(messages):
        print(f"({origemNome}) Nenhum XML válido processado.")
    else:
        pass  # Evita spam quando não há mudanças

    print(f"({origemNome}) Verificação finalizada.\n")
    limparRelatoriosAntigos()

# === LOOP AUTOMÁTICO (com relatório detalhado e suporte a arquivo aberto) ===

RELATORIO_TXT = "relatorio_status.txt"
RELATORIO_TEMP = "relatorio_temp.tmp"
ultimoRelatorio = {"Conta Principal": None, "Conta NFe": None}

while True:
    hora_atual = datetime.now().strftime("%H:%M - %d/%m/%Y")
    print("\n Verificando e-mails...")

    eventosProcessados.clear() # Limpa os eventos do ciclo
    eventosIgnorados.clear() # Limpa os eventos do ciclo

    try:
        processarEmails(gmailPrincipal, "Conta Principal")
        print("Aguardando 10 segundos antes da próxima conta...")
        time.sleep(10)
        processarEmails(gmailNFE, "Conta NFe")
    except Exception as e:
        if "[WinError 2]" not in str(e):
            print(f"Erro: {e}")
            escreverRelatorio(f"[{hora_atual}] Erro: {e}")


    # === Montar relatório de resumo ===
    mensagens = []
    if eventosProcessados or eventosIgnorados:
        escreverRelatorio(f"\nRelatório de {hora_atual}")
        print(f"\nRelatório de {hora_atual}")

        # Processados
        if eventosProcessados:
            escreverRelatorio("Fornecedores processados:")
            print("Fornecedores processados:")
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
        escreverRelatorio("Fim do relatório.\n")
        print("Fim do relatório.\n")
        consolidarRelatorioTMP()

    else:
        hora_chave = datetime.now().strftime("%H")  # identifica a hora atual
        if ultimoRelatorio.get("vazio") != hora_chave:
            texto = f"[{hora_atual}] Nenhuma alteração realizada."
            print(texto)
            escreverRelatorio(texto)
            consolidarRelatorioTMP()
            ultimoRelatorio["vazio"] = hora_chave

    print(f"Aguardando {INTERVALO/60:.0f} minutos para próxima verificação...")
    time.sleep(INTERVALO)