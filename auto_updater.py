import shutil
import subprocess
import threading
import time
from pathlib import Path


class AutoUpdater:
    """
    Atualiza o codigo automaticamente a partir do remoto Git.
    - So roda quando existe .git no projeto e o comando git esta disponivel.
    - Quando detecta e aplica update (ff-only), sinaliza reinicio do app.
    """

    def __init__(
        self,
        repo_dir: str | Path,
        enabled: bool = True,
        interval_minutes: int = 5,
        remote: str = "origin",
        branch: str = "main",
    ):
        self.repo_dir = Path(repo_dir).resolve()
        self.enabled = bool(enabled)
        self.interval_minutes = max(1, int(interval_minutes))
        self.remote = (remote or "origin").strip()
        self.branch = (branch or "main").strip()
        self._stop = threading.Event()
        self._restart_requested = threading.Event()
        self._thread = None
        self._checked_env = False
        self._available = False

    def _check_env(self) -> bool:
        if self._checked_env:
            return self._available
        self._checked_env = True
        if not self.enabled:
            self._available = False
            return False
        if shutil.which("git") is None:
            print("[Updater] Git nao encontrado. Atualizacao automatica desativada.")
            self._available = False
            return False
        if not (self.repo_dir / ".git").exists():
            print("[Updater] Repositorio Git nao encontrado. Atualizacao automatica desativada.")
            self._available = False
            return False
        self._available = True
        return True

    def _run_git(self, *args: str) -> tuple[int, str]:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(self.repo_dir),
                text=True,
                capture_output=True,
                check=False,
            )
            out = (proc.stdout or proc.stderr or "").strip()
            return proc.returncode, out
        except Exception as e:
            return 1, str(e)

    def _head(self) -> str:
        code, out = self._run_git("rev-parse", "HEAD")
        return out if code == 0 else ""

    def _remote_head(self) -> str:
        ref = f"{self.remote}/{self.branch}"
        code, out = self._run_git("rev-parse", ref)
        return out if code == 0 else ""

    def _update_once(self):
        code, out = self._run_git("fetch", self.remote, self.branch)
        if code != 0:
            if out:
                print(f"[Updater] Falha no fetch: {out}")
            return

        local_head = self._head()
        remote_head = self._remote_head()
        if not local_head or not remote_head:
            return
        if local_head == remote_head:
            return

        print(f"[Updater] Nova versao detectada ({local_head[:7]} -> {remote_head[:7]}). Aplicando...")
        code, out = self._run_git("pull", "--ff-only", self.remote, self.branch)
        if code != 0:
            if out:
                print(f"[Updater] Falha no pull: {out}")
            return

        new_head = self._head()
        if new_head and new_head != local_head:
            print(f"[Updater] Atualizacao aplicada para {new_head[:7]}. Reinicio solicitado.")
            self._restart_requested.set()

    def _loop(self):
        print(
            "[Updater] Ativo: "
            f"repo={self.repo_dir} remote={self.remote} branch={self.branch} "
            f"intervalo={self.interval_minutes}min"
        )
        while not self._stop.is_set():
            for _ in range(self.interval_minutes * 60):
                if self._stop.is_set():
                    return
                time.sleep(1)
            if self._stop.is_set():
                return
            self._update_once()

    def start(self):
        if not self._check_env():
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def consume_restart_request(self) -> bool:
        if self._restart_requested.is_set():
            self._restart_requested.clear()
            return True
        return False

