# Roadmap

Planned improvements after v0.11.0, why each one is worth doing, and the order we intend to do them in.
Each item becomes a GitHub issue before work starts (one issue per branch, see [AGENTS.md](../AGENTS.md)).

Revision: 2026-10-31 · Roadmap v1.2 (after v0.12.0) · Classification: Public

| Status | Meaning |
| --- | --- |
| ✅ | Done in this change |
| 🔜 | Next |
| 🗓️ | Planned, later |
| 💤 | Parked: not now, maybe never |

## Rules every item keeps

These come from [SPEC.md](SPEC.md) and the README and do not change:

- Nothing is posted on its own. A person writes, approves and schedules every post.
- No scraping and nothing outside LinkedIn's official API. No likes, comments or messages on anyone's behalf.
- AI stays optional and only suggests. Text goes only to the AI server the owner configured; prefer a local one.
- Data stays on the owner's machine. No telemetry, no "phone home", no SaaS in the middle.
- English and Hebrew (right-to-left) are equal: every new screen, text and report works in both.

## Summary

| # | Item | Area | Effort | Status |
| --- | --- | --- | --- | --- |
| 1 | One-command installers (Linux, macOS, Windows) (#75) | Install | Medium | ✅ |
| 2 | README and install guide fixes (#75) | Docs | Low | ✅ |
| 3 | Reports: empty weekday shows 0.0% (#77) | Reports | Low | ✅ |
| 4 | Reports: show sample size, warn on small samples (#77) | Reports | Low | ✅ |
| 5 | Reports: same metric across weekday, hour and media (#77) | Reports | Low | ✅ |
| 6 | Text cut can split Unicode bold characters (check) (#77) | UI | Low | ✅ |
| 7 | Installer smoke test in CI (#76) | Install | Low | ✅ |
| 8 | Prebuilt images on GHCR (no local build) (#78) | Install | Medium | 🔜 |
| 9 | Format starter pack (English and Hebrew templates) (#79) | Writing | Low | 🔜 |
| 10 | Pre-publish check (#80) | Writing | Low | 🔜 |
| 11 | Voice and context profile (#81) | AI | Medium | 🗓️ |
| 12 | Export and import the profile as a folder (#82) | AI | Low-Medium | 🗓️ |
| 13 | Ask before write (#83) | AI | Medium | 🗓️ |
| 14 | Onboarding wizard (#84) | Setup | Medium | 🗓️ |
| 22 | AI connection check for Ollama (#112) | AI | Medium | 🔜 |
| 23 | External AI providers: OpenRouter, OpenAI, Anthropic (#115) | AI | Medium | 🗓️ |
| 15 | "What worked" loop from reports to writing (#90) | Learn | Medium | 🗓️ |
| 16 | Hook library and variants (#85) | Learn | Medium | 🗓️ |
| 17 | Idea backlog (#86) | Planning | Medium | 🗓️ |
| 18 | Repurpose a strong post (#87) | Writing | Low-Medium | 🗓️ |
| 19 | Posting streak (#88) | Planning | Low-Medium | 🗓️ |
| 20 | Draft-only API for agents (#89) | Integrations | Medium | 🗓️ |
| 21 | Graphics and carousels | Media | High | 💤 |

---

## 1. Install and quick start

### 1.1 One-command installers (#75)
`scripts/install.sh` (Linux, macOS) and `scripts/install.ps1` (Windows 10/11). They install what is missing
(Git, Docker Engine with the Compose and Buildx plugins from Docker's official repository, or Docker Desktop
with WSL 2), clone the newest release tag, create `.env` with random secrets, ask for the LinkedIn app keys
(optional), start the stack, wait for `/api/v1/health` and create the owner account. Optional local Ollama
for the AI assistant (`--with-ai` / `-WithAi`).

Design decisions:

- Safe to run again: an existing clone, `.env` and database are kept. A clone with local changes is not checked out.
- Secrets are never printed and never exported to the shell (Compose prefers shell variables over `.env`).
  `.env` is created with mode 600.
- No `curl | bash` in the docs: download, read, run. Questions read from the terminal, so piping still works.
- Supported automatically: Ubuntu, Debian and their derivatives (apt), Fedora and the RHEL family (dnf), macOS
  with Homebrew, Windows with winget. Anything else gets a clear message and a link to Docker's install page.
- `scripts/new-env.sh` is the Linux/macOS twin of `scripts/new-env.ps1`.

Before release: test on a clean Ubuntu 24.04 VM, a clean Windows 11 VM and macOS; confirm the default Ollama
image tag exists on Docker Hub; commit the scripts with the executable bit
(`git update-index --chmod=+x scripts/install.sh scripts/new-env.sh`).

### 1.2 README and install guide (#75)
Quick start now leads with the installers and shows the manual way for every system (it was Windows-only).
Added the v0.11.0 feature "import a post from its LinkedIn link". Fixed the two "Phase 5" entries. Linked this
roadmap. The install guides (English and Hebrew) point to the installers and to `new-env.sh`.

### 1.3 Installer smoke test in CI (#76)
Done in v0.12.0: `installer-smoke` on Ubuntu (first run + re-run + health) and `installer-ps1-parse` on Windows.
Keeps the installers working as compose files change.

### 1.4 Prebuilt images (#78)
Publish `api`/`worker`/`web`/`backup` images to GitHub Container Registry from the release workflow (signed,
with provenance), and let the compose file use them with `build:` as the fallback. The first install drops from
"a few minutes of building" to a download, which is the biggest single win for new users. Effort: medium.
Keep the local build path for people who want to build from source.

## 2. Reports: issues seen in our own use

Found while reading our own report (30-day window, 4 published posts, two audiences, 30 October 2026).

### 2.1 Empty weekdays show 0.0% (#77)
Done in v0.12.0: buckets with `posts === 0` show a dash and a muted bar; post count stays in the label.

### 2.2 Sample size and small-sample warning (#77)
Done in v0.12.0: each bar shows `n=…`; below 3 posts the bar is marked too few to compare (en + he).

### 2.3 Same metric across charts (#77)
Done in v0.12.0: Engagement rate / Impressions switch for weekday, hour, media and audience charts.

### 2.4 Text cut can split Unicode bold characters (check) (#77)
Done in v0.12.0: `truncateCodePoints` with a unit test; used in Reports, Approvals, Campaigns and LinkCardEditor.

## 3. Writing

### 3.1 Format starter pack (#79)
Twenty to thirty ready templates, in English and in Hebrew, using the existing placeholders: numbered lessons,
good versus bad, before and after, short story with one lesson, checklist, "what I learned from X", question to
the network. Shipped as seed data the owner can import from Templates. Useful on day one and a good thing to
share on its own. Effort: low.

### 3.2 Pre-publish check (#80)
Grows the hashtag counter and the "see more" preview into one checklist shown before submit and before schedule:
hook length before "see more", hashtags (3-5), external link in the body, words from the owner's "never say"
list (3.3), mixed direction problems in Hebrew posts, and numbers in the text that are not in the story bank
(3.3). Warnings only, never blocking. Rules only, no AI needed. Effort: low.

## 4. AI assistant: voice and context

Today the assistant edits the text in front of it and knows nothing about the writer. Most of the value people
get from AI writing setups comes from fixed context files the model reads before every draft: who I am, who I
write for, the stories and numbers that are true, the words I never use. The next items add that layer, kept
local and under the owner's control.

### 4.1 Voice and context profile (#81)
Per workspace (optionally per account): voice (tone, rhythm, habits), audience, story bank (true stories and
numbers), topics, "never say" words, and private notes the assistant must not use. Edited in Settings, stored in
PostgreSQL, sent only to the configured AI server together with the request. Every assistant action reads it.
Effort: medium. Depends on nothing.

### 4.2 Export and import as a folder (#82)
Export the profile as plain Markdown files (`voice.md`, `audience.md`, `story-bank.md`, `rules.md` and a short
`AGENTS.md`) and import them back. People can then use the same context with any coding or writing agent on
their machine and still approve and publish through Heardloop. Effort: low to medium. Depends on 4.1.

### 4.3 Ask before write (#83)
"Draft from an idea" first returns two or three questions for missing facts (a number, a result, a short story)
instead of inventing them. The answers can be saved to the story bank. Effort: medium. Depends on 4.1.

### 4.4 Onboarding wizard (#84)
First-run steps: import the LinkedIn analytics export (exists), answer a short voice questionnaire, paste three
texts the owner wrote himself (not AI-written), review the profile draft, save. Effort: medium. Depends on 4.1.

### 4.5 AI connection check for Ollama (#112)
Classified errors, a Settings status card, and a non-blocking startup check so a bad `AI_BASE_URL` or a missing
model is obvious. Extract `build_request()` so a later provider can plug in without rewriting the client.
Ollama stays the only supported path in that release. Effort: medium. Depends on nothing.

### 4.6 External AI providers: OpenRouter, OpenAI, Anthropic (#115)
Bring-your-own-key cloud models for people without a local server. Ollama stays the default and the recommended
option. OpenRouter first (OpenAI-compatible); then OpenAI and Anthropic with provider-shaped requests via
`build_request()`. Docs must warn that post text goes to that company and usage is billed. Effort: medium.
Depends on 4.5.

## 5. Learn from your own numbers

### 5.1 "What worked" loop (#90)
A short summary per audience and measurement window: top posts, hook traits (number, question, length), media
type, hour. It is shown in Reports and passed to the assistant as evidence. Shown as "worth testing", never as
rules: with a handful of posts per audience, patterns are guesses. Effort: medium. Depends on 2.2 and 4.1.

### 5.2 Hook library and variants (#85)
Keep the opening of every published post with its numbers. A "3 hooks" action suggests openings and shows next
to each one the past post it is based on and how that post did. Effort: medium.

### 5.3 Idea backlog (#86)
A status before Draft ("Idea") with topic and format tags. Ideas can arrive through the existing webhook / n8n
flow (for example from a chat or an e-mail rule) and turn into drafts later. A topics-by-formats grid helps fill
the calendar. Effort: medium.

### 5.4 Repurpose a strong post (#87)
From a published post: a shorter version, a version in the other language (translate exists), the same idea in
another format. Lands as a new draft, so approval still applies. Effort: low to medium.

### 5.5 Posting streak (#88)
Calendar view of weeks with at least one post, the next suggested format, and a line in the weekly digest.
Effort: low to medium.

## 6. Integrations

### 6.1 Draft-only API for agents (#89)
A token scoped to "create draft" only, for n8n, scripts or local agents (optionally exposed as an MCP server).
Approval and publishing stay human and cannot be reached with that token. This makes Heardloop the approval and
publishing layer for any writing setup. Effort: medium.

## 7. Parked
- **Graphics and carousels.** Valuable for reach, far from the core and a lot of work. Revisit after 4 and 5.
- **Counting installs.** No telemetry. We follow adoption through GitHub stars, release downloads and the traffic
  page (visitors, clones, referrers).
- **Auto comments, likes, DMs, scraping.** Against the rules above and LinkedIn's terms. Not planned.
