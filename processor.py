# processor.py
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import gspread

from config import CNPJ_EH, CNPJ_MVA
from sheets_utils import escolherPlanilha
from reporter import registrarEvento
from auth import apiCooldown

# === Extrair fornecedor do XML ===
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
        
# === Processar CT-e ===
def processarCTE(root, filePath):
    from braspress_utils import buscarBraspressFaturas  # import local para evitar dependência circular
    from reporter import escreverRelatorio

    ns = {'cte': 'http://www.portalfiscal.inf.br/cte'}
    emit = root.find('.//cte:emit', ns)
    dest = root.find('.//cte:dest', ns)
    ide = root.find('.//cte:ide', ns)
    total = root.find('.//cte:vPrest/cte:vTPrest', ns)
    entrega = root.find('.//cte:compl/cte:Entrega/cte:comData/cte:dProg', ns)

    fornecedor = emit.find('cte:xNome', ns).text if emit is not None else "-"
    fornecedorUpper = fornecedor.upper() if fornecedor else "-"
    nfNum = ide.find('cte:nCT', ns).text if ide is not None else "-"
    valorTotal = float(total.text) if total is not None else 0.0
    cnpjDest = dest.find('cte:CNPJ', ns).text if dest is not None else ""

    # === BRASPRESS ===
    if "BRASPRESS" in fornecedorUpper:
        print(f"[Braspress] Detectado CT-e {nfNum} - buscando vencimento automático...")
        faturas = buscarBraspressFaturas(cnpjDest)
        if not faturas:
            print(f"[Braspress] Nenhuma fatura obtida para {cnpjDest}, pulando {filePath}")
            registrarEvento("ignorado", fornecedor, "Conta NFe")
            try:
                os.remove(filePath)
            except:
                pass
            return

        from decimal import Decimal
        valDec = Decimal(str(round(valorTotal, 2)))
        correspondentes = [f for f in faturas if abs(f["valor"] - valDec) < Decimal("0.05")]

        if not correspondentes:
            print(f"[Braspress] Nenhuma fatura com valor correspondente ({valorTotal}).")
            registrarEvento("ignorado", fornecedor, "Conta NFe")
            try:
                os.remove(filePath)
            except:
                pass
            return

        if len(correspondentes) > 1:
            msg = f"[Braspress] Aviso: múltiplas faturas com mesmo valor ({valorTotal}) para {cnpjDest}."
            print(msg)
            escreverRelatorio(msg)

        vencimento = correspondentes[0]["vencimento"]
        print(f"[Braspress] Valor {valorTotal} → vencimento {vencimento}")

    else:
        # CT-e normal
        if any(x in fornecedorUpper for x in ["DOMINIO"]):
            print(f"Ignorado CT-e de transportadora ({filePath})")
            try:
                os.remove(filePath)
            except:
                pass
            return

        vencimento = entrega.text if entrega is not None else None

    fornecedor = f"{fornecedor} (Bot)"
    if not vencimento:
        print(f"CT-e {nfNum} sem data de vencimento, pulando.")
        try:
            os.remove(filePath)
        except:
            pass
        return

    # === Continua igual ao original abaixo ===
    try:
        dataVencimento = datetime.strptime(vencimento, "%Y-%m-%d")
    except ValueError:
        try:
            dataVencimento = datetime.strptime(vencimento, "%d/%m/%Y")
        except:
            print(f"Data inválida {vencimento} em {filePath}")
            os.remove(filePath)
            return

    ano = dataVencimento.year
    planilha, empresa = escolherPlanilha(cnpjDest, ano)
    if not planilha:
        print(f"CNPJ {cnpjDest} não corresponde a nenhuma planilha ({filePath})")
        try:
            os.remove(filePath)
        except:
            pass
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
        try:
            os.remove(filePath)
        except:
            pass
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

    try:
        os.remove(filePath)
        print(f"XML removido: {filePath}")
    except:
        pass
    time.sleep(1.0)

# === Decide tipo do XML ===
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
