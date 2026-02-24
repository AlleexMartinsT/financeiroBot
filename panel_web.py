import json
import hashlib
import hmac
import re
import secrets
import socket
import threading
import time
import traceback
import webbrowser
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import auth
import runtime_status
from audit_store import append_audit_event, query_audit_events
from config import RELATORIO_DIR, CNPJ_EH, CNPJ_MVA, APPDATA_BASE
from gmail_fetcher import processarEmails
from history_store import query_events
from settings_manager import load_settings, save_settings


_server_started = False
_server_lock = threading.Lock()
_last_run = {"status": "idle", "message": "", "at": None}
_email_cache = {
    "principal": {"email": "", "error": "", "at": 0.0},
    "nfe": {"email": "", "error": "", "at": 0.0},
}
_diagnostics = []
_diag_lock = threading.Lock()
_manual_lock = threading.Lock()
_manual_stop_event = threading.Event()
_manual_state = {
    "running": False,
    "account": "",
    "started_at": None,
    "cancel_requested": False,
}
_COOKIE_SESSION = "financebot_session"
_SESSION_TTL_SECONDS = 8 * 60 * 60
_SESSIONS = {}
_SESSIONS_LOCK = threading.Lock()
_AUTH_FILE = Path(APPDATA_BASE) / "panel_auth.json"
_AUTH_LOCK = threading.Lock()
_WEAK_PASSWORDS = {
    "123456",
    "12345678",
    "123456789",
    "senha123",
    "senha1234",
    "password",
    "qwerty",
    "admin",
    "admin123",
    "financebot",
}


def _add_diagnostic(source: str, error: Exception):
    raw = str(error) if error else "Erro desconhecido"
    item = {
        "at": datetime.now().isoformat(),
        "source": source,
        "friendly": _friendly_error(raw),
        "raw": raw,
        "traceback": traceback.format_exc(),
    }
    with _diag_lock:
        _diagnostics.append(item)
        if len(_diagnostics) > 40:
            del _diagnostics[:-40]


def _friendly_error(raw: str) -> str:
    txt = (raw or "").lower()
    if "wrong_version_number" in txt or ("ssl" in txt and "version" in txt):
        return "Falha SSL na conexão com o Gmail, verifique proxy, VPN, antivírus com inspeção HTTPS ou firewall"
    if "timed out" in txt or "timeout" in txt:
        return "A conexão com o Gmail demorou para responder, o sistema vai tentar novamente no próximo ciclo"
    if "invalid_grant" in txt:
        return "A autorização do Gmail expirou ou foi revogada, refaça a autenticação desta conta"
    if "quota" in txt or "rate limit" in txt:
        return "Limite temporário da API foi atingido, aguarde alguns minutos e tente novamente"
    if "permission" in txt or "403" in txt:
        return "Permissão negada pela API, verifique credenciais e escopos configurados"
    if "401" in txt:
        return "Sessão de autenticação inválida, refaça a autenticação da conta"
    if "connection reset" in txt or "dns" in txt:
        return "Falha de rede ao falar com o Gmail, verifique internet, proxy ou firewall"
    return "Falha na comunicação com a API, veja os detalhes técnicos para identificar a causa"


def _password_hash(password: str, salt_hex: str) -> str:
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120000)
    return digest.hex()


def _normalize_username(username: str) -> str:
    return str(username or "").strip().lower()


def _username_valid(username: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9._-]{3,32}", _normalize_username(username)))


def _password_errors(password: str, username: str) -> list[str]:
    pwd = str(password or "")
    user = _normalize_username(username)
    errs = []
    if len(pwd) < 6:
        errs.append("A senha deve ter no mínimo 6 caracteres")
    if not re.search(r"[a-z]", pwd):
        errs.append("A senha deve ter ao menos uma letra minúscula")
    if not re.search(r"[A-Z]", pwd):
        errs.append("A senha deve ter ao menos uma letra maiúscula")
    if not re.search(r"\d", pwd):
        errs.append("A senha deve ter ao menos um número")
    if not re.search(r"[^A-Za-z0-9]", pwd):
        errs.append("A senha deve ter ao menos um caractere especial")
    if _normalize_username(pwd) in _WEAK_PASSWORDS:
        errs.append("Senha muito fraca, escolha outra")
    if user and user in _normalize_username(pwd):
        errs.append("A senha não pode conter o usuário")
    if re.fullmatch(r"(.)\1{5,}", pwd):
        errs.append("Senha muito repetitiva, escolha outra")
    return errs


def _verify_password(user_item: dict, password: str) -> bool:
    salt_hex = str(user_item.get("salt", "")).strip()
    saved_hash = str(user_item.get("password_hash", "")).strip()
    if not salt_hex or not saved_hash:
        return False
    calc = _password_hash(password or "", salt_hex)
    return hmac.compare_digest(calc, saved_hash)


def _ensure_auth_store() -> dict:
    if _AUTH_FILE.exists():
        try:
            data = json.loads(_AUTH_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}

    if isinstance(data, dict) and isinstance(data.get("users"), list) and data["users"]:
        users = []
        changed = False
        seen = set()
        for idx, item in enumerate(data.get("users", [])):
            if not isinstance(item, dict):
                changed = True
                continue
            uname = _normalize_username(item.get("username", ""))
            if not _username_valid(uname) or uname in seen:
                changed = True
                continue
            seen.add(uname)
            role = str(item.get("role", "user")).strip().lower()
            if role not in {"dev", "admin", "user"}:
                role = "user"
                changed = True
            if idx == 0 and "role" not in item:
                role = "dev"
                changed = True
            salt_hex = str(item.get("salt", "")).strip()
            pwh = str(item.get("password_hash", "")).strip()
            if not salt_hex or not pwh:
                changed = True
                continue
            users.append(
                {
                    "username": uname,
                    "role": role,
                    "salt": salt_hex,
                    "password_hash": pwh,
                    "created_at": str(item.get("created_at", datetime.now().isoformat())),
                    "updated_at": str(item.get("updated_at", datetime.now().isoformat())),
                }
            )
        if users and not any(u.get("role") == "dev" for u in users):
            users[0]["role"] = "dev"
            changed = True
        if users:
            out = {"users": users, "created_at": str(data.get("created_at", datetime.now().isoformat()))}
            if changed:
                _AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
                _AUTH_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            return out

    default_user = "dev"
    default_pass = "dev"
    salt_hex = secrets.token_hex(16)
    data = {
        "users": [
            {
                "username": default_user,
                "role": "dev",
                "salt": salt_hex,
                "password_hash": _password_hash(default_pass, salt_hex),
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
        ],
        "created_at": datetime.now().isoformat(),
    }
    _AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    _AUTH_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[Painel] Login inicial criado")
    print(f"[Painel] Usuario: {default_user}")
    print(f"[Painel] Senha: {default_pass}")
    print(f"[Painel] Arquivo de usuarios: {_AUTH_FILE}")
    return data


def _load_auth_store() -> dict:
    with _AUTH_LOCK:
        return _ensure_auth_store()


def _save_auth_store(data: dict):
    with _AUTH_LOCK:
        _AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        _AUTH_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _audit(
    actor: str,
    action: str,
    target: str = "",
    before=None,
    after=None,
    status: str = "ok",
    details: str = "",
):
    try:
        append_audit_event(
            actor=actor,
            action=action,
            target=target,
            before=before if before is not None else {},
            after=after if after is not None else {},
            status=status,
            details=details,
        )
    except Exception:
        pass


def _auth_user_snapshot(username: str) -> dict | None:
    user = _normalize_username(username)
    data = _load_auth_store()
    for item in data.get("users", []):
        if _normalize_username(item.get("username", "")) != user:
            continue
        return {
            "username": user,
            "role": str(item.get("role", "user")).lower(),
            "created_at": str(item.get("created_at", "")),
            "updated_at": str(item.get("updated_at", "")),
        }
    return None


def _settings_diff(before: dict, after: dict) -> dict:
    a = before if isinstance(before, dict) else {}
    b = after if isinstance(after, dict) else {}
    out = {}
    for k in sorted(set(a.keys()) | set(b.keys())):
        va = a.get(k)
        vb = b.get(k)
        if va != vb:
            out[k] = {"antes": va, "depois": vb}
    return out


def _verify_login(username: str, password: str) -> bool:
    user = _normalize_username(username)
    pwd = password or ""
    if not user or not pwd:
        return False
    data = _load_auth_store()
    for item in data.get("users", []):
        item_user = _normalize_username(item.get("username", ""))
        if item_user != user:
            continue
        return _verify_password(item, pwd)
    return False


def _role_of(username: str) -> str:
    user = _normalize_username(username)
    data = _load_auth_store()
    for item in data.get("users", []):
        if _normalize_username(item.get("username", "")) == user:
            role = str(item.get("role", "user")).lower()
            return role if role in {"dev", "admin", "user"} else "user"
    return "user"


def _is_dev(username: str) -> bool:
    return _role_of(username) == "dev"


def _can_operate(username: str) -> bool:
    return _role_of(username) in {"dev", "admin"}


def _auth_snapshot(username: str) -> dict:
    user = _normalize_username(username)
    role = _role_of(user)
    can_operate = role in {"dev", "admin"}
    can_manage_users = role == "dev"
    can_change_password = role in {"dev", "admin", "user"}
    can_view_audit = role == "dev"
    out = {
        "user": user,
        "role": role,
        "is_admin": role in {"dev", "admin"},
        "is_dev": role == "dev",
        "can_operate": can_operate,
        "can_manage_users": can_manage_users,
        "can_change_password": can_change_password,
        "can_view_audit": can_view_audit,
    }
    if can_manage_users:
        data = _load_auth_store()
        out["users"] = [
            {
                "username": _normalize_username(x.get("username", "")),
                "role": str(x.get("role", "user")).lower(),
                "created_at": str(x.get("created_at", "")),
            }
            for x in data.get("users", [])
        ]
    return out


def _drop_sessions_for_user(username: str):
    user = _normalize_username(username)
    with _SESSIONS_LOCK:
        kill = [k for k, v in _SESSIONS.items() if _normalize_username(v.get("username", "")) == user]
        for k in kill:
            _SESSIONS.pop(k, None)


def _change_own_password(username: str, current_password: str, new_password: str) -> tuple[bool, str]:
    user = _normalize_username(username)
    data = _load_auth_store()
    items = data.get("users", [])
    idx = next((i for i, x in enumerate(items) if _normalize_username(x.get("username", "")) == user), -1)
    if idx < 0:
        return False, "Usuário não encontrado"
    item = items[idx]
    if not _verify_password(item, current_password or ""):
        return False, "Senha atual inválida"
    errs = _password_errors(new_password or "", user)
    if errs:
        return False, errs[0]
    if _verify_password(item, new_password or ""):
        return False, "A nova senha deve ser diferente da senha atual"
    salt_hex = secrets.token_hex(16)
    item["salt"] = salt_hex
    item["password_hash"] = _password_hash(new_password, salt_hex)
    item["updated_at"] = datetime.now().isoformat()
    data["users"][idx] = item
    _save_auth_store(data)
    _drop_sessions_for_user(user)
    return True, "Senha atualizada com sucesso"


def _admin_create_user(admin_user: str, username: str, password: str, role: str = "user") -> tuple[bool, str]:
    if not _is_dev(admin_user):
        return False, "Apenas dev pode criar usuários"
    uname = _normalize_username(username)
    if not _username_valid(uname):
        return False, "Usuário inválido (use 3-32 chars: a-z, 0-9, ponto, underscore, hífen)"
    role_norm = str(role or "user").strip().lower()
    if role_norm not in {"dev", "admin", "user"}:
        role_norm = "user"
    if role_norm == "dev":
        return False, "Não é permitido criar outro usuário dev"
    data = _load_auth_store()
    if any(_normalize_username(x.get("username", "")) == uname for x in data.get("users", [])):
        return False, "Usuário já existe"
    errs = _password_errors(password or "", uname)
    if errs:
        return False, errs[0]
    salt_hex = secrets.token_hex(16)
    data["users"].append(
        {
            "username": uname,
            "role": role_norm,
            "salt": salt_hex,
            "password_hash": _password_hash(password, salt_hex),
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
    )
    _save_auth_store(data)
    return True, "Usuário criado com sucesso"


def _admin_delete_user(admin_user: str, username: str) -> tuple[bool, str]:
    if not _is_dev(admin_user):
        return False, "Apenas dev pode remover usuários"
    target = _normalize_username(username)
    actor = _normalize_username(admin_user)
    if target == actor:
        return False, "Não é permitido remover o próprio usuário logado"
    data = _load_auth_store()
    users = data.get("users", [])
    idx = next((i for i, x in enumerate(users) if _normalize_username(x.get("username", "")) == target), -1)
    if idx < 0:
        return False, "Usuário não encontrado"
    if str(users[idx].get("role", "user")).lower() == "dev":
        devs = [x for x in users if str(x.get("role", "user")).lower() == "dev"]
        if len(devs) <= 1:
            return False, "Não é permitido remover o último dev"
    users.pop(idx)
    data["users"] = users
    _save_auth_store(data)
    _drop_sessions_for_user(target)
    return True, "Usuário removido com sucesso"


def _admin_reset_password(admin_user: str, username: str, new_password: str) -> tuple[bool, str]:
    if not _is_dev(admin_user):
        return False, "Apenas dev pode redefinir senha de outros usuários"
    target = _normalize_username(username)
    data = _load_auth_store()
    users = data.get("users", [])
    idx = next((i for i, x in enumerate(users) if _normalize_username(x.get("username", "")) == target), -1)
    if idx < 0:
        return False, "Usuário não encontrado"
    errs = _password_errors(new_password or "", target)
    if errs:
        return False, errs[0]
    salt_hex = secrets.token_hex(16)
    users[idx]["salt"] = salt_hex
    users[idx]["password_hash"] = _password_hash(new_password, salt_hex)
    users[idx]["updated_at"] = datetime.now().isoformat()
    data["users"] = users
    _save_auth_store(data)
    _drop_sessions_for_user(target)
    return True, "Senha redefinida com sucesso"


def _expire_sessions():
    now = time.time()
    with _SESSIONS_LOCK:
        expired = [k for k, v in _SESSIONS.items() if float(v.get("expires_at", 0)) <= now]
        for k in expired:
            _SESSIONS.pop(k, None)


def _create_session(username: str) -> str:
    _expire_sessions()
    token = secrets.token_urlsafe(32)
    user = _normalize_username(username)
    with _SESSIONS_LOCK:
        _SESSIONS[token] = {
            "username": user,
            "created_at": time.time(),
            "expires_at": time.time() + _SESSION_TTL_SECONDS,
        }
    return token


def _parse_cookies(handler: BaseHTTPRequestHandler) -> dict:
    raw = handler.headers.get("Cookie", "")
    out = {}
    for part in raw.split(";"):
        chunk = part.strip()
        if not chunk or "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _current_session_user(handler: BaseHTTPRequestHandler) -> str | None:
    _expire_sessions()
    token = _parse_cookies(handler).get(_COOKIE_SESSION, "")
    if not token:
        return None
    with _SESSIONS_LOCK:
        item = _SESSIONS.get(token)
        if not item:
            return None
        item["expires_at"] = time.time() + _SESSION_TTL_SECONDS
        return str(item.get("username", "")).strip() or None


def _destroy_session(handler: BaseHTTPRequestHandler):
    token = _parse_cookies(handler).get(_COOKIE_SESSION, "")
    if not token:
        return
    with _SESSIONS_LOCK:
        _SESSIONS.pop(token, None)


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict, extra_headers: dict | None = None):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    if extra_headers:
        for k, v in extra_headers.items():
            handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(raw)


def _html_response(handler: BaseHTTPRequestHandler, status: int, html: str):
    raw = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _redirect(handler: BaseHTTPRequestHandler, location: str):
    handler.send_response(302)
    handler.send_header("Location", location)
    handler.end_headers()


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _label_map(service) -> dict:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    return {x["name"].lower(): x["id"] for x in labels}


def _reprocess_recent(service, days: int, max_messages: int, mark_unread: bool) -> dict:
    labels = _label_map(service)
    remove_ids = []
    for name in ("xml processado", "xml analisado"):
        if name in labels:
            remove_ids.append(labels[name])
    if not remove_ids:
        return {"matched": 0, "updated": 0, "warning": "Nenhuma label encontrada para remover."}

    q = f'in:inbox newer_than:{days}d {{label:"XML Processado" label:"XML Analisado"}}'
    ids = []
    token = None
    while len(ids) < max_messages:
        resp = service.users().messages().list(
            userId="me",
            q=q,
            maxResults=min(100, max_messages - len(ids)),
            pageToken=token,
        ).execute()
        batch = resp.get("messages", [])
        ids.extend([m["id"] for m in batch if "id" in m])
        token = resp.get("nextPageToken")
        if not token or not batch:
            break

    updated = 0
    add_ids = ["UNREAD"] if mark_unread else []
    for msg_id in ids:
        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"removeLabelIds": remove_ids, "addLabelIds": add_ids},
        ).execute()
        updated += 1
    return {"matched": len(ids), "updated": updated}


