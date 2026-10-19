# Changelog

All notable changes to Heardloop are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

