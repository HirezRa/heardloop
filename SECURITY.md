# Security

This is a personal project. If you find a security issue, please do not open a public issue;
use GitHub's private vulnerability reporting on this repository instead.

**Supported versions:** the latest release only.

**Response:** best effort — this is a personal project, not a commercial SLA.

Ground rules in this repo:
- No secrets, personal data, IP addresses, host names or local paths in commits.
- Secrets come from environment variables only (`.env`, git-ignored).
- LinkedIn tokens are encrypted at rest (AES-256-GCM, record-bound associated data).
- Only the official LinkedIn API is used. No scraping.

Repository social preview image: `docs/assets/social-preview.png` (upload under Settings → General → Social preview).
