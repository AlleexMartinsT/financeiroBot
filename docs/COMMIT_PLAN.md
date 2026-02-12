# Commit Plan

This file proposes an atomic commit sequence to keep history clean and reviewable.

## Suggested Commit Order

1. `chore(repo): standardize repository metadata and ignore rules`
- `.gitignore`
- `.editorconfig`
- `.gitattributes`
- `CONTRIBUTING.md`

2. `chore(structure): reorganize scripts, packaging and branding assets`
- `scripts/build_release.py`
- `scripts/run_server.bat`
- `scripts/deploy_server.ps1`
- `packaging/FinanceBot.spec`
- `packaging/FinanceBot.iss`
- `assets/branding/Arte MVA logo Metalico (1).png`
- root wrappers:
  - `build_script.py`
  - `run_server.bat`
  - `deploy_server.ps1`

3. `fix(panel): harden role visibility and simplify Registro table`
- `panel_web.py`
- `audit_store.py` (if not committed yet)

4. `docs: refresh operational and workflow documentation`
- `README.md`
- `docs/REPOSITORY_STRUCTURE.md`
- `docs/GIT_WORKFLOW.md`
- `docs/RELEASE_CHECKLIST.md`
- `docs/COMMIT_PLAN.md`

## Optional Commit (if desired)

5. `chore(git): add local branch model references`
- no file changes required
- create branches locally:
  - `develop`
  - `release/1.0.0`
  - `hotfix/template`

## Notes

- Keep unrelated runtime code changes outside this sequence where possible.
- If current working tree is already mixed, use the order above while selecting files per commit.
