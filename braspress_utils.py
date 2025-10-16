import re
from decimal import Decimal
from login_braspress_frame import obter_faturas

# Utilitário para normalizar valor (ex: "R$ 1.234,56" -> Decimal("1234.56"))
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

    # Usa a função obter_faturas do login_braspress_frame.py
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
