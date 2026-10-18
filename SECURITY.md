# Security

This is a personal project. If you find a security issue, please do not open a public issue;
use GitHub's private vulnerability reporting on this repository instead.

Ground rules in this repo:
- No secrets, personal data, IP addresses, host names or local paths in commits.
- Secrets come from environment variables only (`.env`, git-ignored).
- LinkedIn tokens are encrypted at rest (AES-256-GCM, record-bound associated data).
- Only the official LinkedIn API is used. No scraping.
