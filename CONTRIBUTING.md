# Contributing

## Branching

Follow `docs/GIT_WORKFLOW.md`.

Quick rule:

- feature work: `feature/<name>` from `develop`
- production fix: `hotfix/<name>` from `main`

## Pull Request

1. Keep scope focused
2. Do not commit secrets
3. Update docs when behavior or paths change
4. Validate runtime (`python main.py --server --no-browser` when applicable)

## Commit Style

Use clear prefixes:

- `feat:`
- `fix:`
- `refactor:`
- `docs:`
- `chore:`
