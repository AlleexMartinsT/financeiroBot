import os
import base64
import time
from datetime import datetime, timedelta

from config import DOWNLOAD_DIR
from processor import processarXML, extrairFornecedor
from reporter import limparRelatoriosAntigos

# Janela de busca de e-mails:
# - "last_30_days": hoje ate 30 dias atras
# - "current_and_previous_month": mes atual + mes anterior
FILTRO_PERIODO_EMAILS = "last_30_days"


def _query_periodo():
    base = (
        'has:attachment filename:xml in:inbox -in:sent -in:drafts '
        '-label:"XML Processado" -label:"XML Analisado"'
    )
    hoje = datetime.now().date()

    if FILTRO_PERIODO_EMAILS == "current_and_previous_month":
        primeiro_dia_mes_atual = hoje.replace(day=1)
        ultimo_dia_mes_anterior = primeiro_dia_mes_atual - timedelta(days=1)
        primeiro_dia_mes_anterior = ultimo_dia_mes_anterior.replace(day=1)
        after = primeiro_dia_mes_anterior.strftime("%Y/%m/%d")
        before = (hoje + timedelta(days=1)).strftime("%Y/%m/%d")
    else:
        after = (hoje - timedelta(days=30)).strftime("%Y/%m/%d")
        before = (hoje + timedelta(days=1)).strftime("%Y/%m/%d")

    return f"{base} after:{after} before:{before}"


def getLabelID(gmail_service, label_name):
    labels = gmail_service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"].lower() == label_name.lower():
            return label["id"]

    novoLabel = gmail_service.users().labels().create(
        userId="me",
        body={"name": label_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
    ).execute()
    print(f"Rotulo criado: {label_name}")
    return novoLabel["id"]


def processarEmails(gmail_service, origemNome):
    """Busca e baixa XMLs de uma conta Gmail e os processa."""
    label_processado = getLabelID(gmail_service, "XML Processado")
    label_analisado = getLabelID(gmail_service, "XML Analisado")

    query = _query_periodo()
    max_paginas = 3
    page_size = 50

    mensagens_brutas = []
    next_page_token = None
    for _ in range(max_paginas):
        req = gmail_service.users().messages().list(
            userId="me",
            q=query,
            maxResults=page_size,
            pageToken=next_page_token,
        )
        results = req.execute()
        mensagens_brutas.extend(results.get("messages", []))
        next_page_token = results.get("nextPageToken")
        if not next_page_token:
            break

    vistos = set()
    messages = []
    for m in mensagens_brutas:
        mid = m.get("id")
        if mid and mid not in vistos:
            vistos.add(mid)
            messages.append(m)

    print(f"({origemNome}) {len(messages)} e-mails com XML encontrados")

    emailsSemXML = 0
    xmlsProcessadosTOTAL = 0

    for msg in messages:
        msgID = msg["id"]

        try:
            message = gmail_service.users().messages().get(
                userId="me",
                id=msgID,
                format="full",
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

        xmlsInseridos = 0
        tentou_analisar = False

        for part in anexosXML:
            filename = part.get("filename")
            attachID = part["body"].get("attachmentId")

            if not filename or not attachID:
                continue

            filePath = os.path.join(DOWNLOAD_DIR, filename)

            try:
                attachment = gmail_service.users().messages().attachments().get(
                    userId="me", messageId=msgID, id=attachID
                ).execute()

                data = attachment.get("data")
                fileData = base64.urlsafe_b64decode(data.encode("UTF-8"))
                with open(filePath, "wb") as f:
                    f.write(fileData)

                if any(x in filename.upper() for x in ["DOMINIO"]):
                    try:
                        os.remove(filePath)
                    except Exception:
                        pass
                    tentou_analisar = True
                    continue

                fornecedor_xml = extrairFornecedor(filePath)
                if fornecedor_xml in [
                    "ELETRONICA HORIZONTE COMERCIO DE PRODUTOS ELETRONICOS LTDA",
                    "MVA COMERCIO DE PRODUTOS ELETRONICOS LTDA EPP",
                ]:
                    try:
                        os.remove(filePath)
                    except Exception:
                        pass
                    tentou_analisar = True
                    continue

                print(f"XML salvo: {filePath}")
                inseriu = processarXML(filePath)
                tentou_analisar = True
                if inseriu:
                    xmlsInseridos += 1
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

        if tentou_analisar:
            add_labels = [label_analisado]
            if xmlsInseridos > 0:
                add_labels.append(label_processado)

            gmail_service.users().messages().modify(
                userId="me",
                id=msgID,
                body={
                    "removeLabelIds": ["UNREAD"],
                    "addLabelIds": add_labels,
                },
            ).execute()
        else:
            emailsSemXML += 1

    if xmlsProcessadosTOTAL > 0:
        print(f"({origemNome}) {xmlsProcessadosTOTAL} XML(s) processado(s).")
    elif emailsSemXML < len(messages):
        print(f"({origemNome}) Nenhum XML valido processado.")

    print(f"({origemNome}) Verificacao finalizada.\n")
    limparRelatoriosAntigos()
