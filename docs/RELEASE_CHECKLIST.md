# Release Checklist

Use this checklist before publishing a new version.

## 1. Code and Branch Preparation

- [ ] `main` is stable and up to date
- [ ] release branch created (example: `release/1.0.1`)
- [ ] no unintended local changes (`git status`)
- [ ] no secrets tracked in Git

## 2. Quality Gate

- [ ] Python compilation check:
  - `python -m py_compile main.py panel_web.py`
- [ ] Core runtime flow validated:
  - login
  - role behavior (`dev/admin/user`)
  - panel tabs and actions
  - manual run / stop behavior
- [ ] Registro tab visible only to `dev`
- [ ] Histórico and Diagnóstico load correctly

## 3. Build and Packaging

- [ ] Build generated successfully:
  - `python build_script.py`
  - or `python scripts/build_release.py`
- [ ] Build output exists:
  - `dist/FinanceBot/FinanceBot.exe`
- [ ] Packaging files are aligned:
  - `packaging/FinanceBot.spec`
  - `packaging/FinanceBot.iss`
- [ ] Branding assets included:
  - `assets/branding/Arte MVA logo Metalico (1).png`

## 4. Server Validation

- [ ] Server mode starts:
  - `python main.py --server --no-browser`
- [ ] Panel reachable from LAN (if host is `0.0.0.0`)
- [ ] Settings load/save works in production environment
- [ ] Authentication and token refresh flow validated

## 5. Git Release

- [ ] commits follow clear convention (`feat`, `fix`, `chore`, `docs`)
- [ ] PR merged to `main`
- [ ] tag created (example: `v1.0.1`)
- [ ] release notes added

## 6. Post-Release

- [ ] deploy command executed on server:
  - `.\deploy_server.ps1 -RepoUrl "<repo>" -Branch "main" -TargetDir "C:\FinanceBot"`
- [ ] smoke test completed on target machine
- [ ] logs/diagnostics checked after first cycle
