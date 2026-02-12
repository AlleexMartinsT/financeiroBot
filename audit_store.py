import json
import threading
from datetime import datetime
from pathlib import Path

from config import RELATORIO_DIR


_LOCK = threading.Lock()
_AUDIT_FILE = Path(RELATORIO_DIR) / "registro_auditoria.jsonl"
_SENSITIVE_KEYS = {"password", "senha", "token", "secret", "hash", "salt"}


def _ensure_parent():
    _AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now().isoformat()


def _has_sensitive_key(name: str) -> bool:
    n = str(name or "").strip().lower()
    return any(k in n for k in _SENSITIVE_KEYS)


def _sanitize_value(value):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _has_sensitive_key(k):
                out[str(k)] = "[protegido]"
            else:
                out[str(k)] = _sanitize_value(v)
        return out
    if isinstance(value, list):
        return [_sanitize_value(x) for x in value]
    if isinstance(value, tuple):
        return [_sanitize_value(x) for x in value]
    if isinstance(value, str):
        txt = value.strip()
        return txt if len(txt) <= 1200 else (txt[:1200] + "...")
    return value


def append_audit_event(
    actor: str,
    action: str,
    target: str = "",
    before=None,
    after=None,
    status: str = "ok",
    details: str = "",
):
    _ensure_parent()
    data = {
        "type": "auditoria",
        "at": _now_iso(),
        "actor": str(actor or "").strip().lower(),
        "action": str(action or "").strip(),
        "target": str(target or "").strip(),
        "status": str(status or "ok").strip().lower(),
        "details": str(details or "").strip(),
        "before": _sanitize_value(before if before is not None else {}),
        "after": _sanitize_value(after if after is not None else {}),
    }
    line = json.dumps(data, ensure_ascii=False)
    with _LOCK:
        with _AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


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


def _match_filter(value: str, filter_value: str) -> bool:
    if not filter_value:
        return True
    return filter_value.lower() in (value or "").lower()


def _match_search(item: dict, query: str) -> bool:
    if not query:
        return True
    q = query.lower()
    fields = [
        item.get("actor", ""),
        item.get("action", ""),
        item.get("target", ""),
        item.get("details", ""),
        json.dumps(item.get("before", {}), ensure_ascii=False),
        json.dumps(item.get("after", {}), ensure_ascii=False),
    ]
    hay = " ".join(str(f) for f in fields).lower()
    return q in hay


def query_audit_events(
    dt_from: str = "",
    dt_to: str = "",
    actor: str = "",
    action: str = "",
    query: str = "",
    limit: int = 500,
) -> list[dict]:
    _ensure_parent()
    if not _AUDIT_FILE.exists():
        return []

    rows: list[dict] = []
    with _LOCK:
        lines = _AUDIT_FILE.read_text(encoding="utf-8", errors="replace").splitlines()

    max_rows = max(1, int(limit or 500))
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue

        if not _in_range(item.get("at", ""), dt_from, dt_to):
            continue
        if not _match_filter(str(item.get("actor", "")), actor):
            continue
        if action and str(item.get("action", "")).strip().lower() != action.strip().lower():
            continue
        if not _match_search(item, query):
            continue

        rows.append(item)
        if len(rows) >= max_rows:
            break

    return rows
