import os
import base64
import time
from datetime import datetime, timedelta

from config import DOWNLOAD_DIR
from processor import processarXML, extrairFornecedor
from reporter import limparRelatoriosAntigos
from settings_manager import load_settings
from history_store import log_email_processado

def _query_periodo(filtro_periodo_emails):
    base = (
        'has:attachment filename:xml in:inbox -in:sent -in:drafts '
        '-label:"XML Processado" -label:"XML Analisado"'
    )
    hoje = datetime.now().date()

    if filtro_periodo_emails == "current_and_previous_month":
        primeiro_dia_mes_atual = hoje.replace(day=1)
        ultimo_dia_mes_anterior = primeiro_dia_mes_atual - timedelta(days=1)
        primeiro_dia_mes_anterior = ultimo_dia_mes_anterior.replace(day=1)
        after = primeiro_dia_mes_anterior.strftime("%Y/%m/%d")
        before = (hoje + timedelta(days=1)).strftime("%Y/%m/%d")
    elif filtro_periodo_emails == "previous_month":
        primeiro_dia_mes_atual = hoje.replace(day=1)
        ultimo_dia_mes_anterior = primeiro_dia_mes_atual - timedelta(days=1)
        primeiro_dia_mes_anterior = ultimo_dia_mes_anterior.replace(day=1)
        after = primeiro_dia_mes_anterior.strftime("%Y/%m/%d")
        before = primeiro_dia_mes_atual.strftime("%Y/%m/%d")
    elif filtro_periodo_emails == "current_week":
        inicio_semana = hoje - timedelta(days=hoje.weekday())  # segunda-feira
        after = inicio_semana.strftime("%Y/%m/%d")
        before = (hoje + timedelta(days=1)).strftime("%Y/%m/%d")
    elif filtro_periodo_emails == "last_15_days":
        after = (hoje - timedelta(days=15)).strftime("%Y/%m/%d")
        before = (hoje + timedelta(days=1)).strftime("%Y/%m/%d")
    elif filtro_periodo_emails == "last_45_days":
        after = (hoje - timedelta(days=45)).strftime("%Y/%m/%d")
        before = (hoje + timedelta(days=1)).strftime("%Y/%m/%d")
    elif filtro_periodo_emails == "last_60_days":
        after = (hoje - timedelta(days=60)).strftime("%Y/%m/%d")
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


def processarEmails(gmail_service, origemNome, stop_event=None):
    """Busca e baixa XMLs de uma conta Gmail e os processa."""
    label_processado = getLabelID(gmail_service, "XML Processado")
    label_analisado = getLabelID(gmail_service, "XML Analisado")

    cfg = load_settings()
    query = _query_periodo(cfg.get("gmail_filter_mode", "last_30_days"))
    max_paginas = int(cfg.get("gmail_max_pages", 3))
    page_size = int(cfg.get("gmail_page_size", 50))

    mensagens_brutas = []
    next_page_token = None
    for _ in range(max_paginas):
        if stop_event and stop_event.is_set():
            print(f"({origemNome}) Leitura manual interrompida antes de concluir as paginas.")
            break
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

    interrompido = False
    for msg in messages:
        if stop_event and stop_event.is_set():
            interrompido = True
            break
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

        payload = message.get("payload", {})
        headers = payload.get("headers", []) if isinstance(payload, dict) else []
        subject = ""
        for h in headers:
            if str(h.get("name", "")).lower() == "subject":
                subject = str(h.get("value", ""))
                break
        data_email = ""
        try:
            internal_ms = int(message.get("internalDate", "0"))
            if internal_ms > 0:
                data_email = datetime.fromtimestamp(internal_ms / 1000).isoformat()
        except Exception:
            data_email = ""

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
        xml_names = [p.get("filename", "") for p in anexosXML if p.get("filename")]

        if not anexosXML:
            emailsSemXML += 1
            continue

        xmlsInseridos = 0
        tentou_analisar = False

        for part in anexosXML:
            if stop_event and stop_event.is_set():
                interrompido = True
                break
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
            try:
                log_email_processado(
                    conta=origemNome,
                    msg_id=msgID,
                    subject=subject,
                    data_email=data_email,
                    xml_total=len(anexosXML),
                    xml_lancados=xmlsInseridos,
                    xml_arquivos=xml_names,
                )
            except Exception:
                pass
        else:
            emailsSemXML += 1

        if interrompido:
            break

    if xmlsProcessadosTOTAL > 0:
        print(f"({origemNome}) {xmlsProcessadosTOTAL} XML(s) processado(s).")
    elif emailsSemXML < len(messages):
        print(f"({origemNome}) Nenhum XML valido processado.")

    if interrompido:
        print(f"({origemNome}) Verificacao interrompida manualmente.\n")
    else:
        print(f"({origemNome}) Verificacao finalizada.\n")
    limparRelatoriosAntigos()
