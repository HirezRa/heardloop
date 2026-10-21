# Changelog

All notable changes to Heardloop are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- Follow-up review against OWASP ASVS 4.0.3 L1-2 (#14).
- Activity log: sign-in events (and their IP addresses) are shown and purged only for members whose only workspace this is, so adding someone from another workspace no longer exposes or erases their sign-in history.
- Manual retry of a failed post (`schedule` / `publish-now`) needs an approved version when approval is required.
- Analytics import: cells beyond column XFD and sheets over 200 000 expanded cells are refused; DOCTYPE/entity declarations are refused by the XML parser in any encoding (UTF-16 no longer slips past).
- Anonymising a member also clears the IP addresses in their activity-log rows.
- Reviewer comments refuse control characters (line feeds allowed).
- Public `/api/v1/meta` no longer returns the version; `/version` decides "owner" correctly for people in several workspaces.
- Media responses: `Cache-Control: private, no-cache`.
- `scripts/backup-now.sh` only does arithmetic on numeric manifest values.
- Containers: `db` gets `no-new-privileges` and a PID limit, `web` a read-only root; nginx sends `Permissions-Policy` and logs paths without query strings.
- CI: release checkout does not keep credentials; Dependabot also watches `src/backup` and the compose file images.

## [0.9.6] - 2026-10-19

### Fixed

- Update check: `POST /version/check` no longer 500s (`MissingGreenlet` after rollback); About shows honest states (`newer` / `current` / `error` / `never` / `disabled`) and never claims "latest" after a failed check; banner still uses the last successful newer release.
- Optional `EXTRA_CA_CERT_FILE` for TLS-inspecting networks (update check only).

### Changed

- New workspaces default to time zone `UTC`; `create_owner` accepts optional `--time-zone` (IANA; otherwise host local if detectable, else UTC).

### Documentation

- Docs review: Python 3.14 badges/tables, README gallery and documentation map, GUIDE screenshot embeds, INSTALL LinkedIn Page wording (en/he), GitHub issue/PR templates, asset and markdown-link CI checks.
- INSTALL troubleshooting (en/he): organisation CA mount for the update check.

## [0.9.5] - 2026-10-19

### Documentation

- Fresh README / GUIDE screenshots from the demo stack (normal text size, English, light theme).
- Demo seed enrolls owner TOTP with a fixed development secret; screenshot script dismisses the update banner after `update-banner.png`.

## [0.9.4] - 2026-10-19

### Fixed

- `react-dom` matches `react` (19.3.1); CI checks the pair and smokes the login form so a blank page cannot ship again.
- README status matches what is shipped (metrics/reports/backups/templates done; optional AI open; company pages later).

### Changed

- Backend image Python `3.13-slim` → `3.14-slim` (`requires-python` allows 3.14; image venv uses system Python).
- Frontend nginx image `nginxinc/nginx-unprivileged` `1.28-alpine` → `1.31-alpine`.
- Dependabot: one weekly minor/patch PR per ecosystem; `react` / `react-dom` / `@types/react*` always move together; majors stay separate.

### Documentation

- Note on LinkedIn's API Terms of Use (section 3.1) and scheduled publishing (README, INSTALL en/he, GUIDE, Accounts).
- CI full-stack smoke: build real images, health through the web port, login page in Chromium.

## [0.9.3] - 2026-10-19

### Fixed

- Text size setting now applies to form controls; meta text uses a readable scale (nothing below 0.8125rem); "very large" is 21px; chart labels match page text; About shows one consistent version and hides unknown build SHAs.

## [0.9.2] - 2026-10-19

### Fixed

- `scripts/update.ps1` / `update.sh` compare the **running** API version (image label, then
  `app_version()`), not only `VERSION` on disk, so a checkout that is ahead of the containers still
  rebuilds. Backend images carry `org.opencontainers.image.version` / `.revision` labels.

## [0.9.1] - 2026-10-18

### Added

- Dev-only `HEARDLOOP_VERSION_OVERRIDE` (when `APP_ENV=development`) for throwaway update-check tests.

### Fixed

- `scripts/update.ps1` accepts `-DryRun` (alias of `-WhatIf`) and prints `already up to date` when current equals target.
- About tab copy: "You are on the latest version (vX.Y.Z)" after a successful check with nothing newer.

## [0.9.0] - 2026-10-18

### Added

- Installation guides in English and Hebrew, plus an n8n example workflow for webhooks.
- Application version from a single `VERSION` file, shown in the sidebar and Settings -> About.
- Optional notice when a newer GitHub release is available (in-app, webhook, e-mail); updates are
  installed on the host with `scripts/update.ps1` / `scripts/update.sh`, never from the web UI.
- GitHub Release workflow on `v*.*.*` tags, driven by this changelog.
- Post templates and version history (restore a previous draft).
- Scheduled backups with verified restore, and a Backups status card for owners.
- Clearer publish failures and a reconnect prompt when a LinkedIn account needs attention.
- Manual LinkedIn analytics import, reports, CSV export and weekly digest.
- Security hardening across sessions, CSRF, webhooks and input validation.

### Changed

- Settings gains an About tab with documentation links and an owner-only update check.