def _refresh_account_email(account: str, force: bool = False) -> dict:
    now = time.time()
    cache = _email_cache.get(account)
    if cache and not force and (now - cache["at"] < 60):
        return cache
    service = auth.get_gmail_service(account)
    data = {"email": "", "error": "", "friendly_error": "", "at": now}
    try:
        profile = service.users().getProfile(userId="me").execute()
        data["email"] = profile.get("emailAddress", "")
        runtime_status.set_account_email(account, data["email"])
    except Exception as e:
        raw = str(e)
        data["error"] = raw
        data["friendly_error"] = _friendly_error(raw)
        _add_diagnostic(f"email_profile_{account}", e)
    _email_cache[account] = data
    return data


def _manual_snapshot() -> dict:
    with _manual_lock:
        return dict(_manual_state)


def _run_now(account: str):
    global _last_run
    with _manual_lock:
        if _manual_state["running"]:
            _last_run = {
                "status": "running",
                "message": "Execução manual já está em andamento",
                "friendly": "Execução manual já em andamento",
                "at": datetime.now().isoformat(),
            }
            return
        _manual_state["running"] = True
        _manual_state["account"] = account
        _manual_state["started_at"] = datetime.now().isoformat()
        _manual_state["cancel_requested"] = False
        _manual_stop_event.clear()

    _last_run = {"status": "running", "message": f"Executando conta {account}", "friendly": "Execução manual iniciada", "at": datetime.now().isoformat()}
    if account == "all":
        runtime_status.set_account_status("principal", "running", "Leitura manual em andamento")
        runtime_status.set_account_status("nfe", "running", "Leitura manual em andamento")
    else:
        runtime_status.set_account_status(account, "running", "Leitura manual em andamento")
    try:
        if account == "principal":
            processarEmails(auth.get_gmail_service("principal"), "Conta Principal", stop_event=_manual_stop_event)
        elif account == "all":
            processarEmails(auth.get_gmail_service("principal"), "Conta Principal", stop_event=_manual_stop_event)
            if not _manual_stop_event.is_set():
                processarEmails(auth.get_gmail_service("nfe"), "Conta NFe", stop_event=_manual_stop_event)
        else:
            processarEmails(auth.get_gmail_service("nfe"), "Conta NFe", stop_event=_manual_stop_event)

        if _manual_stop_event.is_set():
            if account == "all":
                runtime_status.set_account_status("principal", "waiting", "Execução manual interrompida")
                runtime_status.set_account_status("nfe", "waiting", "Execução manual interrompida")
            else:
                runtime_status.set_account_status(account, "waiting", "Execução manual interrompida")
            _last_run = {
                "status": "stopped",
                "message": f"Execução da conta {account} interrompida pelo usuário",
                "friendly": "Execução manual interrompida",
                "at": datetime.now().isoformat(),
            }
        else:
            if account == "all":
                runtime_status.set_account_status("principal", "ok", "Funcionando")
                runtime_status.set_account_status("nfe", "ok", "Funcionando")
            else:
                runtime_status.set_account_status(account, "ok", "Funcionando")
            _last_run = {"status": "ok", "message": f"Execução da conta {account} concluída", "friendly": "Execução manual concluída com sucesso", "at": datetime.now().isoformat()}
    except Exception as e:
        raw = str(e)
        if account == "all":
            runtime_status.set_account_status("principal", "error", _friendly_error(raw))
            runtime_status.set_account_status("nfe", "error", _friendly_error(raw))
        else:
            runtime_status.set_account_status(account, "error", _friendly_error(raw))
        _last_run = {"status": "error", "message": raw, "friendly": _friendly_error(raw), "at": datetime.now().isoformat()}
        _add_diagnostic(f"run_now_{account}", e)
    finally:
        with _manual_lock:
            _manual_state["running"] = False
            _manual_state["account"] = ""
            _manual_state["started_at"] = None
            _manual_state["cancel_requested"] = False
        _manual_stop_event.clear()


def _find_store_image() -> Path | None:
    fname = "Arte MVA logo Metalico (1).png"
    rel = Path("assets") / "branding" / fname
    candidates = [
        Path.cwd() / rel,
        Path.cwd() / fname,
        Path(__file__).resolve().parent / rel,
        Path(__file__).resolve().parent / fname,
        Path(sys.executable).resolve().parent / rel,
        Path(sys.executable).resolve().parent / fname,
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / rel)
        candidates.append(Path(meipass) / fname)
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


def _send_store_image(handler: BaseHTTPRequestHandler) -> bool:
    p = _find_store_image()
    if not p:
        return False
    ext = p.suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "application/octet-stream")
    raw = p.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "public, max-age=3600")
    handler.end_headers()
    handler.wfile.write(raw)
    return True


