import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import gspread

from auth import apiCooldown
from sheet_registry import SHEET_KEYS
from sheets_utils import getPlanilha


IGNORED_STATUS = {"BAIXADO", "BAIXADA", "ESTORNADO", "ESTORNADA", "PAGO", "PAGA", "QUITADO", "QUITADA"}


def _safe_values(worksheet):
    for _ in range(3):
        try:
            return worksheet.get_all_values()
        except gspread.exceptions.APIError as e:
            if "429" in str(e):
                apiCooldown()
                continue
            raise
    return []


def _parse_sheet_key(key: str) -> tuple[str, int]:
    empresa, ano = str(key).split("_", 1)
    return empresa, int(ano)


def _parse_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            pass
    return None


def _parse_number(value):
    text = str(value or "").strip()
    if not text:
        return 0.0
    text = text.replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except Exception:
        return 0.0


def _parse_int(value, default=0):
    text = str(value or "").strip()
    m = re.search(r"\d+", text)
    if not m:
        return default
    try:
        return int(m.group(0))
    except Exception:
        return default


def _doc_tipo(header: str, row: list[str]) -> str:
    h = str(header or "").upper()
    if "CT" in h:
        return "CT-e"
    return "NF"


def _is_open_status(status: str) -> bool:
    text = str(status or "").strip().upper()
    if not text:
        return True
    return text not in IGNORED_STATUS


def _matches_query(row: dict, query: str) -> bool:
    q = str(query or "").strip().lower()
    if not q:
        return True
    fields = [
        row.get("empresa", ""),
        row.get("ano", ""),
        row.get("aba", ""),
        row.get("doc_tipo", ""),
        row.get("documento", ""),
        row.get("fornecedor", ""),
        row.get("parcela", ""),
        row.get("status", ""),
    ]
    return q in " ".join(str(x) for x in fields).lower()


def iter_finance_rows(empresa_filter="", ano_filter=""):
    empresa_filter = str(empresa_filter or "").strip().upper()
    ano_filter = str(ano_filter or "").strip()
    for key in SHEET_KEYS:
        empresa, ano = _parse_sheet_key(key)
        if empresa_filter and empresa != empresa_filter:
            continue
        if ano_filter and str(ano) != ano_filter:
            continue

        planilha = getPlanilha(key)
        if not planilha:
            continue

        for worksheet in planilha.worksheets():
            values = _safe_values(worksheet)
            if len(values) < 2:
                continue
            headers = values[0]
            doc_header = headers[2] if len(headers) > 2 else ""
            for idx, raw in enumerate(values[1:], start=2):
                row = list(raw) + [""] * max(0, 9 - len(raw))
                vencimento = str(row[0] or "").strip()
                fornecedor = str(row[1] or "").strip()
                documento = str(row[2] or "").strip()
                if not vencimento and not fornecedor and not documento:
                    continue
                if not documento or documento.upper() in {"NF", "CT-E", "CTE"}:
                    continue
                due = _parse_date(vencimento)
                qtd = _parse_int(row[4], default=1)
                parcela_num = _parse_int(row[5], default=0)
                yield {
                    "empresa": empresa,
                    "ano": ano,
                    "sheet_key": key,
                    "aba": worksheet.title,
                    "linha": idx,
                    "vencimento": vencimento,
                    "vencimento_iso": due.isoformat() if due else "",
                    "fornecedor": fornecedor,
                    "doc_tipo": _doc_tipo(doc_header, row),
                    "documento": documento,
                    "valor_total": _parse_number(row[3]),
                    "qtd_parcelas": max(1, qtd),
                    "parcela": str(row[5] or "").strip(),
                    "parcela_num": parcela_num,
                    "valor_parcela": _parse_number(row[6]),
                    "valor_pago": _parse_number(row[7]),
                    "status": str(row[8] or "").strip(),
                    "status_aberto": _is_open_status(row[8]),
                }


