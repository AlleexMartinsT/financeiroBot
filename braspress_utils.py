import re
from decimal import Decimal
from login_braspress_frame import obter_faturas
from datetime import datetime
from sheets_utils import escolherPlanilha
import gspread
from history_store import log_boleto_lancado

# UtilitÃ¡rio para normalizar valor (ex: "R$ 1.234,56" -> Decimal("1234.56"))
def normalizarValor(valor_str):
    if not valor_str:
        return Decimal("0")
    limpo = valor_str.replace("R$", "").replace(".", "").replace(",", ".").strip()
    try:
        return Decimal(limpo)
    except Exception:
        return Decimal("0")

def buscarBraspressFaturas(cnpj):
    """
    Faz login (ou usa cookies salvos) via login_braspress_frame.py
    e retorna lista de faturas com vencimento e valor.
    """
    print(f"[Braspress] Efetuando busca de faturas para CNPJ {cnpj} ...")

    # Usa a funÃ§Ã£o obter_faturas do login_braspress_frame.py
    try:
        listaBruta = obter_faturas(cnpj)
    except Exception as e:
        print(f"[Braspress] Erro ao obter faturas: {e}")
        return []

    faturas = []
    for fat, venc, val in listaBruta:
        faturas.append({
            "fatura": str(fat).strip(),
            "vencimento": str(venc).strip(),
            "valor": normalizarValor(val)
        })

    if not faturas:
        print(f"[Braspress] Nenhuma fatura encontrada para {cnpj}.")
    else:
        print(f"[Braspress] {len(faturas)} fatura(s) obtida(s) para {cnpj}.")

    return faturas

# mapeamento de meses em PT (abreviaÃ§Ã£o usada no seu histÃ³rico: "Nov/2025")
MES_ABREV_PT = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]

def _mes_aba_pt(dt: datetime) -> str:
    return f"{MES_ABREV_PT[dt.month-1]}/{dt.year}"

def _texto_parcela(indice: int) -> str:
    return f"{indice}\u00aa Parcela"

def _parse_vencimento(venc_str: str) -> datetime:
    """
    Aceita 'dd/mm/YYYY' ou 'dd/mm/YY' ou outras variaÃ§Ãµes simples.
    Se falhar, usa hoje.
    """
    if not venc_str:
        return datetime.today()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(venc_str.strip(), fmt)
        except Exception:
            pass
    # fallback â€” tenta extrair nÃºmeros
    partes = [p for p in re.split(r"[^\d]", venc_str) if p]
    if len(partes) >= 3:
        try:
            d = int(partes[0]); m = int(partes[1]); y = int(partes[2])
            if y < 100: y += 2000
            return datetime(y, m, d)
        except:
            pass
    return datetime.today()

def inserir_fatura_braspress(cnpj_dest: str, fatura: str, vencimento: str, valor: Decimal):
    """
    Insere faturas da BRASPRESS no mesmo formato das notas processadas no processor.py.
    """
    from reporter import registrarEvento
    from auth import apiCooldown

    # Determinar ano e planilha
    data_venc = _parse_vencimento(vencimento)
    ano = data_venc.year
    planilha, empresa = escolherPlanilha(cnpj_dest, ano)
    if not planilha:
        print(f"[Braspress] NÃ£o foi possÃ­vel escolher planilha para {cnpj_dest} ({ano}).")
        return False

    nome_aba = _mes_aba_pt(data_venc)

    # Tenta obter ou criar a aba
    try:
        aba = planilha.worksheet(nome_aba)
    except gspread.exceptions.WorksheetNotFound:
        try:
            aba = planilha.add_worksheet(title=nome_aba, rows="100", cols="9")
            aba.append_row(["Vencimento", "DescriÃ§Ã£o", "CT-e", "Valor Total", "Qtd Parcelas", "Parcela", "Valor Parcela", "Valor Pago", "Status"])
        except gspread.exceptions.APIError as e:
            if "already exists" in str(e).lower():
                aba = planilha.worksheet(nome_aba)
            else:
                raise e

    # Ler dados existentes
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
        print(f"[Braspress] Falha ao obter dados da aba {nome_aba}")
        return False

    # Evita duplicatas (mesma fatura + vencimento)
    duplicado = any(
        fatura.strip() == linha[2].strip() and data_venc.strftime("%d/%m/%Y") == linha[0].strip()
        for linha in dados if len(linha) >= 3
    )
    if duplicado:
        print(f"[Braspress] Fatura {fatura} ({data_venc.strftime('%d/%m/%Y')}) jÃ¡ existe em {empresa} {ano} / {nome_aba}")
        return False

    # Monta a nova linha no padrÃ£o do processor.py
    valor_fmt = f"R$ {float(valor):,.2f}"
    fornecedor = "BRASPRESS TRANSPORTES URGENTES LTDA (Bot)"
    nova_linha = [
        data_venc.strftime("%d/%m/%Y"),  # Vencimento
        fornecedor,                      # DescriÃ§Ã£o / Fornecedor
        fatura,                          # CT-e (ou nÂº fatura)
        valor_fmt,                       # Valor Total
        1,                               # Qtd Parcelas
        _texto_parcela(1),               # Parcela
        valor_fmt,                       # Valor Parcela
        "",                              # Valor Pago
        ""                               # Status
    ]

    # Inserir linha no final (respeitando USER_ENTERED)
    for _ in range(3):
        try:
            linha_vazia = len(dados) + 1
            cell_range = f"A{linha_vazia}:I{linha_vazia}"
            aba.update(cell_range, [nova_linha], value_input_option="USER_ENTERED")
            break
        except gspread.exceptions.APIError as e:
            if "429" in str(e):
                apiCooldown()
                continue
            else:
                raise e

    print(f"Inserido: {empresa} {ano} | {nome_aba} | Parcela 1/1 - {fornecedor} - {fatura}")
    registrarEvento("processado", fornecedor, "Conta NFe")
    try:
        log_boleto_lancado(
            {
                "conta": "Conta NFe",
                "doc_tipo": "CT-e Braspress",
                "numero": str(fatura),
                "fornecedor": fornecedor,
                "cnpj_emit": "",
                "cnpj_dest": cnpj_dest,
                "vencimento": data_venc.strftime("%d/%m/%Y"),
                "valor_total": f"{float(valor):.2f}",
                "valor_parcela": f"{float(valor):.2f}",
                "parcela": _texto_parcela(1),
                "qtd_parcelas": 1,
                "empresa": empresa,
                "ano": int(ano),
                "aba": nome_aba,
                "arquivo_xml": "",
                "local_lancamento": f"{empresa} {ano}/{nome_aba}",
            }
        )
    except Exception:
        pass
    return True