def _latest_report_path() -> Path | None:
    base = Path(RELATORIO_DIR)
    if not base.exists():
        return None
    files = sorted(base.glob("relatorio_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _parse_report() -> dict:
    path = _latest_report_path()
    if not path:
        return {
            "exists": False,
            "path": "",
            "updated_at": "",
            "totals": {"processados": 0, "ignorados": 0, "avisos_ciclo": 0, "avisos_dia": 0},
            "processados": [],
            "ignorados": [],
            "avisos": [],
            "erros": [],
            "tail": [],
        }
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        lines = []

    processados, ignorados, avisos, erros = [], [], [], []
    resumo = {"processados": 0, "ignorados": 0, "avisos_ciclo": 0, "avisos_dia": 0}
    section = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Fornecedores processados"):
            section = "processados"
            continue
        if line.startswith("Fornecedores ignorados"):
            section = "ignorados"
            continue
        if line.startswith("Avisos de notas com problema"):
            section = "avisos"
            continue
        if line.startswith("Resumo:"):
            nums = [int(x) for x in re.findall(r"\d+", line)]
            if len(nums) >= 4:
                resumo = {"processados": nums[0], "ignorados": nums[1], "avisos_ciclo": nums[2], "avisos_dia": nums[3]}
            section = ""
            continue
        if line.startswith("- "):
            item = line[2:]
            if section == "processados":
                processados.append(item)
            elif section == "ignorados":
                ignorados.append(item)
            elif section == "avisos":
                avisos.append(_format_aviso_text(item))
            continue
        if "Erro (" in line or "Erro ao processar" in line:
            erros.append(line)

    updated = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    return {
        "exists": True,
        "path": str(path),
        "updated_at": updated,
        "totals": resumo,
        "processados": processados[-20:],
        "ignorados": ignorados[-20:],
        "avisos": avisos[-30:],
        "erros": erros[-15:],
        "tail": lines[-120:],
    }


def _mask_xml_in_text(text: str) -> str:
    s = str(text or "")

    def _mask_digits(digits: str) -> str:
        if len(digits) <= 8:
            return digits
        return f"***{digits[-8:]}"

    s = re.sub(
        r"(\d{8,})(-(?:nfe|cte))?\.xml",
        lambda m: f"{_mask_digits(m.group(1))}{m.group(2) or ''}.xml",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"\b(\d{44})\b", lambda m: _mask_digits(m.group(1)), s)
    return s


def _format_aviso_text(text: str) -> str:
    s = _mask_xml_in_text(text)
    s = re.sub(r"\s*\([^)]*\.xml\)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\b([A-Z]{2,})\s+\d{4}/([A-Za-z]{3}/\d{4})\b", r"\1 \2", s)
    s = re.sub(r"\bja lancada\b", "já lançada", s, flags=re.IGNORECASE)
    s = re.sub(r"\bja lancado\b", "já lançado", s, flags=re.IGNORECASE)
    s = s.replace("já", "já").replace("lançada", "lançada").replace("lançado", "lançado")
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _runtime_with_friendly() -> dict:
    state = runtime_status.get_state()
    for key in ("principal", "nfe"):
        account = state["accounts"].get(key, {})
        detail = account.get("detail", "")
        status = account.get("status", "waiting")
        if status == "error":
            friendly = _friendly_error(detail)
        elif status == "cooldown":
            friendly = "Limite da API atingido, aguardando para tentar novamente"
        elif status == "waiting":
            friendly = "Aguardando a próxima verificação automática"
        elif status == "running":
            friendly = "Lendo os e-mails agora"
        else:
            friendly = "Conta funcionando normalmente"
        account["friendly_detail"] = friendly
    return state


def _render_login_html() -> str:
    return """<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>FinanceBot - Login</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Lexend:wght@300;400;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--o:#da7a1c;--o2:#ee9b2f;--b:#4a2b18;--b2:#6b4128}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;font-family:'Lexend',Arial,sans-serif;background:linear-gradient(160deg,rgba(41,22,11,.78),rgba(95,56,28,.72)),url('/assets/store-bg') center/cover fixed;display:flex;justify-content:center;align-items:center;padding:12px;color:#2a1b12}
.card{width:min(420px,96vw);border-radius:16px;border:1px solid rgba(231,200,168,.9);background:linear-gradient(180deg,rgba(255,250,246,.96),rgba(255,245,235,.92));box-shadow:0 24px 60px rgba(21,11,6,.35);padding:16px}
h1{margin:0 0 6px;color:var(--b);font-size:1.2rem}
p{margin:0 0 12px;color:#6b4128}
label{display:block;margin-top:8px;font-weight:600;color:#5c341c}
input{width:100%;padding:10px;margin-top:4px;border:1px solid #d6b18f;border-radius:8px;background:#fffdfb;font-family:inherit}
button{margin-top:12px;width:100%;padding:10px 12px;border:0;border-radius:9px;background:linear-gradient(90deg,var(--o),var(--o2));color:#2b1408;font-weight:700;cursor:pointer}
.btn-sec{background:linear-gradient(90deg,#6b4128,#4a2b18);color:#fff9f3}
.hidden{display:none !important}
.msg{margin-top:10px;font-size:.9rem;color:#9c2c1d;min-height:20px}
</style></head><body>
<section class="card">
<h1>Acesso ao Painel</h1>
<p>Entre com usuário e senha para continuar</p>
<label>Usuário</label><input id="u" type="text" autocomplete="username"/>
<label>Senha</label><input id="p" type="password" autocomplete="current-password"/>
<button id="b" onclick="login()">Entrar</button>
<button id="hubBackLogin" class="btn-sec hidden" type="button" onclick="backToHub()">Voltar ao HUB</button>
<div id="m" class="msg"></div>
</section>
<script>
const _PATH_RESERVED=new Set(['','login','logout','api','assets','static','store-image','favicon.ico']);
function _basePrefix(){const p=String(window.location.pathname||'/');const segs=p.split('/').filter(Boolean);if(!segs.length)return '';const first=String(segs[0]||'').toLowerCase();if(_PATH_RESERVED.has(first))return '';return `/${segs[0]}`;}
const _BASE_PREFIX=_basePrefix();
function backToHub(){
  try{
    const ref=document.referrer?new URL(document.referrer):null;
    if(ref&&ref.origin){window.location.assign(ref.origin+'/');return;}
  }catch(_){}
  window.location.assign(new URL('/',window.location.origin).toString());
}
function initHubBackLogin(){const b=document.getElementById('hubBackLogin');if(!b)return;if(_BASE_PREFIX)b.classList.remove('hidden');}
async function login(){
  const u=document.getElementById('u').value||'';
  const p=document.getElementById('p').value||'';
  const m=document.getElementById('m');
  const b=document.getElementById('b');
  b.disabled=true;
  m.textContent='Validando acesso';
  try{
    const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})});
    const j=await r.json();
    if(r.ok&&j.ok){window.location.href='/';return;}
    m.textContent=j.message||'Usuário ou senha inválidos';
  }catch(_){
    m.textContent='Falha ao conectar com o servidor';
  }finally{
    b.disabled=false;
  }
}
['u','p'].forEach(id=>{document.getElementById(id).addEventListener('keydown',(e)=>{if(e.key==='Enter')login();});});
initHubBackLogin();
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _require_auth(self, parsed_path: str) -> str | None:
        user = _current_session_user(self)
        if user:
            return user
        if parsed_path.startswith("/api/"):
            _json_response(self, 401, {"ok": False, "message": "Não autenticado"})
        else:
            _redirect(self, "/login")
        return None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/assets/store-bg":
            if _send_store_image(self):
                return
            self.send_response(404)
            self.end_headers()
            return

        if parsed.path == "/login":
            if _current_session_user(self):
                return _redirect(self, "/")
            return _html_response(self, 200, _render_login_html())

        if parsed.path == "/logout":
            _destroy_session(self)
            return _redirect(self, "/login")

        current_user = self._require_auth(parsed.path)
        if not current_user:
            return

        if parsed.path == "/api/state":
            principal_email = _refresh_account_email("principal")
            nfe_email = _refresh_account_email("nfe")
            payload = {
                "settings": load_settings(),
                "last_run": _last_run,
                "runtime": _runtime_with_friendly(),
                "connected": {"principal": principal_email, "nfe": nfe_email},
                "report": _parse_report(),
                "manual": _manual_snapshot(),
                "auth": _auth_snapshot(current_user),
            }
            return _json_response(self, 200, payload)

        if parsed.path == "/api/diagnostics":
            with _diag_lock:
                items = list(_diagnostics[-30:])
            return _json_response(self, 200, {"items": items})

        if parsed.path == "/api/history":
            qs = parse_qs(parsed.query or "")
            dt_from = (qs.get("from", [""])[0] or "").strip()
            dt_to = (qs.get("to", [""])[0] or "").strip()
            cnpj_emit = (qs.get("cnpj_emit", [""])[0] or "").strip()
            cnpj_dest = (qs.get("cnpj_dest", [""])[0] or "").strip()
            query = (qs.get("q", [""])[0] or "").strip()
            try:
                limit = int((qs.get("limit", ["500"])[0] or "500").strip())
            except Exception:
                limit = 500
            items = query_events(
                dt_from=dt_from,
                dt_to=dt_to,
                cnpj_emit=cnpj_emit,
                cnpj_dest=cnpj_dest,
                event_type="boleto_lancado",
                query=query,
                limit=max(1, min(limit, 2000)),
            )
            for item in items:
                dest = str(item.get("cnpj_dest", "")).strip()
                if dest == CNPJ_MVA:
                    item["dest_label"] = "MVA"
                elif dest == CNPJ_EH:
                    item["dest_label"] = "EH"
                else:
                    item["dest_label"] = ""
            return _json_response(self, 200, {"items": items})

        if parsed.path == "/api/audit":
            if not _is_dev(current_user):
                return _json_response(self, 403, {"ok": False, "message": "Apenas dev pode visualizar o Registro"})
            qs = parse_qs(parsed.query or "")
            dt_from = (qs.get("from", [""])[0] or "").strip()
            dt_to = (qs.get("to", [""])[0] or "").strip()
            actor = (qs.get("user", [""])[0] or "").strip()
            action = (qs.get("action", [""])[0] or "").strip()
            query = (qs.get("q", [""])[0] or "").strip()
            try:
                limit = int((qs.get("limit", ["500"])[0] or "500").strip())
            except Exception:
                limit = 500
            items = query_audit_events(
                dt_from=dt_from,
                dt_to=dt_to,
                actor=actor,
                action=action,
                query=query,
                limit=max(1, min(limit, 2000)),
            )
            return _json_response(self, 200, {"items": items})

        if parsed.path == "/":
            return _html_response(self, 200, _render_html())

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        data = _read_json(self)

        if parsed.path == "/api/login":
            username = str(data.get("username", "")).strip()
            password = str(data.get("password", ""))
            if not _verify_login(username, password):
                return _json_response(self, 401, {"ok": False, "message": "Usuário ou senha inválidos"})
            token = _create_session(username)
            cookie = f"{_COOKIE_SESSION}={token}; Path=/; HttpOnly; Max-Age={_SESSION_TTL_SECONDS}; SameSite=Lax"
            return _json_response(self, 200, {"ok": True, "message": "Login efetuado"}, {"Set-Cookie": cookie})

        if parsed.path == "/api/logout":
            _destroy_session(self)
            return _json_response(
                self,
                200,
                {"ok": True, "message": "Logout efetuado"},
                {"Set-Cookie": f"{_COOKIE_SESSION}=; Path=/; HttpOnly; Max-Age=0; SameSite=Lax"},
            )

        current_user = self._require_auth(parsed.path)
        if not current_user:
            return

        if parsed.path == "/api/auth/change-password":
            current_password = str(data.get("current_password", ""))
            new_password = str(data.get("new_password", ""))
            ok, msg = _change_own_password(current_user, current_password, new_password)
            _audit(
                actor=current_user,
                action="senha_propria_alterar",
                target=_normalize_username(current_user),
                before={"senha": "oculta"},
                after={"senha": "oculta (alterada)"} if ok else {},
                status="ok" if ok else "erro",
                details=msg,
            )
            return _json_response(self, 200 if ok else 400, {"ok": ok, "message": msg})

        if parsed.path == "/api/auth/create-user":
            if not _is_dev(current_user):
                return _json_response(self, 403, {"ok": False, "message": "Apenas dev pode criar usuários"})
            username = str(data.get("username", ""))
            password = str(data.get("password", ""))
            role = str(data.get("role", "user"))
            target_user = _normalize_username(username)
            before_user = _auth_user_snapshot(target_user)
            ok, msg = _admin_create_user(current_user, username, password, role=role)
            after_user = _auth_user_snapshot(target_user)
            _audit(
                actor=current_user,
                action="usuario_criar",
                target=target_user,
                before={"situacao": "nao_existia"} if not before_user else {"situacao": "ja_existia", "perfil": str(before_user.get("role", ""))},
                after={"situacao": "criado", "perfil": str(after_user.get("role", role or "user"))} if ok else {},
                status="ok" if ok else "erro",
                details=msg,
            )
            return _json_response(self, 200 if ok else 400, {"ok": ok, "message": msg})

        if parsed.path == "/api/auth/delete-user":
            if not _is_dev(current_user):
                return _json_response(self, 403, {"ok": False, "message": "Apenas dev pode remover usuários"})
            username = str(data.get("username", ""))
            target_user = _normalize_username(username)
            before_user = _auth_user_snapshot(target_user)
            ok, msg = _admin_delete_user(current_user, username)
            after_user = _auth_user_snapshot(target_user)
            _audit(
                actor=current_user,
                action="usuario_remover",
                target=target_user,
                before={"situacao": "existia", "perfil": str(before_user.get("role", ""))} if before_user else {},
                after={"situacao": "removido"} if ok and not after_user else {},
                status="ok" if ok else "erro",
                details=msg,
            )
            return _json_response(self, 200 if ok else 400, {"ok": ok, "message": msg})

        if parsed.path == "/api/auth/reset-password":
            if not _is_dev(current_user):
                return _json_response(self, 403, {"ok": False, "message": "Apenas dev pode redefinir senha"})
            username = str(data.get("username", ""))
            new_password = str(data.get("new_password", ""))
            target_user = _normalize_username(username)
            before_user = _auth_user_snapshot(target_user)
            ok, msg = _admin_reset_password(current_user, username, new_password)
            after_user = _auth_user_snapshot(target_user)
            _audit(
                actor=current_user,
                action="senha_usuario_redefinir",
                target=target_user,
                before={"perfil": str(before_user.get("role", "")) if before_user else "", "senha": "oculta"},
                after={"perfil": str(after_user.get("role", "")) if after_user else "", "senha": "oculta (alterada)"} if ok else {},
                status="ok" if ok else "erro",
                details=msg,
            )
            return _json_response(self, 200 if ok else 400, {"ok": ok, "message": msg})

        if parsed.path == "/api/settings":
            if not _can_operate(current_user):
                return _json_response(self, 403, {"ok": False, "message": "Sem permissão para alterar configurações"})
            before_settings = load_settings()
            saved = save_settings(data)
            diff = _settings_diff(before_settings, saved)
            before_changed = {k: v.get("antes") for k, v in diff.items()}
            after_changed = {k: v.get("depois") for k, v in diff.items()}
            _audit(
                actor=current_user,
                action="configuracao_salvar",
                target="settings",
                before=before_changed,
                after=after_changed,
                status="ok",
                details=f"{len(diff)} configuracao(oes) alterada(s)" if diff else "Nenhuma alteração detectada",
            )
            return _json_response(self, 200, {"ok": True, "settings": saved})

        if parsed.path == "/api/reprocess":
            if not _can_operate(current_user):
                return _json_response(self, 403, {"ok": False, "message": "Sem permissão para reprocessar e-mails"})
            account = data.get("account", "principal")
            days = int(data.get("days", 30))
            max_messages = int(data.get("max_messages", 100))
            mark_unread = bool(data.get("mark_unread", True))
            req_payload = {
                "account": account,
                "days": days,
                "max_messages": max_messages,
                "mark_unread": mark_unread,
            }
            try:
                if account == "all":
                    result = {}
                    total_matched = 0
                    total_updated = 0
                    for acc in ("principal", "nfe"):
                        service = auth.get_gmail_service(acc)
                        item = _reprocess_recent(service, days, max_messages, mark_unread)
                        result[acc] = item
                        total_matched += int(item.get("matched", 0))
                        total_updated += int(item.get("updated", 0))
                    result["total"] = {"matched": total_matched, "updated": total_updated}
                else:
                    service = auth.get_gmail_service(account)
                    result = _reprocess_recent(service, days, max_messages, mark_unread)
                _audit(
                    actor=current_user,
                    action="reprocessar_emails",
                    target=str(account),
                    before=req_payload,
                    after=result,
                    status="ok",
                    details="Reprocessamento concluído",
                )
                return _json_response(self, 200, {"ok": True, "result": result})
            except Exception as e:
                _add_diagnostic("reprocess", e)
                _audit(
                    actor=current_user,
                    action="reprocessar_emails",
                    target=str(account),
                    before=req_payload,
                    after={},
                    status="erro",
                    details=str(e),
                )
                return _json_response(self, 400, {"ok": False, "friendly": _friendly_error(str(e)), "error": str(e)})

        if parsed.path == "/api/run-now":
            if not _can_operate(current_user):
                return _json_response(self, 403, {"ok": False, "message": "Sem permissão para executar agora"})
            account = data.get("account", "principal")
            with _manual_lock:
                if _manual_state["running"]:
                    _audit(
                        actor=current_user,
                        action="execucao_manual_iniciar",
                        target=str(account),
                        before={"running": True, "account": _manual_state.get("account", "")},
                        after={},
                        status="erro",
                        details="Execução manual já em andamento",
                    )
                    return _json_response(self, 409, {"ok": False, "message": "Execução manual já em andamento"})
            cfg = load_settings()
            interval_min = int(cfg.get("loop_interval_minutes", 30))
            interval_sec = max(60, interval_min * 60)
            runtime_status.request_next_cycle_reset(interval_sec)
            t = threading.Thread(target=_run_now, args=(account,), daemon=True)
            t.start()
            _audit(
                actor=current_user,
                action="execucao_manual_iniciar",
                target=str(account),
                before={"running": False},
                after={"running": True, "account": str(account), "reset_timer_seconds": interval_sec},
                status="ok",
                details="Execução manual iniciada",
            )
            return _json_response(self, 200, {"ok": True, "message": "Execução iniciada"})

        if parsed.path == "/api/run-stop":
            if not _can_operate(current_user):
                return _json_response(self, 403, {"ok": False, "message": "Sem permissão para parar execução"})
            with _manual_lock:
                if not _manual_state["running"]:
                    _audit(
                        actor=current_user,
                        action="execucao_manual_parar",
                        target="manual",
                        before={"running": False},
                        after={},
                        status="erro",
                        details="Não há execução manual ativa",
                    )
                    return _json_response(self, 400, {"ok": False, "message": "Não há execução manual ativa"})
                _manual_state["cancel_requested"] = True
            _manual_stop_event.set()
            _audit(
                actor=current_user,
                action="execucao_manual_parar",
                target=str(_manual_state.get("account", "manual")),
                before={"running": True, "cancel_requested": False},
                after={"running": True, "cancel_requested": True},
                status="ok",
                details="Solicitação de parada enviada",
            )
            return _json_response(self, 200, {"ok": True, "message": "Solicitação de parada enviada"})

        if parsed.path == "/api/reauth":
            if not _can_operate(current_user):
                return _json_response(self, 403, {"ok": False, "message": "Sem permissão para reautenticar"})
            account = data.get("account", "principal")
            try:
                conta_ok = auth.reautenticarGmail(account)
                _refresh_account_email(conta_ok, force=True)
                runtime_status.set_account_status(conta_ok, "ok", "Reautenticação concluída")
                _audit(
                    actor=current_user,
                    action="reautenticar_gmail",
                    target=str(conta_ok),
                    before={"account": str(account)},
                    after={"account": str(conta_ok), "status": "ok"},
                    status="ok",
                    details="Reautenticação concluída",
                )
                return _json_response(self, 200, {"ok": True, "message": f"Reautenticação da conta '{conta_ok}' concluída"})
            except Exception as e:
                _add_diagnostic(f"reauth_{account}", e)
                runtime_status.set_account_status(account, "error", _friendly_error(str(e)))
                _audit(
                    actor=current_user,
                    action="reautenticar_gmail",
                    target=str(account),
                    before={"account": str(account)},
                    after={},
                    status="erro",
                    details=str(e),
                )
                return _json_response(self, 400, {"ok": False, "friendly": _friendly_error(str(e)), "error": str(e)})

        self.send_response(404)
        self.end_headers()


def _render_html() -> str:
    return """<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>FinanceBot - Painel</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Lexend:wght@300;400;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--o:#da7a1c;--o2:#ee9b2f;--b:#4a2b18;--b2:#6b4128;--ok:#2e7d32;--w:#d68c1a;--e:#c62828;--i:#175ea8;--cd:#6a1b9a}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font-family:'Lexend',Arial,sans-serif;background:linear-gradient(160deg,rgba(41,22,11,.78),rgba(95,56,28,.72)),url('/assets/store-bg') center/cover fixed;display:flex;justify-content:center;align-items:center;padding:12px;color:#2a1b12}
.app{width:min(1150px,100%);border-radius:18px;overflow:hidden;border:1px solid rgba(231,200,168,.9);background:linear-gradient(180deg,rgba(255,250,246,.96),rgba(255,245,235,.92));box-shadow:0 24px 60px rgba(21,11,6,.35)}
.top{padding:14px 20px;background:linear-gradient(90deg,var(--b),var(--o));color:#fff9f3;font-weight:700;display:flex;justify-content:space-between;align-items:center;gap:8px}
.top-right{display:flex;align-items:center;gap:8px}
.whoami{font-size:.82rem;opacity:.95}
.logout-btn{padding:7px 10px;border:1px solid rgba(255,244,234,.5);border-radius:8px;background:rgba(255,244,234,.12);color:#fff9f3;font-weight:700;cursor:pointer}
.logout-btn:hover{background:rgba(255,244,234,.2)}
.hub-back-btn{margin-right:8px}
.tabs{display:flex;gap:8px;padding:10px 10px 0}
.tab-btn{padding:8px 12px;border-radius:9px;border:1px solid #d7b393;background:#fff5ea;color:#5a311b;font-weight:700;cursor:pointer}
.tab-btn.active{background:linear-gradient(90deg,var(--o),var(--o2));color:#2b1408;border-color:#ca894f}
.hidden{display:none!important}
.tab-panel.hidden{display:none}
.c{padding:10px;display:grid;gap:9px}.card{background:rgba(255,248,240,.92);border:1px solid #e7c8a8;border-radius:13px;padding:10px}
h3{margin:0 0 8px;color:var(--b);font-size:.98rem}label{display:block;margin-top:6px;font-weight:600;color:#5c341c;font-size:.9rem}
input,select{width:100%;padding:8px;margin-top:4px;border:1px solid #d6b18f;border-radius:8px;background:#fffdfb;font-family:inherit}
input[type="checkbox"]{width:auto;padding:0;margin:0;border:0;background:transparent}
input[type="number"]::-webkit-outer-spin-button,input[type="number"]::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
input[type="number"]{-moz-appearance:textfield;appearance:textfield}
.row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.status{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.s{border:1px solid #d5b08f;background:#fffaf6;border-radius:11px;padding:10px}.h{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;font-weight:700;color:#5b321c}
.pill{display:inline-flex;align-items:center;gap:6px;padding:3px 8px;border-radius:999px;font-size:.76rem;font-weight:700;border:1px solid transparent}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block}.ok{color:var(--ok);background:#ebf7ec;border-color:#b8dfbb}.ok .dot{background:var(--ok)}
.warn{color:#8a5a10;background:#fff6e8;border-color:#f4dbb3}.warn .dot{background:var(--w)}
.err{color:var(--e);background:#fdecec;border-color:#f3b9b9}.err .dot{background:var(--e)}
.cd{color:var(--cd);background:#f5ebff;border-color:#d9b7ef}.cd .dot{background:var(--cd)}
.info{color:var(--i);background:#e9f2fc;border-color:#bfd7f4}.muted{color:#6c4a35;font-size:.84rem}.problem{color:#862818;font-size:.82rem;margin-top:4px}
.btns{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}button{padding:9px 12px;border:0;border-radius:9px;background:linear-gradient(90deg,var(--o),var(--o2));color:#2b1408;font-weight:700;cursor:pointer}
button.sec{background:linear-gradient(90deg,var(--b),var(--b2));color:#fff4ea}.cb{margin-top:8px;display:inline-flex;align-items:center;justify-content:flex-start;gap:8px;font-size:.95rem;white-space:nowrap;line-height:1.1;width:fit-content;max-width:100%}.cb span{white-space:nowrap;display:inline-block}.cb input{width:auto;margin:0;padding:0;flex:0 0 auto}
button:disabled{cursor:not-allowed;opacity:.55;filter:saturate(.45)}
.stop-btn-locked{background:linear-gradient(90deg,#8b7e73,#9b8f84)!important;color:#f3ede8!important;border:1px solid #a79b90}
.stop-btn-active{background:linear-gradient(90deg,#8c1f1f,#b43030)!important;color:#fff6f6!important;opacity:1!important;filter:none!important}
.kpi{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.k{border:1px solid #deb999;background:#fff9f3;border-radius:10px;padding:9px}.n{font-size:1.2rem;font-weight:800}.t{font-size:.78rem;color:#6b4128}
.lists{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}.box{border:1px solid #e4c6a7;border-radius:10px;background:#fffdfb;padding:8px}.box h4{margin:0 0 6px;font-size:.85rem;color:#58311b}.box ul{margin:0;padding-left:16px;max-height:160px;overflow:auto}.box li{margin:3px 0;font-size:.8rem}
.cfg-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(140px,170px) minmax(220px,260px);gap:10px;align-items:start}
.cfg-main-card,.cfg-auth-card{height:250px}
.cfg-main-card h3{text-align:center}
.cfg-main{display:grid;gap:8px}
.cfg-fields{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;align-items:end;justify-items:center}
.cfg-fields > div{display:flex;flex-direction:column;align-items:center}
.cfg-fields > div label{text-align:center}
.cfg-fields > div input,.cfg-fields > div select{width:min(170px,100%);text-align:center}
.cfg-fields > div input.num-sm{width:min(78px,100%)}
.cfg-fields > div select.mode-wide{width:min(210px,100%)}
.cfg-save{display:flex;justify-content:center;align-items:center}
.cfg-save button{min-width:0;width:auto}
.cfg-status{display:flex;flex-direction:column;align-items:center}
.cfg-status label{text-align:center}
.cfg-status input{width:min(240px,100%);text-align:center}
.cfg-auth-card{padding:10px;display:flex;flex-direction:column;height:250px}
.cfg-auth-card h3{text-align:center}
.btns.stack{flex-direction:column;align-items:stretch}
.btns.stack button{width:100%;margin:0}
.cfg-auth-card .btns.stack{flex:1;justify-content:center;align-items:center;margin:0}
.cfg-auth-card .btns.stack button{width:min(170px,100%)}
.cfg-sec-card{grid-column:1 / span 2;grid-row:2}
.sec-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;align-items:start}
.sec-box{border:1px solid #e4c6a7;border-radius:10px;background:#fffdfb;padding:7px;align-self:start}
.sec-box h4{margin:0 0 6px;font-size:.86rem;color:#58311b}
.sec-row{display:grid;gap:4px}
.sec-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
.sec-actions button{width:auto}
.exp-tabs{display:grid;gap:6px}
.exp-tab{border:1px solid #e4c6a7;border-radius:8px;background:#fffaf6;overflow:hidden}
.exp-toggle{width:100%;border:0;cursor:pointer;padding:7px 9px;font-weight:700;color:#5a311b;background:#fff1e3;display:flex;align-items:center;justify-content:space-between;text-align:left}
.exp-toggle:after{content:"+";font-weight:800}
.exp-tab.open .exp-toggle:after{content:"-"}
.exp-body{padding:0 7px;max-height:0;opacity:0;overflow:hidden;transition:max-height .28s ease,opacity .22s ease,padding .22s ease}
.exp-tab.open .exp-body{padding:7px;opacity:1}
.admin-only{display:none}
.admin-only.show{display:block}
.user-tags{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.user-tag{border:1px solid #d6b18f;border-radius:999px;padding:2px 8px;background:#fff4e7;font-size:.78rem;color:#5a311b}
.mini-note{font-size:.78rem;color:#6c4a35;margin-top:2px}
.pwd-reqs{margin:4px 0 0 18px;padding:0}
.pwd-reqs li{font-size:.78rem;color:#8b3d33}
.pwd-reqs li.ok{color:#2e7d32;font-weight:600}
.field-error{border:2px solid #c62828!important;box-shadow:0 0 0 2px rgba(198,40,40,.14)}
.reproc-card{padding:10px;display:flex;flex-direction:column;height:auto;align-self:start;grid-column:3;grid-row:1 / span 2}
.reproc-card h3{text-align:center}
.reproc-stack{display:grid;gap:6px}
.reproc-stack > div{display:flex;flex-direction:column;align-items:center}
.reproc-stack > div label{text-align:center}
.reproc-stack > div select,.reproc-stack > div input{text-align:center}
.reproc-stack #account{width:min(190px,100%)}
.reproc-stack #days,.reproc-stack #limit{width:min(90px,100%)}
.reproc-card .cb{justify-content:center;margin:6px auto 0}
.reproc-card .btns.stack{margin-top:8px;align-items:center}
.reproc-card .btns.stack button{width:min(210px,100%)}
.hist-filters{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:8px;align-items:end}
.hist-filters > div{display:flex;flex-direction:column;justify-content:center;align-items:center}
.hist-filters > div label{width:100%;text-align:center}
.hist-filters > div input,.hist-filters > div select{width:100%;text-align:center}
.hist-filters .search-wide{grid-column:span 2}
.reg-filters{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:8px;align-items:end}
.reg-filters > div{display:flex;flex-direction:column;justify-content:center;align-items:center}
.reg-filters > div label{width:100%;text-align:center}
.reg-filters > div input,.reg-filters > div select{width:100%;text-align:center}
.reg-filters .search-wide{grid-column:span 2}
.table-wrap{width:100%;overflow:auto;border:1px solid #e4c6a7;border-radius:10px;background:#fffdfb}
.hist-table{width:100%;border-collapse:collapse;font-size:.82rem;table-layout:fixed}
.hist-table th,.hist-table td{border-bottom:1px solid #edd4bc;padding:7px 8px;text-align:center;vertical-align:top;white-space:normal;word-break:break-word}
.hist-table th{position:sticky;top:0;background:#fff1e3;color:#5c341c;z-index:1}
.hist-table th.sortable{cursor:pointer;user-select:none}
.hist-table th.sortable:after{content:" <>";font-size:.75rem;color:#b0672d}
.hist-table th.sortable.asc:after{content:" ^"}
.hist-table th.sortable.desc:after{content:" v"}
.hist-table td:last-child{max-width:360px;white-space:normal}
.reg-table{width:100%;border-collapse:collapse;font-size:.8rem;table-layout:fixed}
.reg-table th,.reg-table td{border-bottom:1px solid #edd4bc;padding:7px 8px;text-align:center;vertical-align:top;white-space:normal;word-break:break-word}
.reg-table th{position:sticky;top:0;background:#fff1e3;color:#5c341c;z-index:1}
.audit-status{display:inline-flex;align-items:center;justify-content:center;padding:2px 8px;border-radius:999px;font-size:.75rem;font-weight:700}
.audit-status.ok{background:#e8f6ea;color:#2e7d32;border:1px solid #b6dfbf}
.audit-status.erro{background:#fdecec;color:#b42b2b;border:1px solid #f1bbbb}
.cell-menu{position:relative;display:flex;align-items:center;gap:6px}
.cell-text{display:inline-block;max-width:220px}
.cell-btn{padding:0;border:0;background:transparent;color:#5a311b;font-weight:700;cursor:pointer;text-align:left}
.cell-btn:hover{text-decoration:underline}
.cell-pop{position:absolute;top:100%;left:0;background:#fffaf6;border:1px solid #e7c8a8;border-radius:8px;padding:6px;box-shadow:0 8px 20px rgba(21,11,6,.15);display:none;z-index:5;min-width:160px}
.cell-pop button{width:100%;border:0;background:#fff1e3;padding:6px;border-radius:6px;cursor:pointer;font-size:.78rem;color:#5a311b}
.cell-menu.open .cell-pop{display:block}
pre{margin:6px 0 0;background:#fff7ef;border:1px dashed #cf9f78;padding:8px;border-radius:10px;overflow:auto;max-height:220px;font-size:12px}
.ov{position:fixed;inset:0;z-index:99999;display:none;align-items:center;justify-content:center;background:rgba(22,10,5,.78);backdrop-filter:blur(3px)}.ov.show{display:flex}
.ovb{width:min(440px,92vw);border-radius:14px;border:1px solid #f0c89d;background:linear-gradient(180deg,#fff6ec,#ffe8d4);text-align:center;padding:18px}.cnt{margin-top:12px;font-size:2.4rem;font-weight:800;color:#b05714}
.toast{position:fixed;right:16px;bottom:16px;z-index:99998;background:linear-gradient(180deg,#fff6ec,#ffe8d4);border:1px solid #f0c89d;color:#5a311b;padding:10px 12px;border-radius:10px;box-shadow:0 10px 30px rgba(21,11,6,.2);opacity:0;transform:translateY(8px);pointer-events:none;transition:opacity .2s ease,transform .2s ease;max-width:min(380px,92vw)}.toast.show{opacity:1;transform:translateY(0)}
@media(max-width:1200px){.cfg-grid{grid-template-columns:1fr 1fr}.cfg-sec-card,.reproc-card{grid-column:1 / span 2;grid-row:auto}}
@media(max-width:1020px){.row{grid-template-columns:1fr 1fr}.status,.lists{grid-template-columns:1fr}.kpi{grid-template-columns:1fr 1fr}.cfg-fields{grid-template-columns:1fr 1fr}.hist-filters,.reg-filters{grid-template-columns:1fr 1fr 1fr}}
@media(max-width:640px){.row{grid-template-columns:1fr}.btns{flex-direction:column}button{width:100%}.cfg-grid{grid-template-columns:1fr}.cfg-main-card,.cfg-auth-card{height:auto}.cfg-sec-card,.reproc-card{grid-column:auto}.sec-grid{grid-template-columns:1fr}.cfg-fields{grid-template-columns:1fr}.hist-filters,.reg-filters{grid-template-columns:1fr}.top-right{flex-direction:column;align-items:flex-end}}
</style></head><body>
<div id="ov" class="ov"><div class="ovb"><h4>Reautenticação em andamento</h4><p>Troque para a conta correta no navegador<br/>A autenticação começará em:</p><div id="cnt" class="cnt">5</div></div></div>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
<main class="app"><div class="top"><span>FinanceBot - Painel de Controle MVA</span><div class="top-right"><span id="whoami" class="whoami">Usuário: -</span><button id="backHubBtn" class="logout-btn hub-back-btn hidden" onclick="goHub()">Voltar ao HUB</button><button class="logout-btn" onclick="logout()">Sair</button></div></div>
<div class="tabs"><button id="tabBtnMain" class="tab-btn active" onclick="switchTab('main')">Painel</button><button id="tabBtnHist" class="tab-btn" onclick="switchTab('hist')">Histórico</button><button id="tabBtnReg" class="tab-btn hidden" onclick="switchTab('reg')">Registro</button><button id="tabBtnDiag" class="tab-btn" onclick="switchTab('diag')">Diagnóstico</button></div>
<div id="tabMain" class="c tab-panel">
<section class="card"><h3>Status das contas de e-mail</h3><div class="status">
<article class="s"><div class="h"><span>Conta Principal</span><span id="pillP" class="pill warn"><span class="dot"></span>Esperando</span></div><div id="mailP" class="muted">E-mail conectado: -</div><div id="detP" class="muted">Aguardando</div><div id="probP" class="problem"></div></article>
<article class="s"><div class="h"><span>Conta Secundária</span><span id="pillN" class="pill warn"><span class="dot"></span>Esperando</span></div><div id="mailN" class="muted">E-mail conectado: -</div><div id="detN" class="muted">Aguardando</div><div id="probN" class="problem"></div></article>
</div><div id="cool" class="muted" style="margin-top:8px">Próxima verificação automática: sem contagem no momento</div></section>

<section class="card"><h3>Relatório diário</h3>
<div class="kpi"><div class="k"><div id="kp1" class="n">0</div><div class="t">Processados</div></div><div class="k"><div id="kp2" class="n">0</div><div class="t">Ignorados</div></div><div class="k"><div id="kp3" class="n">0</div><div class="t">Avisos no ciclo</div></div><div class="k"><div id="kp4" class="n">0</div><div class="t">Avisos no dia</div></div></div>
<div id="rmeta" class="muted">Sem relatório encontrado ainda</div>
<div class="lists" style="margin-top:8px"><div class="box"><h4>Últimos processados</h4><ul id="lp"></ul></div><div class="box"><h4>Últimos ignorados</h4><ul id="li"></ul></div><div class="box"><h4>Avisos recentes</h4><ul id="la"></ul></div></div>
</section>
<section class="cfg-grid">
<section class="card cfg-main-card"><h3>Configuração do Gmail</h3>
<div class="cfg-main">
<div class="cfg-fields"><div><label>Período</label><select id="mode" class="mode-wide"><option value="last_15_days">Últimos 15 dias</option><option value="last_30_days">Últimos 30 dias</option><option value="last_45_days">Últimos 45 dias</option><option value="last_60_days">Últimos 60 dias</option><option value="current_week">Semana atual</option><option value="previous_month">Mês anterior</option><option value="current_and_previous_month">Mês atual + mês anterior</option></select></div><div><label>Máx páginas</label><input id="maxPages" class="num-sm" type="number" min="1" max="20"/></div><div><label>Tamanho da página</label><input id="pageSize" class="num-sm" type="number" min="1" max="500"/></div><div><label>Intervalo de leitura</label><input id="intervalMin" class="num-sm" type="number" min="1" max="720"/></div></div>
<div class="cfg-save"><button onclick="saveSettings()">Salvar configuração</button></div>
<div class="cfg-status"><label>Status da última execução</label><input id="last" type="text" readonly/></div>
</div>
</section>
<section class="card cfg-auth-card"><h3>Autenticação</h3><div class="btns stack"><button class="sec" onclick="reauth('principal')">Principal</button><button class="sec" onclick="reauth('nfe')">Secundária</button></div></section>
<section class="card reproc-card"><h3>Reprocessar e-mails</h3>
<div class="reproc-stack"><div><label>Conta</label><select id="account"><option value="all">Todos</option><option value="principal">E-mail Principal</option><option value="nfe">E-mail Secundário</option></select></div><div><label>Dias para trás</label><input id="days" type="number" value="30" min="1" max="365"/></div><div><label>Limite de mensagens</label><input id="limit" type="number" value="100" min="1" max="1000"/></div></div>
<label class="cb"><input id="unread" type="checkbox" checked/><span>Marcar como não lido</span></label>
<div class="btns stack"><button onclick="reprocess()">Remover labels para reprocessar</button><button id="runNowBtn" class="sec" onclick="runNow()">Executar agora</button><button id="stopNowBtn" class="sec stop-btn-locked" onclick="stopRunNow()" disabled>Parar</button></div></section>
<section class="card cfg-sec-card"><h3>Configurações</h3><div class="sec-grid">
<div class="sec-box"><h4>Reiniciar senha</h4><div class="sec-row"><div><label>Senha atual</label><input id="pwdCurr" type="password" autocomplete="current-password"/></div><div><label>Nova senha</label><input id="pwdNew" type="password" autocomplete="new-password"/></div><ul class="pwd-reqs"><li id="reqLen">* Mínimo 6 caracteres</li><li id="reqLower">* Pelo menos uma letra minúscula</li><li id="reqUpper">* Pelo menos uma letra maiúscula</li><li id="reqDigit">* Pelo menos um número</li><li id="reqSpec">* Pelo menos um caractere especial</li></ul><div><label>Confirmar nova senha</label><input id="pwdNew2" type="password" autocomplete="new-password"/></div><div class="sec-actions"><button class="sec" onclick="changeOwnPassword()">Atualizar minha senha</button></div><div class="mini-note">Os requisitos ficam verdes conforme a senha atende cada regra</div></div></div>
<div id="adminArea" class="sec-box admin-only"><h4>Administração de usuários</h4><div id="userTags" class="user-tags"></div><div class="exp-tabs">
<section class="exp-tab"><button type="button" class="exp-toggle" aria-expanded="false">Criar novo usuário</button><div class="exp-body"><div class="sec-row"><div><label>Novo usuário</label><input id="newUser" type="text" placeholder="usuario.exemplo"/></div><div><label>Senha do novo usuário</label><input id="newUserPwd" type="password" autocomplete="new-password"/></div><div><label>Confirmar senha</label><input id="newUserPwd2" type="password" autocomplete="new-password"/></div><div><label>Perfil</label><select id="newUserRole"><option value="user">Usuário</option><option value="admin">Admin</option></select></div><div class="sec-actions"><button onclick="adminCreateUser()">Criar usuário</button></div><div><label>Remover usuário</label><select id="delUser"></select></div><div class="sec-actions"><button class="sec" onclick="adminDeleteUser()">Remover usuário</button></div></div></div></section>
<section class="exp-tab"><button type="button" class="exp-toggle" aria-expanded="false">Redefinir senha (qualquer usuário)</button><div class="exp-body"><div class="sec-row"><div><label>Usuário</label><select id="rstUser"></select></div><div><label>Nova senha</label><input id="rstPwd" type="password" autocomplete="new-password"/></div><div><label>Confirmar nova senha</label><input id="rstPwd2" type="password" autocomplete="new-password"/></div><div class="sec-actions"><button class="sec" onclick="adminResetPassword()">Redefinir senha</button></div></div></div></section>
</div></div>
</div></section>
</section>

</div>
<div id="tabHist" class="c tab-panel hidden">
<section class="card"><h3>Histórico de processamento e lançamentos</h3>
<div class="hist-filters">
<div><label>Data inicial</label><input id="hFrom" type="date"/></div>
<div><label>Data final</label><input id="hTo" type="date"/></div>
<div><label>CNPJ emitente</label><input id="hEmit" type="text" placeholder="Somente números"/></div>
<div><label>CNPJ destinatário</label><input id="hDest" type="text" placeholder="Somente números"/></div>
<div class="search-wide"><label>Busca</label><input id="hQuery" type="text" placeholder="Fornecedor, documento, aba"/></div>
<div><label>Limite</label><input id="hLimit" type="number" min="10" max="2000" value="300"/></div>
<div style="display:flex;align-items:end"><button onclick="loadHistory()">Aplicar filtros</button></div>
</div>
<div class="table-wrap" style="margin-top:10px">
<table class="hist-table">
<thead><tr><th class="sortable" data-key="at">Data/Hora</th><th class="sortable" data-key="conta">Conta</th><th class="sortable" data-key="doc">Documento</th><th class="sortable" data-key="fornecedor">Fornecedor</th><th class="sortable" data-key="dest">Destino</th><th class="sortable" data-key="local">Lançado em</th><th class="sortable" data-key="detalhe">Detalhes</th></tr></thead>
<tbody id="hBody"><tr><td colspan="7">Sem dados</td></tr></tbody>
</table>
</div>
</section>
</div>
<div id="tabReg" class="c tab-panel hidden">
<section class="card"><h3>Registro de alterações</h3>
<div class="reg-filters">
<div><label>Data inicial</label><input id="aFrom" type="date"/></div>
<div><label>Data final</label><input id="aTo" type="date"/></div>
<div><label>Usuário</label><input id="aUser" type="text" placeholder="Exemplo: dev"/></div>
<div><label>Ação</label><select id="aAction"><option value="">Todas</option><option value="configuracao_salvar">Configurações</option><option value="senha_propria_alterar">Senha própria</option><option value="senha_usuario_redefinir">Senha de usuário</option><option value="usuario_criar">Criar usuário</option><option value="usuario_remover">Remover usuário</option><option value="reautenticar_gmail">Reautenticação</option><option value="reprocessar_emails">Reprocessar e-mails</option><option value="execucao_manual_iniciar">Executar agora</option><option value="execucao_manual_parar">Parar execução</option></select></div>
<div class="search-wide"><label>Busca</label><input id="aQuery" type="text" placeholder="Usuário, ação, alvo, detalhes"/></div>
<div><label>Limite</label><input id="aLimit" type="number" min="10" max="2000" value="300"/></div>
<div style="display:flex;align-items:end"><button onclick="loadAudit()">Aplicar filtros</button></div>
</div>
<div class="table-wrap" style="margin-top:10px">
<table class="reg-table">
<thead><tr><th>Data/Hora</th><th>Usuário</th><th>Ação</th><th>Alvo</th><th>Status</th><th>Detalhes</th></tr></thead>
<tbody id="aBody"><tr><td colspan="6">Sem dados</td></tr></tbody>
</table>
</div>
</section>
</div>
<div id="tabDiag" class="c tab-panel hidden"><section class="card"><h3>Diagnóstico</h3><div id="fr" class="pill info"><span class="dot"></span>Nenhum erro recente</div><pre id="tech"></pre></section></div>
</main>
<script>
const tech=document.getElementById('tech');
function switchTab(tab){const main=document.getElementById('tabMain');const hist=document.getElementById('tabHist');const reg=document.getElementById('tabReg');const diag=document.getElementById('tabDiag');const bMain=document.getElementById('tabBtnMain');const bHist=document.getElementById('tabBtnHist');const bReg=document.getElementById('tabBtnReg');const bDiag=document.getElementById('tabBtnDiag');if(tab==='reg'&&!_authCtx.can_view_audit){tab='main';}main.classList.add('hidden');hist.classList.add('hidden');reg.classList.add('hidden');diag.classList.add('hidden');bMain.classList.remove('active');bHist.classList.remove('active');bReg.classList.remove('active');bDiag.classList.remove('active');if(tab==='diag'){diag.classList.remove('hidden');bDiag.classList.add('active');}else if(tab==='hist'){hist.classList.remove('hidden');bHist.classList.add('active');}else if(tab==='reg'){reg.classList.remove('hidden');bReg.classList.add('active');loadAudit(true);}else{main.classList.remove('hidden');bMain.classList.add('active');}}
const fmt=(s)=>{
  s=Math.max(0,Number(s||0));
  const h=Math.floor(s/3600);
  const m=Math.floor((s%3600)/60);
  const ss=Math.floor(s%60);
  if(h>0)return `${h}h ${m}min ${ss}s`;
  if(m>0)return `${m}min ${ss}s`;
  return `${ss}s`;
};
const pill=(id,st)=>{const e=document.getElementById(id);let l='Esperando',c='warn';if(st==='ok'){l='Funcionando';c='ok'}else if(st==='error'){l='Com problema';c='err'}else if(st==='running'){l='Lendo e-mails';c='warn'}else if(st==='cooldown'){l='Limite atingido';c='cd'}e.className=`pill ${c}`;e.innerHTML=`<span class="dot"></span>${l}`};
const box=(msg,k)=>{const e=document.getElementById('fr');const c=k==='error'?'err':(k==='warn'?'warn':'info');e.className=`pill ${c}`;e.innerHTML=`<span class="dot"></span>${msg}`};
const fill=(id,arr)=>{const ul=document.getElementById(id);ul.innerHTML='';const a=Array.isArray(arr)?arr:[];if(!a.length){const li=document.createElement('li');li.textContent='Sem itens';ul.appendChild(li);return;}a.forEach(x=>{const li=document.createElement('li');li.textContent=String(x);ul.appendChild(li);});};
const maskXml=(txt)=>{let s=String(txt||'');s=s.replace(/(\\d{8,})(-(?:nfe|cte))?\\.xml/gi,(_,d,suf)=>`***${d.slice(-8)}${suf||''}.xml`);s=s.replace(/\\b(\\d{44})\\b/g,(_,d)=>`***${d.slice(-8)}`);return s;};
let _toastTimer=null;
function showToast(msg){const t=document.getElementById('toast');if(!t)return;t.textContent=msg;clearTimeout(_toastTimer);t.classList.add('show');_toastTimer=setTimeout(()=>{t.classList.remove('show');},2800);}
const _PATH_RESERVED=new Set(['','login','logout','api','assets','static','store-image','favicon.ico']);
function _basePrefix(){const p=String(window.location.pathname||'/');const segs=p.split('/').filter(Boolean);if(!segs.length)return '';const first=String(segs[0]||'').toLowerCase();if(_PATH_RESERVED.has(first))return '';return `/${segs[0]}`;}
const _BASE_PREFIX=_basePrefix();
function _url(path){const p=String(path||'');if(!p.startsWith('/'))return p;if(!_BASE_PREFIX)return p;return p.startsWith(`${_BASE_PREFIX}/`)||p===_BASE_PREFIX?p:`${_BASE_PREFIX}${p}`;}
function goHub(){
  try{
    const ref=document.referrer?new URL(document.referrer):null;
    if(ref&&ref.origin&&ref.origin!==window.location.origin){
      window.location.assign(ref.origin+'/');
      return;
    }
  }catch(_){}
  const target=new URL('/',window.location.origin).toString();
  window.location.assign(target);
}
function initHubBackButton(){const b=document.getElementById('backHubBtn');if(!b)return;if(_BASE_PREFIX)b.classList.remove('hidden');else b.classList.add('hidden');}
async function api(url,opts){const r=await fetch(_url(url),opts);const j=await r.json().catch(()=>({}));if(r.status===401){window.location.href=_url('/login');throw new Error('nao autenticado');}return {r,j};}
async function logout(){await fetch(_url('/api/logout'),{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).catch(()=>{});window.location.href=_url('/login');}
const _cfgEditIds=['mode','maxPages','pageSize','intervalMin'];
let _cfgDirty=false;
let _authCtx={user:'',role:'user',is_admin:false,is_dev:false,can_operate:false,can_manage_users:false,can_change_password:false,can_view_audit:false,users:[]};
function _cfgEditingNow(){const a=document.activeElement;return !!(a&&_cfgEditIds.includes(a.id));}
function _markCfgDirty(){_cfgDirty=true;}
_cfgEditIds.forEach(id=>{const el=document.getElementById(id);if(!el)return;el.addEventListener('input',_markCfgDirty);el.addEventListener('change',_markCfgDirty);});
function _markFieldError(id,on=true){const el=document.getElementById(id);if(!el)return;el.classList.toggle('field-error',!!on);}
function _clearPwdFieldErrors(){['pwdCurr','pwdNew','pwdNew2'].forEach(id=>_markFieldError(id,false));}
function _pwdRules(pwd){
  const p=String(pwd||'');
  return {
    len:p.length>=6,
    low:/[a-z]/.test(p),
    up:/[A-Z]/.test(p),
    dig:/\\d/.test(p),
    sp:/[^A-Za-z0-9]/.test(p),
  };
}
function _updatePwdReqUi(){
  const r=_pwdRules(document.getElementById('pwdNew')?.value||'');
  const map=[['reqLen',r.len],['reqLower',r.low],['reqUpper',r.up],['reqDigit',r.dig],['reqSpec',r.sp]];
  map.forEach(([id,ok])=>{const el=document.getElementById(id);if(el)el.classList.toggle('ok',!!ok);});
  return r;
}
function _setPanelWriteAccess(canWrite){
  const fields=['mode','maxPages','pageSize','intervalMin','account','days','limit','unread'];
  fields.forEach(id=>{const el=document.getElementById(id);if(el)el.disabled=!canWrite;});
  const btnSelectors=[
    'button[onclick="saveSettings()"]',
    'button[onclick="reprocess()"]',
    'button[onclick="runNow()"]',
    'button[onclick="stopRunNow()"]',
    'button[onclick="reauth(\\'principal\\')"]',
    'button[onclick="reauth(\\'nfe\\')"]',
  ];
  btnSelectors.forEach(sel=>{document.querySelectorAll(sel).forEach(b=>{b.disabled=!canWrite;});});
}
function _setPasswordAccess(canChange){
  ['pwdCurr','pwdNew','pwdNew2'].forEach(id=>{const el=document.getElementById(id);if(el)el.disabled=!canChange;});
  document.querySelectorAll('button[onclick="changeOwnPassword()"]').forEach((b)=>{b.disabled=!canChange;});
}
function _setAuthUi(auth){
  _authCtx={
    user:String(auth&&auth.user||''),
    role:String(auth&&auth.role||'user'),
    is_admin:Boolean(auth&&auth.is_admin),
    is_dev:Boolean(auth&&auth.is_dev),
    can_operate:Boolean(auth&&auth.can_operate),
    can_manage_users:Boolean(auth&&auth.can_manage_users),
    can_change_password:Boolean(auth&&auth.can_change_password),
    can_view_audit:Boolean(auth&&auth.can_view_audit),
    users:Array.isArray(auth&&auth.users)?auth.users:[]
  };
  const who=document.getElementById('whoami');
  if(who)who.textContent=`Usuário: ${_authCtx.user||'-'} (${_authCtx.role||'user'})`;
  const regBtn=document.getElementById('tabBtnReg');
  if(regBtn)regBtn.classList.toggle('hidden',!_authCtx.can_view_audit);
  if(!_authCtx.can_view_audit){
    const regPanel=document.getElementById('tabReg');
    const regActiveBtn=document.getElementById('tabBtnReg');
    if(regPanel&&!regPanel.classList.contains('hidden'))switchTab('main');
    if(regActiveBtn)regActiveBtn.classList.remove('active');
  }
  const admin=document.getElementById('adminArea');
  if(admin)admin.classList.toggle('show',_authCtx.can_manage_users);
  _setPanelWriteAccess(_authCtx.can_operate);
  _setPasswordAccess(_authCtx.can_change_password);
  if(!_authCtx.can_manage_users)return;
  const tags=document.getElementById('userTags');
  if(tags){
    tags.innerHTML='';
    _authCtx.users.forEach(u=>{const sp=document.createElement('span');sp.className='user-tag';sp.textContent=`${u.username} (${u.role})`;tags.appendChild(sp);});
  }
  const del=document.getElementById('delUser'); const rst=document.getElementById('rstUser');
  if(del){del.innerHTML='';_authCtx.users.filter(u=>u.username!==_authCtx.user).forEach(u=>{const o=document.createElement('option');o.value=u.username;o.textContent=u.username;del.appendChild(o);});}
  if(rst){rst.innerHTML='';_authCtx.users.forEach(u=>{const o=document.createElement('option');o.value=u.username;o.textContent=u.username;rst.appendChild(o);});}
}
function _bindExpanders(){
  document.querySelectorAll('.exp-tab').forEach((tab)=>{
    const btn=tab.querySelector('.exp-toggle');
    const body=tab.querySelector('.exp-body');
    if(!btn||!body)return;
    tab.classList.remove('open');
    body.style.maxHeight='0px';
    btn.setAttribute('aria-expanded','false');
    btn.addEventListener('click',()=>{
      const isOpen=tab.classList.contains('open');
      if(isOpen){
        const h=body.scrollHeight;
        body.style.maxHeight=`${h}px`;
        requestAnimationFrame(()=>{
          tab.classList.remove('open');
          btn.setAttribute('aria-expanded','false');
          body.style.maxHeight='0px';
        });
        return;
      }
      tab.classList.add('open');
      btn.setAttribute('aria-expanded','true');
      body.style.maxHeight=`${body.scrollHeight}px`;
      setTimeout(()=>{if(tab.classList.contains('open'))body.style.maxHeight='none';},300);
    });
  });
}
function syncManualButtons(man){const runBtn=document.getElementById('runNowBtn');const stopBtn=document.getElementById('stopNowBtn');if(!runBtn||!stopBtn)return;const running=Boolean(man&&man.running&&man.account);const stopping=Boolean(man&&man.cancel_requested);const canOp=Boolean(_authCtx&&_authCtx.can_operate);if(!canOp){runBtn.disabled=true;stopBtn.disabled=true;stopBtn.classList.remove('stop-btn-active');stopBtn.classList.add('stop-btn-locked');stopBtn.setAttribute('aria-disabled','true');stopBtn.textContent='Parar';return;}runBtn.disabled=running;stopBtn.disabled=!running;stopBtn.classList.toggle('stop-btn-active',running);stopBtn.classList.toggle('stop-btn-locked',!running);stopBtn.setAttribute('aria-disabled',(!running)?'true':'false');stopBtn.textContent=stopping?'Parando':'Parar';}
function upd(prefix,state,con){const st=(state&&state.status)||'waiting';const det=(state&&(state.friendly_detail||state.detail))||'Aguardando';const mail=(con&&con.email)||'-';if(prefix==='P'){pill('pillP',st);document.getElementById('mailP').textContent=`E-mail conectado: ${mail}`;document.getElementById('detP').textContent=det;document.getElementById('probP').textContent='';}else{pill('pillN',st);document.getElementById('mailN').textContent=`E-mail conectado: ${mail}`;document.getElementById('detN').textContent=det;document.getElementById('probN').textContent='';}}
function report(r){const t=(r&&r.totals)||{};document.getElementById('kp1').textContent=t.processados||0;document.getElementById('kp2').textContent=t.ignorados||0;document.getElementById('kp3').textContent=t.avisos_ciclo||0;document.getElementById('kp4').textContent=t.avisos_dia||0;if(!r||!r.exists){document.getElementById('rmeta').textContent='Sem relatório encontrado ainda';}else{const w=r.updated_at?new Date(r.updated_at).toLocaleString('pt-BR'):'-';document.getElementById('rmeta').textContent=`Atualizado em: ${w} | Arquivo: ${r.path}`;}fill('lp',r.processados);fill('li',r.ignorados);fill('la',(r.avisos||[]).map(maskXml));}
function _fmtDateTime(v){if(!v)return '-';try{return new Date(v).toLocaleString('pt-BR');}catch(_){return String(v);}}
function _esc(s){return String(s??'').replace(/[&<>\"']/g,(c)=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));}
function _mapDestLabel(item){const v=String(item?.dest_label||'').trim();return v?v:'-';}
function _fmtLocal(local){const s=String(local||'');if(!s)return '-';const parts=s.split('/');if(parts.length>=2){return parts.slice(-2).join('/');}return s;}
function _toggleMenu(ev,btn){ev.stopPropagation();const wrap=btn.closest('.cell-menu');document.querySelectorAll('.cell-menu.open').forEach(x=>{if(x!==wrap)x.classList.remove('open');});wrap.classList.toggle('open');}
async function _showCnpj(ev,btn){ev.stopPropagation();const cnpj=btn.getAttribute('data-cnpj')||'-';try{await navigator.clipboard.writeText(cnpj);showToast(`CNPJ copiado: ${cnpj}`);}catch(_){showToast(`CNPJ emitente: ${cnpj}`);}const wrap=btn.closest('.cell-menu');if(wrap)wrap.classList.remove('open');}
document.addEventListener('click',()=>{document.querySelectorAll('.cell-menu.open').forEach(x=>x.classList.remove('open'));});
function _shortName(txt){
  let s=String(txt||'').replace(/\\(bot\\)/ig,'').trim();
  if(!s)return '-';
  const parts=s.split(/\\s+/).filter(Boolean);
  if(parts.length<=2)return parts.join(' ');
  const shortTok=(v)=>{const c=String(v||'').replace(/[^A-Za-z0-9]/g,'');return c.length>0&&c.length<=2;};
  if(shortTok(parts[0])&&shortTok(parts[1]))return parts.slice(0,3).join(' ');
  return parts.slice(0,2).join(' ');
}
let _histItems=[];
let _histSort={key:'at',dir:'desc'};
function _getSortValue(it,key){if(key==='at')return it.at||'';if(key==='conta')return it.conta||'';if(key==='doc')return it._doc||'';if(key==='fornecedor')return it._fornecedor||'';if(key==='dest')return it._dest||'';if(key==='local')return it._local||'';if(key==='detalhe')return it._detalhe||'';return '';} 
function _sortHist(items){const k=_histSort.key;const dir=_histSort.dir==='asc'?1:-1;return [...items].sort((a,b)=>{const va=_getSortValue(a,k);const vb=_getSortValue(b,k);if(va<vb)return -1*dir;if(va>vb)return 1*dir;return 0;});}
function _renderHistory(items){_histItems=Array.isArray(items)?items:[];const body=document.getElementById('hBody');body.innerHTML='';let arr=_histItems.filter(it=>it.type==='boleto_lancado');arr=arr.map(it=>{const doc=`${it.doc_tipo||'-'} ${it.numero||''}`.trim();const fornec=_shortName(it.fornecedor||'-');const dest=_mapDestLabel(it);const local=_fmtLocal(it.local_lancamento);const detalhe=`Venc: ${it.vencimento||'-'} | ${it.parcela||'-'}`;return {...it,_doc:doc,_fornecedor:fornec,_dest:dest,_local:local,_detalhe:detalhe};});if(!arr.length){body.innerHTML='<tr><td colspan="7">Sem dados para os filtros selecionados</td></tr>';return;}arr=_sortHist(arr);arr.forEach(it=>{const tr=document.createElement('tr');const conta=it.conta||'-';const emit=String(it.cnpj_emit||'-');const menu=`<div class=\"cell-menu\"><button class=\"cell-btn\" onclick=\"_toggleMenu(event,this)\">${_esc(it._fornecedor)}</button><div class=\"cell-pop\"><button data-cnpj=\"${_esc(emit)}\" onclick=\"_showCnpj(event,this)\">Copiar CNPJ emitente</button></div></div>`;tr.innerHTML=`<td>${_fmtDateTime(it.at)}</td><td>${conta}</td><td>${it._doc}</td><td>${menu}</td><td>${it._dest}</td><td>${it._local}</td><td>${it._detalhe}</td>`;body.appendChild(tr);});}
function _setSort(key){const ths=document.querySelectorAll('.hist-table th.sortable');ths.forEach(th=>{th.classList.remove('asc');th.classList.remove('desc');});if(_histSort.key===key){_histSort.dir=_histSort.dir==='asc'?'desc':'asc';}else{_histSort.key=key;_histSort.dir='asc';}const th=document.querySelector(`.hist-table th.sortable[data-key="${key}"]`);if(th)th.classList.add(_histSort.dir);_renderHistory(_histItems);} 
document.querySelectorAll('.hist-table th.sortable').forEach(th=>{th.addEventListener('click',()=>_setSort(th.dataset.key));});
async function loadHistory(silent=false){if(!silent)showToast('Buscando histórico');const p=new URLSearchParams();const vFrom=document.getElementById('hFrom').value||'';const vTo=document.getElementById('hTo').value||'';const vEmit=(document.getElementById('hEmit').value||'').trim();const vDest=(document.getElementById('hDest').value||'').trim();const vQuery=(document.getElementById('hQuery').value||'').trim();const vLimit=Number(document.getElementById('hLimit').value||300);if(vFrom)p.set('from',vFrom);if(vTo)p.set('to',vTo);if(vEmit)p.set('cnpj_emit',vEmit);if(vDest)p.set('cnpj_dest',vDest);if(vQuery)p.set('q',vQuery);p.set('limit',String(Math.max(10,Math.min(2000,vLimit||300))));const {j}=await api(`/api/history?${p.toString()}`);const items=j.items||[];_renderHistory(items);if(!silent)showToast(items.length?`Resultado: ${items.length} registro(s)`:'Nenhum resultado para os filtros selecionados');}
function _fmtAuditAction(v){
  const s=String(v||'').trim().toLowerCase();
  const map={
    configuracao_salvar:'Configurações',
    senha_propria_alterar:'Senha própria',
    senha_usuario_redefinir:'Senha de usuário',
    usuario_criar:'Criar usuário',
    usuario_remover:'Remover usuário',
    reautenticar_gmail:'Reautenticação Gmail',
    reprocessar_emails:'Reprocessar e-mails',
    execucao_manual_iniciar:'Executar agora',
    execucao_manual_parar:'Parar execução',
  };
  return map[s]||String(v||'-');
}
function _fmtAuditStatus(v){const s=String(v||'').toLowerCase();if(s==='ok')return '<span class="audit-status ok">OK</span>';return '<span class="audit-status erro">Erro</span>';}
function _renderAudit(items){const body=document.getElementById('aBody');if(!body)return;body.innerHTML='';const arr=Array.isArray(items)?items:[];if(!arr.length){body.innerHTML='<tr><td colspan="6">Sem dados para os filtros selecionados</td></tr>';return;}arr.forEach(it=>{const tr=document.createElement('tr');tr.innerHTML=`<td>${_fmtDateTime(it.at)}</td><td>${_esc(it.actor||'-')}</td><td>${_esc(_fmtAuditAction(it.action||'-'))}</td><td>${_esc(it.target||'-')}</td><td>${_fmtAuditStatus(it.status||'')}</td><td>${_esc(it.details||'-')}</td>`;body.appendChild(tr);});}
async function loadAudit(silent=false){if(!_authCtx.can_view_audit)return;if(!silent)showToast('Buscando registro de alterações');const p=new URLSearchParams();const vFrom=document.getElementById('aFrom')?.value||'';const vTo=document.getElementById('aTo')?.value||'';const vUser=(document.getElementById('aUser')?.value||'').trim();const vAction=(document.getElementById('aAction')?.value||'').trim();const vQuery=(document.getElementById('aQuery')?.value||'').trim();const vLimit=Number(document.getElementById('aLimit')?.value||300);if(vFrom)p.set('from',vFrom);if(vTo)p.set('to',vTo);if(vUser)p.set('user',vUser);if(vAction)p.set('action',vAction);if(vQuery)p.set('q',vQuery);p.set('limit',String(Math.max(10,Math.min(2000,vLimit||300))));const {j}=await api(`/api/audit?${p.toString()}`);const items=j.items||[];_renderAudit(items);if(!silent)showToast(items.length?`Resultado: ${items.length} registro(s)`:'Nenhum resultado para os filtros selecionados');}
async function state(){const {j}=await api('/api/state');_setAuthUi(j.auth||{});const s=j.settings||{};if(!_cfgDirty&&!_cfgEditingNow()){document.getElementById('mode').value=s.gmail_filter_mode;document.getElementById('maxPages').value=s.gmail_max_pages;document.getElementById('pageSize').value=s.gmail_page_size;document.getElementById('intervalMin').value=s.loop_interval_minutes||30;}document.getElementById('last').value=(j.last_run&&j.last_run.friendly)||(j.last_run&&j.last_run.message)||'-';const rt=j.runtime||{};const a=rt.accounts||{};const sch=rt.scheduler||{};const cd=rt.cooldown||{};const man=j.manual||{};upd('P',a.principal||{},(j.connected||{}).principal||{});upd('N',a.nfe||{},(j.connected||{}).nfe||{});syncManualButtons(man);const left=Number(sch.remaining_seconds||0);const cdLeft=Number(cd.remaining_seconds||0);const cdActive=Boolean(cd.active)&&cdLeft>0;document.getElementById('cool').textContent=cdActive?('Limite da API atingido, nova tentativa em '+fmt(cdLeft)):(left>0?('Próxima verificação automática em '+fmt(left)):'Próxima verificação automática: sem contagem no momento');report(j.report||{});let msg='Nenhum erro recente',k='info';const p=(j.connected||{}).principal||{};const n=(j.connected||{}).nfe||{};if(p.friendly_error||n.friendly_error){msg=p.friendly_error||n.friendly_error;k='warn';}if((a.principal||{}).status==='error'||(a.nfe||{}).status==='error'){msg=(a.principal||{}).friendly_detail||(a.nfe||{}).friendly_detail||msg;k='error';}box(msg,k);}
async function diag(){const {j}=await api('/api/diagnostics');tech.textContent=JSON.stringify(j,null,2);}
async function saveSettings(){const p={gmail_filter_mode:document.getElementById('mode').value,gmail_max_pages:Number(document.getElementById('maxPages').value),gmail_page_size:Number(document.getElementById('pageSize').value),loop_interval_minutes:Number(document.getElementById('intervalMin').value)};await api('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});_cfgDirty=false;await state();await diag();}
async function changeOwnPassword(){if(!_authCtx.can_change_password){showToast('Perfil sem permissão para redefinir senha');return;}const curr=document.getElementById('pwdCurr').value||'';const np=document.getElementById('pwdNew').value||'';const np2=document.getElementById('pwdNew2').value||'';_clearPwdFieldErrors();const r=_updatePwdReqUi();let invalid=false;if(!curr){_markFieldError('pwdCurr',true);invalid=true;}if(!np){_markFieldError('pwdNew',true);invalid=true;}if(!(r.len&&r.low&&r.up&&r.dig&&r.sp)){_markFieldError('pwdNew',true);invalid=true;}if(np!==np2||!np2){_markFieldError('pwdNew2',true);invalid=true;}if(invalid){showToast('Corrija os campos destacados em vermelho');return;}const {j}=await api('/api/auth/change-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({current_password:curr,new_password:np})});if(!j.ok){const m=String(j.message||'Falha ao atualizar senha');if(m.toLowerCase().includes('atual'))_markFieldError('pwdCurr',true);else _markFieldError('pwdNew',true);showToast(m);return;}showToast(j.message||'Senha atualizada');document.getElementById('pwdCurr').value='';document.getElementById('pwdNew').value='';document.getElementById('pwdNew2').value='';_clearPwdFieldErrors();_updatePwdReqUi();await state();}
async function adminCreateUser(){if(!_authCtx.can_manage_users){showToast('Apenas dev');return;}const u=(document.getElementById('newUser').value||'').trim();const p1=document.getElementById('newUserPwd').value||'';const p2=document.getElementById('newUserPwd2').value||'';const role=document.getElementById('newUserRole').value||'user';if(!u||!p1){showToast('Preencha usuário e senha');return;}if(p1!==p2){showToast('Confirmação de senha não confere');return;}if((role||'').toLowerCase()==='dev'){showToast('Não é permitido criar outro usuário dev');return;}const {j}=await api('/api/auth/create-user',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p1,role:role})});showToast(j.message||'Usuário criado');if(!j.ok)return;document.getElementById('newUser').value='';document.getElementById('newUserPwd').value='';document.getElementById('newUserPwd2').value='';await state();}
async function adminDeleteUser(){if(!_authCtx.can_manage_users){showToast('Apenas dev');return;}const u=(document.getElementById('delUser').value||'').trim();if(!u){showToast('Selecione um usuário para remover');return;}const {j}=await api('/api/auth/delete-user',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u})});showToast(j.message||'Usuário removido');await state();}
async function adminResetPassword(){if(!_authCtx.can_manage_users){showToast('Apenas dev');return;}const u=(document.getElementById('rstUser').value||'').trim();const p1=document.getElementById('rstPwd').value||'';const p2=document.getElementById('rstPwd2').value||'';if(!u||!p1){showToast('Selecione usuário e informe a nova senha');return;}if(p1!==p2){showToast('Confirmação de senha não confere');return;}const {j}=await api('/api/auth/reset-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,new_password:p1})});showToast(j.message||'Senha redefinida');if(!j.ok)return;document.getElementById('rstPwd').value='';document.getElementById('rstPwd2').value='';await state();}
async function reprocess(){const p={account:document.getElementById('account').value,days:Number(document.getElementById('days').value),max_messages:Number(document.getElementById('limit').value),mark_unread:document.getElementById('unread').checked};const {j}=await api('/api/reprocess',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});box(j.friendly||'Reprocessamento concluído',j.ok?'info':'error');await diag();await state();}
async function runNow(){const acc=document.getElementById('account').value;syncManualButtons({running:true,account:acc,cancel_requested:false});const p={account:acc};const {j}=await api('/api/run-now',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});if(!j.ok){showToast(j.message||'Não foi possível iniciar a execução manual');syncManualButtons({running:false,account:'',cancel_requested:false});}await state();await diag();}
async function stopRunNow(){syncManualButtons({running:true,account:'manual',cancel_requested:true});const {j}=await api('/api/run-stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});showToast(j.message||'Solicitação de parada enviada');await state();await diag();}
async function countdown(sec){const ov=document.getElementById('ov');const c=document.getElementById('cnt');let n=Number(sec||5);c.textContent=String(n);ov.classList.add('show');await new Promise((res)=>{const t=setInterval(()=>{n-=1;c.textContent=String(Math.max(n,0));if(n<=0){clearInterval(t);res();}},1000);});ov.classList.remove('show');}
async function reauth(a){await countdown(5);const {j}=await api('/api/reauth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account:a})});box(j.friendly||j.message||'Reautenticação concluída',j.ok?'info':'error');await diag();await state();}
document.querySelectorAll('#hFrom,#hTo,#hEmit,#hDest,#hQuery,#hLimit').forEach(el=>{el.addEventListener('keydown',(e)=>{if(e.key==='Enter'){e.preventDefault();loadHistory();}});});
document.querySelectorAll('#aFrom,#aTo,#aUser,#aAction,#aQuery,#aLimit').forEach(el=>{el.addEventListener('keydown',(e)=>{if(e.key==='Enter'){e.preventDefault();loadAudit();}});});
function bindDigitsOnly(id,minV,maxV){
  const el=document.getElementById(id);
  if(!el)return;
  const sanitize=()=>{el.value=String(el.value||'').replace(/\\D+/g,'');};
  const clamp=()=>{sanitize();if(!el.value)return;let n=Number(el.value);if(Number.isNaN(n)){el.value='';return;}if(minV!=null&&n<minV)n=minV;if(maxV!=null&&n>maxV)n=maxV;el.value=String(n);};
  el.addEventListener('input',sanitize);
  el.addEventListener('blur',clamp);
  el.addEventListener('keydown',(e)=>{if(e.ctrlKey||e.metaKey||e.altKey)return;const ok=['Backspace','Delete','Tab','ArrowLeft','ArrowRight','Home','End','Enter'];if(ok.includes(e.key))return;if(!/^\\d$/.test(e.key))e.preventDefault();});
}
bindDigitsOnly('maxPages',1,20);
bindDigitsOnly('pageSize',1,500);
bindDigitsOnly('intervalMin',1,720);
_bindExpanders();
initHubBackButton();
const _pwdNewInput=document.getElementById('pwdNew');
if(_pwdNewInput){_pwdNewInput.addEventListener('input',_updatePwdReqUi);}
_updatePwdReqUi();
let _intervalHintShown=false;
const _intervalInput=document.getElementById('intervalMin');
if(_intervalInput){
  const _showIntervalHint=()=>{if(_intervalHintShown)return;_intervalHintShown=true;showToast('Intervalo em minutos entre cada leitura automática');};
  _intervalInput.addEventListener('focus',_showIntervalHint);
  _intervalInput.addEventListener('keydown',_showIntervalHint);
  _intervalInput.addEventListener('blur',()=>{_intervalHintShown=false;});
}
state();diag();loadHistory(true);loadAudit(true);setInterval(state,2000);setInterval(diag,5000);setInterval(()=>loadHistory(true),15000);setInterval(()=>{if(_authCtx.can_view_audit)loadAudit(true);},15000);
</script></body></html>"""


def _lan_urls(port: int) -> list[str]:
    ips = set()
    try:
        ips.add(socket.gethostbyname(socket.gethostname()))
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    clean = sorted(ip for ip in ips if ip and not ip.startswith("127."))
    return [f"http://{ip}:{port}" for ip in clean]


def start_control_panel(host="127.0.0.1", port=8765, open_browser=False) -> str:
    global _server_started
    _ensure_auth_store()
    with _server_lock:
        if _server_started:
            return f"http://127.0.0.1:{port}" if host == "0.0.0.0" else f"http://{host}:{port}"
        server = ThreadingHTTPServer((host, port), _Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        _server_started = True
    local_url = f"http://127.0.0.1:{port}" if host == "0.0.0.0" else f"http://{host}:{port}"
    print(f"[Painel] Acesso local: {local_url}")
    if host == "0.0.0.0":
        for u in _lan_urls(port):
            print(f"[Painel] Acesso na rede: {u}")
    if open_browser:
        webbrowser.open(local_url)
    return local_url







