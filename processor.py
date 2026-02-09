import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal, InvalidOperation

import gspread

from config import CNPJ_EH, CNPJ_MVA
from sheets_utils import escolherPlanilha
from reporter import registrarEvento, registrarAviso, escreverRelatorio
from auth import apiCooldown


MES_ABREV_PT = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


def nome_aba_pt(dt):
    return f"{MES_ABREV_PT[dt.month - 1]}/{dt.year}"


def _doc_ref(prefixo, numero, file_path):
    arq = os.path.basename(file_path)
    if numero and numero != "-":
        return f"{prefixo} {numero} ({arq})"
    return f"{prefixo} ({arq})"


def _texto_parcela(indice):
    return f"{indice}\u00aa Parcela"


# === Extrair fornecedor do XML ===
def extrairFornecedor(file_path):
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        ns = {
            "nfe": "http://www.portalfiscal.inf.br/nfe",
            "cte": "http://www.portalfiscal.inf.br/cte",
        }

        emit = root.find(".//nfe:emit", ns)
        if emit is None:
            emit = root.find(".//cte:emit", ns)

        if emit is not None:
            nome = emit.find("nfe:xNome", ns)
            if nome is None:
                nome = emit.find("cte:xNome", ns)

            if nome is not None and nome.text:
                return nome.text.strip().upper()

        return "DESCONHECIDO"

    except Exception:
        return "DESCONHECIDO"


