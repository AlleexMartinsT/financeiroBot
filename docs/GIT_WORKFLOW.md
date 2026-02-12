# Git Workflow

This project uses a lightweight formal branch model.

## Branches

- `main`
  - production-ready code only
  - protected branch (recommended)
- `develop`
  - integration branch for ongoing work
- `feature/<short-name>`
  - isolated feature development
- `release/<version>`
  - stabilization before merge to `main`
- `hotfix/<short-name>`
  - urgent production fixes from `main`

## Standard Flow

1. Create feature branch from `develop`.
2. Commit logically grouped changes with clear messages.
3. Open PR to `develop`.
4. After validation, merge using squash or rebase strategy.
5. For release, create `release/<version>` from `develop`.
6. Merge release to `main` and back to `develop`.

## Commit Message Convention

Recommended format:

- `feat: ...`
- `fix: ...`
- `refactor: ...`
- `docs: ...`
- `chore: ...`

Example:

- `feat: add audit log tab visible only to dev users`
- `fix: hide Registro tab for non-dev profiles`

## Pull Request Checklist

- scope is clear and limited
- no secrets committed
- runtime behavior validated
- docs updated when paths or ops changed
