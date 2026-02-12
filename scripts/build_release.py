"""Build FinanceBot executable with PyInstaller and required runtime assets."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "FinanceBot"
MAIN_SCRIPT = "main.py"

BASE_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = BASE_DIR / "dist" / APP_NAME
SECRETS_DIR = BASE_DIR / "secrets"
MS_PLAYWRIGHT_DIR = Path(os.getenv("USERPROFILE", "")) / "AppData" / "Local" / "ms-playwright"

REQUIRED_SECRET_FILES = [
    "config_privado.json",
    "credentials.json",
    "credentials_gmail.json",
    "credentials_gmailNFE.json",
]


def ensure_required_secrets() -> None:
    if not SECRETS_DIR.exists():
        raise FileNotFoundError(f"Missing directory: {SECRETS_DIR}")

    missing = [name for name in REQUIRED_SECRET_FILES if not (SECRETS_DIR / name).exists()]
    if missing:
        pretty = "\n - ".join([""] + missing)
        raise FileNotFoundError(
            "Missing required secrets files:" + pretty +
            f"\nExpected location: {SECRETS_DIR}"
        )


def run_pyinstaller() -> None:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--noconsole",
        "--name",
        APP_NAME,
        "--add-data",
        f"secrets{os.pathsep}secrets",
        "--add-data",
        f"assets{os.pathsep}assets",
        MAIN_SCRIPT,
    ]

    print("[*] Running PyInstaller...")
    subprocess.run(command, check=True, cwd=str(BASE_DIR))


def copy_secrets_runtime() -> None:
    # Copy secrets to both common runtime locations used by packaged apps.
    root_target = DIST_DIR / "secrets"
    internal_target = DIST_DIR / "_internal" / "secrets"

    root_target.parent.mkdir(parents=True, exist_ok=True)
    internal_target.parent.mkdir(parents=True, exist_ok=True)

    shutil.copytree(SECRETS_DIR, root_target, dirs_exist_ok=True)
    shutil.copytree(SECRETS_DIR, internal_target, dirs_exist_ok=True)

    print(f"[+] Secrets copied to: {root_target}")
    print(f"[+] Secrets copied to: {internal_target}")


def copy_playwright_chromium() -> None:
    print("[*] Checking Playwright Chromium...")
    if not MS_PLAYWRIGHT_DIR.exists():
        print("[!] Playwright Chromium not found.")
        print("[!] Run: python -m playwright install chromium")
        return

    print(f"[+] Chromium source: {MS_PLAYWRIGHT_DIR}")
    browsers_dest = DIST_DIR / "_internal" / "playwright" / "driver" / "package" / ".local-browsers"
    browsers_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(MS_PLAYWRIGHT_DIR, browsers_dest, dirs_exist_ok=True)
    print(f"[+] Chromium copied to: {browsers_dest}")

    # Compatibility alias for environments expecting chromium-1187.
    for folder in browsers_dest.glob("chromium-*"):
        expected_folder = browsers_dest / "chromium-1187"
        if not expected_folder.exists():
            shutil.copytree(folder, expected_folder)
            print(f"[+] Created Chromium alias: {expected_folder.name} -> {folder.name}")
        break


def main() -> None:
    print("\n=== Building FinanceBot ===\n")

    ensure_required_secrets()
    run_pyinstaller()
    copy_secrets_runtime()
    copy_playwright_chromium()

    print("\n[+] Build finished successfully.")
    print(f"[+] Output folder: {DIST_DIR}\n")


if __name__ == "__main__":
    main()

