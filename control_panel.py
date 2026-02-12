import json
import threading
import webbrowser
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import auth
from gmail_fetcher import processarEmails
from settings_manager import load_settings, save_settings


_server_started = False
_server_lock = threading.Lock()
_last_run = {"status": "idle", "message": "", "at": None}


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


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


def _run_now(account: str):
    global _last_run
    _last_run = {"status": "running", "message": f"Executando conta {account}...", "at": datetime.now().isoformat()}
    try:
        if account == "principal":
            processarEmails(auth.get_gmail_service("principal"), "Conta Principal")
        else:
            processarEmails(auth.get_gmail_service("nfe"), "Conta NFe")
        _last_run = {"status": "ok", "message": f"Execução da conta {account} concluída.", "at": datetime.now().isoformat()}
    except Exception as e:
        _last_run = {"status": "error", "message": str(e), "at": datetime.now().isoformat()}


def _find_store_image() -> Path | None:
    fname = "Arte MVA logo Metalico (1).png"
    candidates = []

    for p in (
        Path.cwd() / fname,
        Path(__file__).resolve().parent / fname,
        Path(sys.executable).resolve().parent / fname,
    ):
        candidates.append(p)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
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
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")

    raw = p.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "public, max-age=3600")
    handler.end_headers()
    handler.wfile.write(raw)
    return True


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            payload = {"settings": load_settings(), "last_run": _last_run}
            return _json_response(self, 200, payload)

        if parsed.path == "/assets/store-bg":
            if _send_store_image(self):
                return
            self.send_response(404)
            self.end_headers()
            return

        if parsed.path == "/":
            html = _render_html()
            raw = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        data = _read_json(self)

        if parsed.path == "/api/settings":
            saved = save_settings(data)
            return _json_response(self, 200, {"ok": True, "settings": saved})

        if parsed.path == "/api/reprocess":
            account = data.get("account", "principal")
            days = int(data.get("days", 30))
            max_messages = int(data.get("max_messages", 100))
            mark_unread = bool(data.get("mark_unread", True))
            service = auth.get_gmail_service(account)
            result = _reprocess_recent(service, days, max_messages, mark_unread)
            return _json_response(self, 200, {"ok": True, "result": result})

        if parsed.path == "/api/run-now":
            account = data.get("account", "principal")
            t = threading.Thread(target=_run_now, args=(account,), daemon=True)
            t.start()
            return _json_response(self, 200, {"ok": True, "message": "Execução iniciada."})

        if parsed.path == "/api/reauth":
            account = data.get("account", "principal")
            try:
                conta_ok = auth.reautenticarGmail(account)
                return _json_response(self, 200, {"ok": True, "message": f"Reautenticação da conta '{conta_ok}' concluída."})
            except Exception as e:
                return _json_response(self, 400, {"ok": False, "error": str(e)})

        self.send_response(404)
        self.end_headers()


