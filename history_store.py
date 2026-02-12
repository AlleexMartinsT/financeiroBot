import json
import threading
from datetime import datetime
from pathlib import Path

from config import RELATORIO_DIR


_LOCK = threading.Lock()
_HISTORY_FILE = Path(RELATORIO_DIR) / "historico_eventos.jsonl"


def _ensure_parent():
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now().isoformat()


def append_event(event_type: str, payload: dict):
    _ensure_parent()
    data = {"type": event_type, "at": _now_iso()}
    data.update(payload or {})
    line = json.dumps(data, ensure_ascii=False)
    with _LOCK:
        with _HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def log_email_processado(
    conta: str,
    msg_id: str,
    subject: str,
    data_email: str,
    xml_total: int,
    xml_lancados: int,
    xml_arquivos: list[str],
):
    subj = str(subject or "")
    subj_up = subj.upper()
    if "DANFE" in subj_up or " NFE " in f" {subj_up} ":
        return

    status = "lancado" if int(xml_lancados or 0) > 0 else "analisado_sem_lancamento"
    append_event(
        "email_processado",
        {
            "conta": conta or "",
            "msg_id": msg_id or "",
            "subject": subj,
            "data_email": data_email or "",
            "xml_total": int(xml_total or 0),
            "xml_lancados": int(xml_lancados or 0),
            "xml_arquivos": list(xml_arquivos or []),
            "status": status,
        },
    )


def log_boleto_lancado(payload: dict):
    append_event("boleto_lancado", payload or {})


def _match_filter(value: str, filter_value: str) -> bool:
    if not filter_value:
        return True
    return filter_value.lower() in (value or "").lower()


def _date_part(iso_or_date: str) -> str:
    text = str(iso_or_date or "")
    if "T" in text:
        return text.split("T", 1)[0]
    return text[:10]


def _in_range(event_at: str, dt_from: str, dt_to: str) -> bool:
    if not event_at:
        return False
    day = _date_part(event_at)
    if dt_from and day < dt_from:
        return False
    if dt_to and day > dt_to:
        return False
    return True


def _match_search(item: dict, query: str) -> bool:
    if not query:
        return True
    q = query.lower()
    fields = [
        item.get("subject", ""),
        item.get("fornecedor", ""),
        item.get("numero", ""),
        item.get("doc_tipo", ""),
        item.get("conta", ""),
        item.get("status", ""),
        item.get("arquivo_xml", ""),
        item.get("local_lancamento", ""),
    ]
    hay = " ".join(str(f) for f in fields).lower()
    return q in hay


def query_events(
    dt_from: str = "",
    dt_to: str = "",
    cnpj_emit: str = "",
    cnpj_dest: str = "",
    event_type: str = "",
    query: str = "",
    limit: int = 500,
) -> list[dict]:
    _ensure_parent()
    if not _HISTORY_FILE.exists():
        return []

    rows: list[dict] = []
    with _LOCK:
        lines = _HISTORY_FILE.read_text(encoding="utf-8", errors="replace").splitlines()

    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue

        if event_type and item.get("type") != event_type:
            continue
        if not _in_range(item.get("at", ""), dt_from, dt_to):
            continue
        if not _match_filter(str(item.get("cnpj_emit", "")), cnpj_emit):
            continue
        if not _match_filter(str(item.get("cnpj_dest", "")), cnpj_dest):
            continue
        if not _match_search(item, query):
            continue

        rows.append(item)
        if len(rows) >= max(1, int(limit)):
            break

    return rows