# === Processar NF-e ===
def processarNFE(root, filePath):
    inseriu_alguma = False
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
    emit = root.find(".//nfe:emit", ns)
    dest = root.find(".//nfe:dest", ns)
    ide = root.find(".//nfe:ide", ns)
    total = root.find(".//nfe:ICMSTot", ns)
    duplicatas = root.findall(".//nfe:cobr/nfe:dup", ns)

    fornecedor = emit.find("nfe:xNome", ns).text if emit is not None and emit.find("nfe:xNome", ns) is not None else "-"
    fornecedor = f"{fornecedor} (Bot)"
    nfNum = ide.find("nfe:nNF", ns).text if ide is not None and ide.find("nfe:nNF", ns) is not None else "-"
    valorTotal = float(total.find("nfe:vNF", ns).text) if total is not None and total.find("nfe:vNF", ns) is not None else 0.0
    cnpjDest = dest.find("nfe:CNPJ", ns).text if dest is not None and dest.find("nfe:CNPJ", ns) is not None else ""
    cnpjEmit = emit.find("nfe:CNPJ", ns).text if emit is not None and emit.find("nfe:CNPJ", ns) is not None else ""

    if cnpjEmit in [CNPJ_EH, CNPJ_MVA]:
        print(f"NF {nfNum} ignorada: emitente e a propria empresa ({cnpjEmit})")
        registrarEvento("ignorado", fornecedor, "Conta Principal")
        try:
            os.remove(filePath)
        except Exception:
            pass
        return False

    parcelas = []
    for dup in duplicatas:
        vencimento = dup.find("nfe:dVenc", ns).text if dup.find("nfe:dVenc", ns) is not None else "-"
        valor = float(dup.find("nfe:vDup", ns).text) if dup.find("nfe:vDup", ns) is not None else 0.0
        parcelas.append((nfNum, vencimento, valor))

    if not parcelas:
        aviso = f"{_doc_ref('NF', nfNum, filePath)} sem duplicatas/vencimento no XML; nota nao lancada"
        print(aviso)
        registrarAviso(aviso, "Conta Principal")
        registrarEvento("ignorado", fornecedor, "Conta Principal")
        return False

    qtdParcelas = len(parcelas)

    for i, (num, vencimento, valor) in enumerate(parcelas, start=1):
        try:
            dataVencimento = datetime.strptime(vencimento, "%Y-%m-%d")
        except Exception:
            aviso = f"{_doc_ref('NF', num, filePath)} com vencimento invalido '{vencimento}'; parcela ignorada"
            print(aviso)
            registrarAviso(aviso, "Conta Principal")
            continue

        ano = dataVencimento.year
        planilha, empresa = escolherPlanilha(cnpjDest, ano)
        if not planilha:
            aviso = f"{_doc_ref('NF', num, filePath)} sem planilha para CNPJ destino {cnpjDest} ({ano})"
            print(aviso)
            registrarAviso(aviso, "Conta Principal")
            continue

        nomeAba = nome_aba_pt(dataVencimento)

        try:
            for _ in range(3):
                try:
                    aba = planilha.worksheet(nomeAba)
                    break
                except gspread.exceptions.APIError as e:
                    if "429" in str(e):
                        apiCooldown()
                        continue
                    raise
            else:
                aviso = f"{_doc_ref('NF', num, filePath)}: falha ao acessar aba {nomeAba}"
                print(aviso)
                registrarAviso(aviso, "Conta Principal")
                return False
        except gspread.exceptions.WorksheetNotFound:
            try:
                aba = planilha.add_worksheet(title=nomeAba, rows="100", cols="9")
                aba.append_row(["Vencimento", "Descricao", "NF", "Valor Total", "Qtd Parcelas", "Parcela", "Valor Parcela", "Valor Pago", "Status"])
            except gspread.exceptions.APIError as e:
                if "already exists" in str(e).lower():
                    aba = planilha.worksheet(nomeAba)
                else:
                    raise

        for _ in range(3):
            try:
                dados = aba.get_all_values()
                break
            except gspread.exceptions.APIError as e:
                if "429" in str(e):
                    apiCooldown()
                    continue
                raise
        else:
            aviso = f"{_doc_ref('NF', num, filePath)}: falha ao ler dados da aba {nomeAba}"
            print(aviso)
            registrarAviso(aviso, "Conta Principal")
            return False

        dadosValidos = [
            linha
            for linha in dados
            if len(linha) >= 3 and linha[0] and linha[2] and "Vencimento" not in linha[0]
        ]
        duplicado = any(
            num == linha[2].strip() and dataVencimento.strftime("%d/%m/%Y") == linha[0].strip()
            for linha in dadosValidos
        )
        if duplicado:
            aviso = f"{_doc_ref('NF', num, filePath)} ja lancada em {empresa} {ano}/{nomeAba} ({dataVencimento.strftime('%d/%m/%Y')})"
            print(aviso)
            registrarAviso(aviso, "Conta Principal")
            continue

        novaLinha = [
            dataVencimento.strftime("%d/%m/%Y"),
            fornecedor,
            num,
            f"{valorTotal:.2f}".replace(".", ","),
            qtdParcelas,
            _texto_parcela(i),
            f"{valor:.2f}".replace(".", ","),
            "",
            "",
        ]

        for _ in range(3):
            try:
                linha_vazia = len(dados) + 1
                cell_range = f"A{linha_vazia}:I{linha_vazia}"
                aba.update(cell_range, [novaLinha], value_input_option="USER_ENTERED")
                dados.append(novaLinha)
                break
            except gspread.exceptions.APIError as e:
                if "429" in str(e):
                    apiCooldown()
                    continue
                raise

        print(f"Inserido: {empresa} {ano} | {nomeAba} | Parcela {i}/{qtdParcelas} - {fornecedor} - {num}")
        registrarEvento("processado", fornecedor, "Conta Principal")
        inseriu_alguma = True

    return inseriu_alguma