def query_prazos(empresa="", query="", status="", dt_from="", dt_to="", days="", limit=500):
    today = date.today()
    from_date = _parse_date(dt_from) if dt_from else None
    to_date = _parse_date(dt_to) if dt_to else None
    if days not in ("", None):
        try:
            to_date = today + timedelta(days=max(0, int(days)))
        except Exception:
            pass

    status_filter = str(status or "").strip().lower()
    items = []
    for row in iter_finance_rows(empresa_filter=empresa):
        if not row.get("status_aberto"):
            continue
        if not _matches_query(row, query):
            continue
        due = _parse_date(row.get("vencimento_iso") or row.get("vencimento"))
        if from_date and (not due or due < from_date):
            continue
        if to_date and (not due or due > to_date):
            continue
        if due:
            delta = (due - today).days
            situacao = "hoje" if delta == 0 else ("vencido" if delta < 0 else "a_vencer")
        else:
            delta = None
            situacao = "sem_data"
        if status_filter and situacao != status_filter:
            continue
        item = dict(row)
        item["dias"] = delta
        item["situacao"] = situacao
        items.append(item)

    items.sort(key=lambda x: (x.get("vencimento_iso") or "9999-99-99", x.get("empresa", ""), x.get("fornecedor", "")))
    return items[: max(1, int(limit or 500))]


def gerar_conferencia(empresa="", ano="", query="", include_ok=True, limit=1000):
    groups = defaultdict(list)
    for row in iter_finance_rows(empresa_filter=empresa, ano_filter=ano):
        if not _matches_query(row, query):
            continue
        key = (row.get("empresa"), row.get("ano"), row.get("doc_tipo"), row.get("documento"))
        groups[key].append(row)

    out = []
    for (empresa_key, ano_key, doc_tipo, documento), rows in groups.items():
        expected = max([int(r.get("qtd_parcelas") or 1) for r in rows] or [1])
        nums = [int(r.get("parcela_num") or 0) for r in rows if int(r.get("parcela_num") or 0) > 0]
        counts = Counter(nums)
        missing = [n for n in range(1, expected + 1) if counts.get(n, 0) == 0]
        duplicated = [n for n, count in sorted(counts.items()) if count > 1]
        extra = [n for n in nums if n > expected]
        actual = len(rows)

        if missing and duplicated:
            status_key = "faltando_duplicada"
            status_label = "Faltando + duplicada"
        elif missing:
            status_key = "faltando"
            status_label = "Faltando"
        elif duplicated:
            status_key = "duplicada"
            status_label = "Duplicada"
        elif extra or actual > expected:
            status_key = "a_mais"
            status_label = "A mais"
        else:
            status_key = "ok"
            status_label = "OK"

        if status_key == "ok" and not include_ok:
            continue

        rows_sorted = sorted(rows, key=lambda r: (r.get("vencimento_iso") or "", r.get("linha") or 0))
        out.append(
            {
                "empresa": empresa_key,
                "ano": ano_key,
                "doc_tipo": doc_tipo,
                "documento": documento,
                "fornecedor": rows_sorted[0].get("fornecedor", "") if rows_sorted else "",
                "status": status_key,
                "status_label": status_label,
                "esperado": expected,
                "lancado": actual,
                "faltando": missing,
                "duplicadas": duplicated,
                "extras": sorted(set(extra)),
                "abas": sorted(set(r.get("aba", "") for r in rows_sorted if r.get("aba"))),
                "linhas": [
                    {
                        "aba": r.get("aba", ""),
                        "linha": r.get("linha", ""),
                        "vencimento": r.get("vencimento", ""),
                        "parcela": r.get("parcela", ""),
                        "valor_parcela": r.get("valor_parcela", 0.0),
                        "status": r.get("status", ""),
                    }
                    for r in rows_sorted
                ],
            }
        )

    order = {"faltando": 0, "faltando_duplicada": 1, "duplicada": 2, "a_mais": 3, "ok": 9}
    out.sort(key=lambda x: (order.get(x.get("status"), 8), x.get("empresa", ""), str(x.get("documento", ""))))
    return out[: max(1, int(limit or 1000))]
