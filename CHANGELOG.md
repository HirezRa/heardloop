# Changelog

All notable changes to Heardloop are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Backend: asyncpg 0.31.0 → 0.32.0 (#103).
- Backend: FastAPI 0.143.2 → 0.162.0 (#104).

## [0.12.1] - 2026-11-01

### Changed

- Dependabot: `cooldown.default-days: 7` on every updates entry (#100).
- Frontend nginx base image bump (#71).
- CI: `astral-sh/setup-uv` bump (#72).
- Frontend: Vite bump (#73).
- Backend patches: SQLAlchemy 2.1.6 and Alembic 1.20.1 (#101).

## [0.12.0] - 2026-10-31

### Added

- One-command installers: `scripts/install.sh` (Linux, macOS) and `scripts/install.ps1` (Windows). They install Git and Docker when missing, clone the newest release, create `.env` with random secrets, ask for the LinkedIn app keys, start the stack, wait until it is healthy and create the owner account. Optional local Ollama for the AI assistant (`--with-ai` / `-WithAi`). Safe to run again.
- `scripts/new-env.sh`, the Linux/macOS twin of `scripts/new-env.ps1`.
- CI installer smoke: `installer-smoke` runs `install.sh` twice on Ubuntu and checks `/api/v1/health`; `installer-ps1-parse` parse-checks `install.ps1` under Windows PowerShell 5.1 and PowerShell 7.
- `docs/ROADMAP.md`: planned improvements, known report issues and their order.

### Changed

- README quick start leads with the installers and shows the manual steps for every system; the feature list includes importing a post from its LinkedIn link; the roadmap section no longer lists two "Phase 5" entries.
- The install guides (English and Hebrew) point to the installers and to `scripts/new-env.sh`.
- Reports weekday, hour, media and audience bars share one Engagement rate / Impressions switch (remembered in the browser). Empty buckets show a dash; bars with fewer than 3 posts are marked as too few to compare.

### Fixed

- Report bars no longer show `0.0%` for weekdays (and other buckets) with no posts.
- Snippet cuts in Reports, Approvals, Campaigns and link-card titles no longer split a Unicode bold/italic letter in half.

## [0.11.0] - 2026-10-24

### Added

- Import from LinkedIn (#66): bring in a post you published directly on LinkedIn from its address plus its analytics export (Posts -> Import from LinkedIn). The file must belong to the post in the address; since only a post's author can export its analytics, only your own posts come in. Choose account, campaign and audience in the same step. Nothing is fetched from LinkedIn and the file is not kept.

### Fixed

- Imported posts whose LinkedIn id is a `ugcPost` were saved as `share`, so "Open on LinkedIn" could lead nowhere. New imports keep the right kind, and re-importing an older post corrects it.

## [0.10.0] - 2026-10-23

### Added

- Audiences (#59): the markets you write for, each with its own time zone and weekend (Settings -> Workspace, with presets). A post uses its own audience, or its campaign's, or none. Owners manage them; members can read them. Deleting one that posts or campaigns still use is refused.
- Reports and best-time suggestions follow the audience (#59). Charts and suggestions use that audience's clock and weekend, and audiences are shown side by side instead of averaged. The CSV export adds `audience`, `time_zone`, `published_local`, `weekend` and `measured_age_days`.
- Measurement window (#59): each post is represented by the measurement closest to a chosen age (7, 14 or 30 days, or the latest). Posts younger than the window are marked preliminary. A measurement taken much earlier or later is marked far from the window.
- Optional AI writing assistant in the post editor (#11): improve, shorten, stronger opening, fix spelling, suggest hashtags, translate to English or Hebrew, optional tone. Works with any OpenAI-compatible server; off unless `AI_BASE_URL` and `AI_MODEL` are set. Suggestions only: nothing changes until you replace or insert, and nothing is stored.
- Best-time suggestions next to the scheduling field (#11), from your own metrics: the weekday and hour with the best engagement rate.
- Signed-in devices (#55): see where you are signed in and sign out one device or all the others (Settings -> Sign-in & security).
- `AUDIT_IP_RETENTION_DAYS` (#55): optional age-based clearing of IP addresses in the activity log. The entries stay.
- Update scripts: optional signed-tag check (`-VerifyTag` / `--verify-tag` / `HEARDLOOP_VERIFY_TAG=1`) (#55).
- Webhook settings warn about plain `http://` to a public address (#55).
- Base images pinned by digest (Dependabot keeps them current) (#55).

### Changed

- Reports default to the 7-day measurement window instead of the latest measurement (#59). Choose "Latest measurement" for the previous view, so numbers can differ from v0.9.x.
- Weekday and hour charts are split per audience, each titled with its time zone (#59). A toggle switches to the workspace clock (the hour the post went out here).
- Best-time suggestions are per audience and in that audience's time zone; choosing one fills the picker in the workspace time zone (#59).
- CI runners are pinned to Ubuntu 24.04, and the browser install in CI has a timeout (#61).

### Security

- Signed-in devices (#55): list your own sessions and sign out one or all others (Settings -> Sign-in & security).
- Notices to the person when the password changes, two-step verification is turned on or off, recovery codes are regenerated, or devices are signed out (in-app, and e-mail when on).
- Common or easily guessed passwords are refused (bundled offline list plus padding, repetition and sequence checks).
- New recovery codes have 80 bits (`xxxx-xxxx-xxxx-xxxx`); existing codes keep working.
- Password change and the two-step setup/disable/recovery-code endpoints return 423 while the account is locked.
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
- Webhooks and link previews pin the vetted IP address (#55): the name is resolved and checked at connect time and only a checked address is dialled, so DNS rebinding cannot make the server call a private or internal address.

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