# === Processar CT-e ===
def processarCTE(root, filePath):
    from braspress_utils import buscarBraspressFaturas, inserir_fatura_braspress

    inseriu_alguma = False
    ns = {"cte": "http://www.portalfiscal.inf.br/cte", "nfe": "http://www.portalfiscal.inf.br/nfe"}
    emit = root.find(".//cte:emit", ns)
    dest = root.find(".//cte:dest", ns)
    ide = root.find(".//cte:ide", ns)
    total = root.find(".//cte:vPrest/cte:vTPrest", ns)
    entrega = root.find(".//cte:compl/cte:Entrega/cte:comData/cte:dProg", ns)

    fornecedor = emit.find("cte:xNome", ns).text if emit is not None and emit.find("cte:xNome", ns) is not None else "-"
    fornecedorUpper = fornecedor.upper() if fornecedor else "-"
    nfNum = ide.find("cte:nCT", ns).text if ide is not None and ide.find("cte:nCT", ns) is not None else "-"
    valorTotal = float(total.text) if total is not None and total.text else 0.0
    cnpjDest = dest.find("cte:CNPJ", ns).text if dest is not None and dest.find("cte:CNPJ", ns) is not None else ""
    cnpjEmit = emit.find("cte:CNPJ", ns).text if emit is not None and emit.find("cte:CNPJ", ns) is not None else ""

    if cnpjEmit in [CNPJ_EH, CNPJ_MVA]:
        print(f"CT-e {nfNum} ignorado: emitente e a propria empresa ({cnpjEmit})")
        registrarEvento("ignorado", fornecedor, "Conta Principal")
        try:
            os.remove(filePath)
        except Exception:
            pass
        return inseriu_alguma

    if "BRASPRESS" in fornecedorUpper:
        print(f"[Braspress] Detectado CT-e {nfNum} - buscando vencimento automatico...")
        faturas = buscarBraspressFaturas(cnpjDest)

        for item in faturas:
            if inserir_fatura_braspress(cnpjDest, item["fatura"], item["vencimento"], item["valor"]):
                inseriu_alguma = True

        if not faturas:
            aviso = f"{_doc_ref('CT-e', nfNum, filePath)} Braspress sem faturas para CNPJ {cnpjDest}; nota nao lancada"
            print(aviso)
            registrarAviso(aviso, "Conta NFe")
            registrarEvento("ignorado", fornecedor, "Conta NFe")
            try:
                os.remove(filePath)
            except Exception:
                pass
            return inseriu_alguma

        try:
            if isinstance(valorTotal, Decimal):
                valDec = valorTotal.quantize(Decimal("0.01"))
            else:
                valDec = Decimal(str(round(float(valorTotal), 2))).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            valDec = Decimal("0.00")

        correspondentes = []
        for f in faturas:
            try:
                f_val = f["valor"]
                f_val_dec = f_val if isinstance(f_val, Decimal) else Decimal(str(f_val))
                if abs(f_val_dec - valDec) < Decimal("0.05"):
                    correspondentes.append(f)
            except Exception:
                continue

        if not correspondentes:
            aviso = f"{_doc_ref('CT-e', nfNum, filePath)} Braspress sem fatura com valor correspondente ({valorTotal})"
            print(aviso)
            registrarAviso(aviso, "Conta NFe")
            registrarEvento("ignorado", fornecedor, "Conta NFe")
            try:
                os.remove(filePath)
            except Exception:
                pass
            return inseriu_alguma

        if len(correspondentes) > 1:
            msg = f"{_doc_ref('CT-e', nfNum, filePath)} Braspress com multiplas faturas no mesmo valor ({valorTotal})"
            print(msg)
            escreverRelatorio(msg)
            registrarAviso(msg, "Conta NFe")

        vencimento = correspondentes[0]["vencimento"]
        nfNum = correspondentes[0]["fatura"]
        print(f"[Braspress] Valor {valorTotal} -> vencimento {vencimento}")
    else:
        if any(x in fornecedorUpper for x in ["DOMINIO"]):
            print(f"Ignorado CT-e de transportadora ({filePath})")
            try:
                os.remove(filePath)
            except Exception:
                pass
            return inseriu_alguma

        vencimento = entrega.text if entrega is not None else None

    fornecedor = f"{fornecedor} (Bot)"
    if not vencimento:
        aviso = f"{_doc_ref('CT-e', nfNum, filePath)} sem data de vencimento; nota nao lancada"
        print(aviso)
        registrarAviso(aviso, "Conta NFe")
        try:
            os.remove(filePath)
        except Exception:
            pass
        return inseriu_alguma

    try:
        dataVencimento = datetime.strptime(vencimento, "%Y-%m-%d")
    except ValueError:
        try:
            dataVencimento = datetime.strptime(vencimento, "%d/%m/%Y")
        except Exception:
            aviso = f"{_doc_ref('CT-e', nfNum, filePath)} com data invalida '{vencimento}'; nota nao lancada"
            print(aviso)
            registrarAviso(aviso, "Conta NFe")
            try:
                os.remove(filePath)
            except Exception:
                pass
            return inseriu_alguma

    ano = dataVencimento.year
    planilha, empresa = escolherPlanilha(cnpjDest, ano)
    if not planilha:
        aviso = f"{_doc_ref('CT-e', nfNum, filePath)} sem planilha para CNPJ destino {cnpjDest} ({ano})"
        print(aviso)
        registrarAviso(aviso, "Conta NFe")
        try:
            os.remove(filePath)
        except Exception:
            pass
        return inseriu_alguma

    nomeAba = nome_aba_pt(dataVencimento)
    try:
        aba = planilha.worksheet(nomeAba)
    except gspread.exceptions.WorksheetNotFound:
        try:
            aba = planilha.add_worksheet(title=nomeAba, rows="100", cols="9")
            aba.append_row(["Vencimento", "Descricao", "CT-e", "Valor Total", "Qtd Parcelas", "Parcela", "Valor Parcela", "Valor Pago", "Status"])
        except gspread.exceptions.APIError as e:
            if "already exists" in str(e).lower():
                aba = planilha.worksheet(nomeAba)
            else:
                raise

    dados = aba.get_all_values()
    duplicado = any(
        nfNum == linha[2].strip() and dataVencimento.strftime("%d/%m/%Y") == linha[0].strip()
        for linha in dados
        if len(linha) >= 3
    )

    if duplicado:
        aviso = f"{_doc_ref('CT-e', nfNum, filePath)} ja lancado em {empresa} {ano}/{nomeAba} ({dataVencimento.strftime('%d/%m/%Y')})"
        print(aviso)
        registrarAviso(aviso, "Conta NFe")
        try:
            os.remove(filePath)
        except Exception:
            pass
        return inseriu_alguma

    novaLinha = [
        dataVencimento.strftime("%d/%m/%Y"),
        fornecedor,
        nfNum,
        f"{valorTotal:.2f}".replace(".", ","),
        1,
        _texto_parcela(1),
        f"{valorTotal:.2f}".replace(".", ","),
        "",
        "",
    ]

    aba.append_row(novaLinha, value_input_option="USER_ENTERED")
    print(f"Inserido: {empresa} {ano} | {nomeAba} | Parcela 1/1 - {fornecedor} - {nfNum}")
    registrarEvento("processado", fornecedor, "Conta NFe")
    inseriu_alguma = True

    try:
        os.remove(filePath)
        print(f"XML removido: {filePath}")
    except Exception:
        pass
    time.sleep(1.0)
    return inseriu_alguma


# === Decide tipo do XML ===
def processarXML(filePath):
    try:
        tree = ET.parse(filePath)
        root = tree.getroot()
    except Exception as e:
        aviso = f"XML invalido ({os.path.basename(filePath)}): {e}"
        print(aviso)
        registrarAviso(aviso, "Conta Principal")
        return False

    tag = root.tag.lower()
    if tag.endswith("nfeproc"):
        return processarNFE(root, filePath)
    elif tag.endswith("cteproc"):
        return processarCTE(root, filePath)
    else:
        aviso = f"Tipo de XML desconhecido ({os.path.basename(filePath)}); nao processado"
        print(aviso)
        registrarAviso(aviso, "Conta Principal")
        return False
