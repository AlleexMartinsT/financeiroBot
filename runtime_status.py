import threading
from datetime import datetime, timedelta


_lock = threading.Lock()

_state = {
    "accounts": {
        "principal": {"status": "waiting", "detail": "Aguardando.", "email": ""},
        "nfe": {"status": "waiting", "detail": "Aguardando.", "email": ""},
    },
    "scheduler": {
        "next_cycle_at": None,
        "interval_seconds": 0,
        "reset_requested_seconds": None,
    },
    "cooldown": {
        "active": False,
        "until": None,
        "seconds": 0,
    },
}
_cooldown_prev = {}


def set_account_status(account: str, status: str, detail: str = ""):
    with _lock:
        if account in _state["accounts"]:
            _state["accounts"][account]["status"] = status
            _state["accounts"][account]["detail"] = detail or ""


def set_account_email(account: str, email: str):
    with _lock:
        if account in _state["accounts"]:
            _state["accounts"][account]["email"] = email or ""


def set_next_cycle(seconds: int):
    with _lock:
        sec = max(0, int(seconds))
        _state["scheduler"]["interval_seconds"] = sec
        _state["scheduler"]["next_cycle_at"] = (datetime.now() + timedelta(seconds=sec)).isoformat()


def clear_next_cycle():
    with _lock:
        _state["scheduler"]["next_cycle_at"] = None
        _state["scheduler"]["interval_seconds"] = 0


def request_next_cycle_reset(seconds: int):
    with _lock:
        sec = max(0, int(seconds))
        _state["scheduler"]["reset_requested_seconds"] = sec
        _state["scheduler"]["interval_seconds"] = sec
        _state["scheduler"]["next_cycle_at"] = (datetime.now() + timedelta(seconds=sec)).isoformat()


def consume_next_cycle_reset() -> int | None:
    with _lock:
        sec = _state["scheduler"].get("reset_requested_seconds")
        _state["scheduler"]["reset_requested_seconds"] = None
        if sec is None:
            return None
        return max(0, int(sec))


def begin_api_cooldown(seconds: int, detail: str = "Limite da API atingido, aguardando nova tentativa."):
    with _lock:
        sec = max(1, int(seconds))
        _state["cooldown"]["active"] = True
        _state["cooldown"]["seconds"] = sec
        _state["cooldown"]["until"] = (datetime.now() + timedelta(seconds=sec)).isoformat()

        _cooldown_prev.clear()
        for acc in ("principal", "nfe"):
            current = _state["accounts"][acc]
            if current.get("status") == "running":
                _cooldown_prev[acc] = {"status": "running", "detail": current.get("detail", "")}
                current["status"] = "cooldown"
                current["detail"] = detail


def end_api_cooldown():
    with _lock:
        _state["cooldown"]["active"] = False
        _state["cooldown"]["seconds"] = 0
        _state["cooldown"]["until"] = None

        for acc, prev in _cooldown_prev.items():
            if acc in _state["accounts"]:
                if _state["accounts"][acc].get("status") == "cooldown":
                    _state["accounts"][acc]["status"] = prev.get("status", "running")
                    _state["accounts"][acc]["detail"] = prev.get("detail", "")
        _cooldown_prev.clear()


def get_state() -> dict:
    with _lock:
        snapshot = {
            "accounts": {
                "principal": dict(_state["accounts"]["principal"]),
                "nfe": dict(_state["accounts"]["nfe"]),
            },
            "scheduler": dict(_state["scheduler"]),
            "cooldown": dict(_state["cooldown"]),
        }

    next_cycle_at = snapshot["scheduler"].get("next_cycle_at")
    remaining = 0
    if next_cycle_at:
        try:
            dt = datetime.fromisoformat(next_cycle_at)
            remaining = max(0, int((dt - datetime.now()).total_seconds()))
        except Exception:
            remaining = 0
    snapshot["scheduler"]["remaining_seconds"] = remaining

    cd_until = snapshot["cooldown"].get("until")
    cd_remaining = 0
    if cd_until:
        try:
            dt = datetime.fromisoformat(cd_until)
            cd_remaining = max(0, int((dt - datetime.now()).total_seconds()))
        except Exception:
            cd_remaining = 0
    snapshot["cooldown"]["remaining_seconds"] = cd_remaining
    if cd_remaining <= 0 and snapshot["cooldown"].get("active"):
        snapshot["cooldown"]["active"] = False
    return snapshot
