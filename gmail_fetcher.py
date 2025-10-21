# gmail_fetcher.py
import os
import base64
import time

from config import DOWNLOAD_DIR
from processor import processarXML, extrairFornecedor
from reporter import limparRelatoriosAntigos

# Guarda mensagens já processadas na sessão
emailsProcessados = set()

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

def processarEmails(gmail_service, origemNome):
    """Busca e baixa XMLs de uma conta Gmail e os processa."""
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
                if any(x in filename.upper() for x in ["DOMINIO"]):
                    try:
                        os.remove(filePath)
                    except:
                        pass
                    continue
                fornecedor_xml = extrairFornecedor(filePath)
                if fornecedor_xml in ["ELETRONICA HORIZONTE COMERCIO DE PRODUTOS ELETRONICOS LTDA",
                                      "MVA COMERCIO DE PRODUTOS ELETRONICOS LTDA EPP"]:
                    try:
                        os.remove(filePath)
                    except:
                        pass
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
