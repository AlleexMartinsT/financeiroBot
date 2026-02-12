# Repository Structure

This repository is organized for operational clarity and predictable deployments.

## Top-level

- `main.py`: application entrypoint.
- `panel_web.py`: web control panel.
- `processor.py`, `gmail_fetcher.py`, `auth.py`, etc.: core runtime modules.
- `requirements.txt`: Python dependencies.
- `README.md`: operational and deployment guide.

## Folders

- `scripts/`
  - `build_release.py`: build automation for PyInstaller release.
  - `run_server.bat`: server-mode launcher.
  - `deploy_server.ps1`: deployment/update helper for Windows servers.
- `packaging/`
  - `FinanceBot.spec`: PyInstaller spec file.
  - `FinanceBot.iss`: Inno Setup installer script.
- `assets/branding/`
  - branding assets used by the UI.
- `docs/`
  - project standards and Git workflow documentation.

## Compatibility Wrappers

To avoid breaking existing operational commands, wrapper files remain at root:

- `run_server.bat` -> delegates to `scripts/run_server.bat`
- `deploy_server.ps1` -> delegates to `scripts/deploy_server.ps1`
- `build_script.py` -> delegates to `scripts/build_release.py`