def _render_html() -> str:
    return """<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>FinanceBot - Painel</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Lexend:wght@300;400;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --mva-orange: #da7a1c;
      --mva-orange-2: #ee9b2f;
      --mva-brown: #4a2b18;
      --mva-brown-soft: #6b4128;
      --ink: #2a1b12;
      --line: #e7c8a8;
      --glass: rgba(255, 248, 240, 0.86);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      font-family: 'Lexend', 'Segoe UI', Arial, sans-serif;
      color: var(--ink);
      background:
        linear-gradient(160deg, rgba(41, 22, 11, 0.78), rgba(95, 56, 28, 0.72)),
        url('/assets/store-bg') center / cover no-repeat fixed;
      display: flex;
      justify-content: center;
      align-items: center;
      padding: 20px;
    }

    .app {
      width: min(1100px, 100%);
      background: linear-gradient(180deg, rgba(255, 250, 246, 0.95), rgba(255, 245, 235, 0.9));
      border: 1px solid rgba(231, 200, 168, 0.9);
      border-radius: 18px;
      box-shadow: 0 24px 60px rgba(21, 11, 6, 0.35);
      backdrop-filter: blur(4px);
      overflow: hidden;
    }

    .topbar {
      padding: 18px 22px;
      background: linear-gradient(90deg, var(--mva-brown), var(--mva-orange));
      color: #fff9f3;
      font-weight: 700;
      letter-spacing: 0.4px;
    }

    .content {
      padding: 18px;
      display: grid;
      grid-template-columns: 1fr;
      gap: 14px;
    }

    .card {
      background: var(--glass);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
    }

    h3 { margin: 0 0 10px; color: var(--mva-brown); font-size: 1rem; }

    label { display: block; margin-top: 9px; font-weight: 600; color: #5c341c; }
    .help { font-size: 12px; margin-left: 6px; color: #7a4a2c; cursor: help; }
    .checkline { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .checkline input[type="checkbox"] { width: 18px; height: 18px; margin: 0; }

    input, select {
      width: 100%;
      padding: 10px;
      margin-top: 4px;
      border: 1px solid #d6b18f;
      border-radius: 9px;
      background: #fffdfb;
      color: #2e1c13;
      font-family: inherit;
    }

    .btns { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; }

    button {
      padding: 10px 14px;
      border: 0;
      border-radius: 9px;
      background: linear-gradient(90deg, var(--mva-orange), var(--mva-orange-2));
      color: #2b1408;
      font-weight: 700;
      font-family: inherit;
      cursor: pointer;
    }

    button.secondary {
      background: linear-gradient(90deg, var(--mva-brown), var(--mva-brown-soft));
      color: #fff4ea;
    }

    .row {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }

    pre {
      margin: 0;
      background: #fff7ef;
      border: 1px dashed #cf9f78;
      padding: 10px;
      border-radius: 10px;
      overflow: auto;
      min-height: 130px;
      color: #3a2418;
      font-size: 12px;
      line-height: 1.45;
    }

    .auth-overlay {
      position: fixed;
      inset: 0;
      z-index: 99999;
      display: none;
      align-items: center;
      justify-content: center;
      background: rgba(22, 10, 5, 0.78);
      backdrop-filter: blur(3px);
    }

    .auth-overlay.show { display: flex; }

    .auth-box {
      width: min(440px, 92vw);
      border-radius: 14px;
      border: 1px solid #f0c89d;
      background: linear-gradient(180deg, #fff6ec, #ffe8d4);
      color: #3a2114;
      box-shadow: 0 20px 45px rgba(0,0,0,0.35);
      text-align: center;
      padding: 18px;
    }

    .auth-box h4 {
      margin: 0 0 8px;
      font-size: 1.1rem;
      color: #5a321d;
    }

    .auth-box p {
      margin: 0;
      font-size: 0.95rem;
      line-height: 1.35;
    }

    .auth-count {
      margin-top: 12px;
      font-size: 2.4rem;
      font-weight: 800;
      color: #b05714;
    }

    @media (max-width: 900px) {
      .row { grid-template-columns: 1fr 1fr; }
    }

    @media (max-width: 640px) {
      body { padding: 10px; align-items: stretch; }
      .app { border-radius: 12px; }
      .content { padding: 12px; }
      .row { grid-template-columns: 1fr; }
      .btns { flex-direction: column; }
      button { width: 100%; }
    }
  </style>
</head>
<body>
  <div id="authOverlay" class="auth-overlay" aria-live="polite">
    <div class="auth-box">
      <h4>Reautentica??o em andamento.</h4>
      <p>Troque para a conta correta no navegador.<br/>A autentica??o come?ar? em:</p>
      <div id="authCount" class="auth-count">5</div>
    </div>
  </div>

  <main class="app">
    <div class="topbar">FinanceBot - Painel de Controle MVA.</div>
    <div class="content">
      <section class="card">
        <h3>Configuração do Gmail.</h3>
        <label>Período:<span class="help" title="Define a janela de tempo usada na busca de e-mails com XML.">ⓘ</span></label>
        <select id="mode">
          <option value="last_30_days">Últimos 30 dias.</option>
          <option value="current_and_previous_month">Mês atual + mês anterior.</option>
        </select>
        <div class="row">
          <div>
            <label>Máx. páginas:<span class="help" title="Número de páginas da API Gmail que o sistema vai varrer em cada ciclo.">ⓘ</span></label>
            <input id="maxPages" type="number" min="1" max="20" />
          </div>
          <div>
            <label>Tamanho da página:<span class="help" title="Quantidade de mensagens por página na API Gmail.">ⓘ</span></label>
            <input id="pageSize" type="number" min="1" max="500" />
          </div>
          <div>
            <label>Status da última execução:</label>
            <input id="lastStatus" type="text" readonly />
          </div>
        </div>
        <div class="btns">
          <button onclick="saveSettings()">Salvar configuração.</button>
          <button class="secondary" onclick="reauth('principal')">Refazer autenticação (Principal).</button>
          <button class="secondary" onclick="reauth('nfe')">Refazer autenticação (NFe).</button>
        </div>
      </section>

      <section class="card">
        <h3>Reprocessar e-mails.</h3>
        <div class="row">
          <div>
            <label>Conta:</label>
            <select id="account">
              <option value="principal">Conta Principal</option>
              <option value="nfe">Conta NFe</option>
            </select>
          </div>
          <div>
            <label>Dias para trás:<span class="help" title="Faixa de e-mails para remover labels e permitir novo processamento.">ⓘ</span></label>
            <input id="days" type="number" value="30" min="1" max="365" />
          </div>
          <div>
            <label>Limite de mensagens:<span class="help" title="Quantidade máxima de mensagens alteradas por operação.">ⓘ</span></label>
            <input id="limit" type="number" value="100" min="1" max="1000" />
          </div>
        </div>
        <label class="checkline">
          <span title="Se marcado, o e-mail será marcado como não lido após remover as labels.">Marcar como não lido.</span>
          <input id="unread" type="checkbox" checked />
        </label>
        <div class="btns">
          <button onclick="reprocess()">Remover labels para reprocessar.</button>
          <button class="secondary" onclick="runNow()">Executar agora.</button>
        </div>
      </section>

      <section class="card">
        <h3>Status detalhado.</h3>
        <pre id="out"></pre>
      </section>
    </div>
  </main>

  <script>
    const out = document.getElementById('out');
    function log(x){ out.textContent = JSON.stringify(x, null, 2); }

    async function state(){
      const r = await fetch('/api/state');
      const j = await r.json();
      document.getElementById('mode').value = j.settings.gmail_filter_mode;
      document.getElementById('maxPages').value = j.settings.gmail_max_pages;
      document.getElementById('pageSize').value = j.settings.gmail_page_size;
      document.getElementById('lastStatus').value = `${j.last_run.status || '-'} ${j.last_run.at || ''}`.trim();
      log(j);
    }

    async function saveSettings(){
      const payload = {
        gmail_filter_mode: document.getElementById('mode').value,
        gmail_max_pages: Number(document.getElementById('maxPages').value),
        gmail_page_size: Number(document.getElementById('pageSize').value),
      };
      const r = await fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      log(await r.json());
      await state();
    }

    async function reprocess(){
      const payload = {
        account: document.getElementById('account').value,
        days: Number(document.getElementById('days').value),
        max_messages: Number(document.getElementById('limit').value),
        mark_unread: document.getElementById('unread').checked,
      };
      const r = await fetch('/api/reprocess', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      log(await r.json());
    }

    async function runNow(){
      const payload = { account: document.getElementById('account').value };
      const r = await fetch('/api/run-now', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      log(await r.json());
    }

    async function startAuthCountdown(seconds){
      const overlay = document.getElementById('authOverlay');
      const count = document.getElementById('authCount');
      let current = Number(seconds || 5);
      count.textContent = String(current);
      overlay.classList.add('show');

      await new Promise((resolve) => {
        const timer = setInterval(() => {
          current -= 1;
          count.textContent = String(Math.max(current, 0));
          if (current <= 0) {
            clearInterval(timer);
            resolve();
          }
        }, 1000);
      });

      overlay.classList.remove('show');
    }

    async function reauth(account){
      await startAuthCountdown(5);
      const payload = { account };
      const r = await fetch('/api/reauth', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      log(await r.json());
      await state();
    }

    state();
    setInterval(state, 5000);
  </script>
</body>
</html>"""


def start_control_panel(host="127.0.0.1", port=8765, open_browser=False) -> str:
    global _server_started
    with _server_lock:
        if _server_started:
            return f"http://{host}:{port}"
        server = ThreadingHTTPServer((host, port), _Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        _server_started = True

    url = f"http://{host}:{port}"
    if open_browser:
        webbrowser.open(url)
    return url
