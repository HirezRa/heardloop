# Instructions for AI coding agents

Read `docs/SPEC.md` before changing code. Personal project.

## Language
- Everything committed is English: code, comments, docs, commit messages, PR and issue text.
- Exceptions: UI copy in `src/frontend/src/i18n.ts` and the Hebrew user documentation in `docs/he/`.
- Enforced by `scripts/check_english.py` (pre-commit and CI).

## Privacy - hard rules
- Never commit: secrets, tokens, passwords, `.env`, IP addresses, host/server names, local paths
  (drive-letter or home-folder paths), real names or e-mails, container/infra details of the owner's machines,
  or anything from `docs/reports/` (local only).
- Do not name the editors, assistants or tools used to develop this project anywhere in committed files,
  commit messages, PR or issue text. No `Co-authored-by` or similar attribution trailers. Editor/assistant
  settings folders and task prompts stay local (excluded via `.git/info/exclude`).
- The commit-msg hook runs the same privacy and English checks on every commit message.
- Personal compose tweaks go in `docker-compose.override.yml` (git-ignored).
- Git author e-mail must be the GitHub noreply address.
- Before every commit: `pre-commit run --all-files` must pass (gitleaks + `scripts/check_private.py`).
- In reports and chat output, write secret values as `<redacted>`.
- Never run commands that print credentials: `gh auth status -t` / `--show-token`, `gh auth token`,
  `git config --list --show-origin` on credential helpers, `cat .env`, `printenv`/`Get-ChildItem Env:` unfiltered.
  Check auth with plain `gh auth status`.
- Never set `HEARDLOOP_TOKEN_KEY` or `POSTGRES_PASSWORD` as variables in a shell that later runs `docker compose`:
  Compose gives shell variables priority over `.env`, so the stack would start with the wrong key and everything
  encrypted becomes unreadable. If a test needs them, set them only inside a child process, e.g.
  `powershell -NoProfile -Command { $env:HEARDLOOP_TOKEN_KEY = '...'; uv run alembic check }`.
  The API and worker refuse to start when the key does not match the database (`heardloop/keycheck.py`).

## Code
- Validate all input server-side (Pydantic models). SQLAlchemy only - no SQL string building with user input.
- Check workspace membership + role on every endpoint (no IDOR).
- Change post status only via `Post.transition_to` / `domain/workflow.py`
  (the worker's locked claim in `worker/dispatcher.py` is the one documented exception).
- Time: `heardloop.timeutil.utcnow()` - never naive datetimes.
- Logs: structured JSON with ids; never tokens, passwords, Authorization headers or full post bodies.
- Tests for domain logic you touch. `uv run pytest` and `uv run ruff check .` must pass.
- New dependency: say why in the commit message, pin the exact version.

## Must not
- Scrape LinkedIn or automate anything outside the official API.
- `git push --force`, rewrite published history, delete Docker volumes, or drop the DB without the owner's OK.
- Disable security headers, auth or CSRF "temporarily".

## Workflow
- One GitHub issue per branch: `feat/<issue-number>-<slug>` (or `fix/` / `chore/` as appropriate).
- Open a PR whose body includes `Closes #<n>`.
- CI must be green (`privacy`, `gitleaks`, `backend`, `backup-smoke`, `frontend`, `stack-smoke`) before squash-merge to `main`.
- Prefer Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, …).
- Do not push secrets, `.env`, or anything under `docs/reports/` (local only).

## Local URLs
- Use `http://localhost:8380` (never `127.0.0.1`) in instructions to the owner: LinkedIn redirects back to
  `localhost`, and cookies are per host name.

## Commands
- Backend: `cd src/backend && uv sync && uv run ruff check . && uv run pytest`
- Migration: `uv run alembic revision --autogenerate -m "<change>"` then review the file
- Frontend: `cd src/frontend && npm ci && npm run build`
- Stack: `docker compose up -d --build`; logs: `docker compose logs -f api worker`
- Task reports go to `docs/reports/` (local only, git-ignored).
