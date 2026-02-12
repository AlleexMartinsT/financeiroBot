# FinanceBot

FinanceBot automates XML invoice processing from Gmail and posts payable entries to Google Sheets.

## Core Features

- Reads two Gmail accounts (`principal` and `nfe`)
- Downloads and processes `NF-e` and `CT-e` XML attachments
- Avoids duplicate launches in Sheets
- Handles Braspress-specific logic
- Generates daily reporting and warnings
- Exposes a web control panel with role-based access (`dev`, `admin`, `user`)
- Supports server mode and optional auto-update from Git

## Repository Layout

- Runtime modules: project root (`main.py`, `panel_web.py`, `processor.py`, etc.)
- Scripts: `scripts/`
- Packaging: `packaging/`
- Branding assets: `assets/branding/`
- Technical docs: `docs/`

See `docs/REPOSITORY_STRUCTURE.md` for details.

## Requirements

- Python 3.11+
- Gmail + Google Sheets credentials
- Access to target spreadsheets

## Required Secrets

Create these files under `secrets/`:

- `config_privado.json`
- `credentials.json`
- `credentials_gmail.json`
- `credentials_gmailNFE.json`
- `braspress_config.json` (if Braspress flow is enabled)

## Run (Local)

```bash
python main.py
```

## Run (Server mode, no tray)

```bash
python main.py --server --no-browser
```

Windows shortcut:

```bat
run_server.bat
```

## Deploy from Git (Windows)

```powershell
.\deploy_server.ps1 -RepoUrl "https://github.com/<org>/<repo>.git" -Branch "main" -TargetDir "C:\FinanceBot"
```

## Build EXE

Compatibility command:

```bash
python build_script.py
```

Direct script:

```bash
python scripts/build_release.py
```

## Packaging Files

- PyInstaller spec: `packaging/FinanceBot.spec`
- Inno Setup script: `packaging/FinanceBot.iss`

## Git Workflow

Branch model and contribution process:

- `docs/GIT_WORKFLOW.md`
- `docs/COMMIT_PLAN.md`
- `docs/RELEASE_CHECKLIST.md`

## Important Notes

- No secrets should be committed
- Runtime-generated folders are ignored by Git
- Auto-update requires an actual Git clone on the target machine
