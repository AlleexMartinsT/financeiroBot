import json
from pathlib import Path

from config import APPDATA_BASE


SETTINGS_PATH = Path(APPDATA_BASE) / "settings.json"

DEFAULT_SETTINGS = {
    "gmail_filter_mode": "last_30_days",  # last_30_days | current_and_previous_month
    "gmail_max_pages": 3,
    "gmail_page_size": 50,
    "loop_interval_minutes": 30,
    "panel_bind_host": "0.0.0.0",  # 0.0.0.0 (rede) | 127.0.0.1 (somente local)
    "panel_port": 8765,
    "auto_update_enabled": True,
    "auto_update_interval_minutes": 5,
    "auto_update_remote": "origin",
    "auto_update_branch": "main",
}

ALLOWED_FILTER_MODES = {
    "last_15_days",
    "last_30_days",
    "last_45_days",
    "last_60_days",
    "current_week",
    "previous_month",
    "current_and_previous_month",
}


def _sanitize(data: dict) -> dict:
    out = dict(DEFAULT_SETTINGS)
    if not isinstance(data, dict):
        return out

    mode = data.get("gmail_filter_mode")
    if mode in ALLOWED_FILTER_MODES:
        out["gmail_filter_mode"] = mode

    try:
        out["gmail_max_pages"] = max(1, min(20, int(data.get("gmail_max_pages", out["gmail_max_pages"]))))
    except Exception:
        pass

    try:
        out["gmail_page_size"] = max(1, min(500, int(data.get("gmail_page_size", out["gmail_page_size"]))))
    except Exception:
        pass

    try:
        out["loop_interval_minutes"] = max(1, min(720, int(data.get("loop_interval_minutes", out["loop_interval_minutes"]))))
    except Exception:
        pass

    host = str(data.get("panel_bind_host", out["panel_bind_host"])).strip()
    if host in {"0.0.0.0", "127.0.0.1"}:
        out["panel_bind_host"] = host

    try:
        out["panel_port"] = max(1024, min(65535, int(data.get("panel_port", out["panel_port"]))))
    except Exception:
        pass

    out["auto_update_enabled"] = bool(data.get("auto_update_enabled", out["auto_update_enabled"]))

    try:
        out["auto_update_interval_minutes"] = max(
            1,
            min(720, int(data.get("auto_update_interval_minutes", out["auto_update_interval_minutes"]))),
        )
    except Exception:
        pass

    remote = str(data.get("auto_update_remote", out["auto_update_remote"])).strip()
    if remote:
        out["auto_update_remote"] = remote

    branch = str(data.get("auto_update_branch", out["auto_update_branch"])).strip()
    if branch:
        out["auto_update_branch"] = branch

    return out


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)

    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    cfg = _sanitize(raw)
    if cfg != raw:
        save_settings(cfg)
    return cfg


def save_settings(data: dict) -> dict:
    cfg = _sanitize(data)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg
