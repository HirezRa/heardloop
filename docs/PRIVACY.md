# Privacy notice (template)

> **Template for the person or organisation that runs this Heardloop instance.** Heardloop is self-hosted: the
> operator, not the Heardloop project, decides what is collected and where it goes. Replace every `[fill in]`,
> remove what does not apply, and have it reviewed before you publish it. This is not legal advice.
>
> Hebrew version: [docs/he/PRIVACY.md](he/PRIVACY.md)

Last updated: [fill in date]

## Who runs this service

[fill in: name of the person or organisation], contact for privacy questions: [fill in e-mail].

## What is stored, and why

| Data | Why | Where | How long |
| --- | --- | --- | --- |
| E-mail address, display name, role in each workspace | Sign-in, showing who wrote or approved a post | Database | Until the account is deleted or anonymised by an owner |
| Password (as an Argon2id hash only) and two-step secret (encrypted) | Sign-in security | Database | As above |
| Sessions: browser user agent and IP address | Keeping you signed in; showing your active devices | Database | Until sign-out or expiry (12 hours idle, 7 days at most by default) |
| Activity log: who did what and when, with the IP address | Accountability for approvals, publishing and settings | Database | Entries until an owner purges them; IP addresses cleared after `AUDIT_IP_RETENTION_DAYS` (default 90 days) |
| LinkedIn connection: profile name and id, access token (AES-256-GCM encrypted) | Publishing the posts you approve | Database | Until the account is disconnected |
| Posts, versions, comments, images and videos you upload | The product itself | Database and media folder | Until deleted |
| Post metrics you enter or import (numbers only; the uploaded file is not kept) | Reports | Database | Until deleted |
| Notifications and interface preferences | The app itself | Database (preferences), browser storage for one report setting | Until deleted |
| Backups of all of the above | Restore after a failure | `BACKUP_DIR` [fill in location; encrypted: yes/no] | `BACKUP_KEEP_DAYS` (default 30 days) |

Cookies: one session cookie (`__Host-hl_session`, strictly necessary) and one short-lived cookie during the
LinkedIn connection (`hl_oauth`). No analytics, advertising or third-party cookies, scripts or fonts.

## Where data is sent

| Recipient | What | When |
| --- | --- | --- |
| LinkedIn (official API) | Post text and media, your LinkedIn token | When you connect an account and when an approved post is published |
| GitHub (releases API) | The server's IP address; no user data | Daily update check; off with `UPDATE_CHECK=false` |
| E-mail server [fill in provider, or "none"] | Your e-mail address and the notification text | Only if e-mail is set up and you keep e-mail notifications on |
| Webhook [fill in destination, or "none"] | Event, post id and status; the post text only if an owner turned on "Include the post text" | Only if an owner set up a webhook |
| AI server [fill in host and provider, or "none"] | The text you select and ask the assistant to work on | Only when you click an assistant action; the editor shows the destination |
| Web pages you link in a post | The server's IP address (fetching the title and image for the link card) | When you add a link card |

[fill in: hosting location of the server and its backups]

## Your rights

You can ask [fill in contact] to see, correct or delete your data. Owners can anonymise a member from
Settings -> Workspace; this removes the name, e-mail address and IP addresses and disconnects their LinkedIn
accounts, while keeping the history of the posts.

## Changes

[fill in: how you tell people about changes to this notice]
