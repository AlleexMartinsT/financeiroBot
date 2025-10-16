# sheets_utils.py
import gspread
from config import (
    SHEET_EH_2025, SHEET_EH_2026, SHEET_MVA_2025, SHEET_MVA_2026,
)
from auth import sheetsClient, apiCooldown

planilhasCache = {}

def getPlanilha(chave):
    """Retorna objeto da planilha (gspread) a partir da chave lógica."""
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

def escolherPlanilha(cnpjDest, ano):
    """Escolhe a planilha (gspread) e retorna também a sigla da empresa."""
    from config import CNPJ_EH, CNPJ_MVA
    if cnpjDest == CNPJ_EH:
        empresa = "EH"
    elif cnpjDest == CNPJ_MVA:
        empresa = "MVA"
    else:
        return None, None
    chave = f"{empresa}_{ano}"
    return getPlanilha(chave), empresa
