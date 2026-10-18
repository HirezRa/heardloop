# Heardloop + n8n

> Hebrew version: [docs/he/n8n.md](../../docs/he/n8n.md)

## What this is for

Heardloop can call a URL of yours every time something happens: a post is waiting for approval, approved or
sent back, published, or failed; a LinkedIn connection is about to expire or needs reconnecting; a backup failed;
the Monday digest is ready. [n8n](https://n8n.io) (self-hosted automation, like Zapier) receives that call and
does whatever you build after it. Heardloop works fine without it; this is for people who want events to reach
other tools.

Ideas:

| When | Do |
| --- | --- |
| `post.failed`, `account.reconnect_needed`, `backup.failed` | Send yourself a Telegram/Slack/e-mail message right away |
| `post.submitted` | Message the approver with a link to the post |
| `post.published` | Add a row to a Google Sheet or Notion database (date, link, campaign) |
| `post.approved` | Move a card on a project board |
| `digest.weekly` | Forward the weekly numbers to a team channel |

The full event list and payload are in [docs/WEBHOOKS.md](../../docs/WEBHOOKS.md).

## What the example workflow does

`heardloop-webhook.json` is a starting point with no outside services:

1. **Heardloop webhook** receives the call.
2. **Build signed string**, **HMAC SHA256**, **Check signature** prove the call really came from your Heardloop
   (anyone who guesses the URL could otherwise send fake events). Old calls (more than 5 minutes) are refused too.
3. **Signature valid?** answers `200` to a genuine call and `401` to anything else (Heardloop retries non-2xx
   calls after 1, 5, 15 and 60 minutes).
4. **Needs attention?** sends `post.failed`, `account.reconnect_needed`, `account.expiring`, `backup.failed` and
   `backup.stale` to **Alert**, everything else to **Log event**. Both only build a message; connect your own
   action (Telegram, e-mail, Sheets...) after them.

Every item after **Check signature** carries everything Heardloop sent:

| Field | Content |
| --- | --- |
| `valid`, `alert` | Signature check result; whether the event needs attention |
| `event`, `delivery`, `timestamp`, `id`, `occurred_at`, `workspace_id` | About the delivery |
| `post_id`, `post_status`, `post_url`, `linkedin_url`, `campaign_id`, `scheduled_at`, `published_at` | Post summary (null for events that are not about a post, for example `webhook.test`) |
| `post_text`, `comment`, `actor_name`, `account_name` | Only when **Include the post text** is on in Heardloop |
| `error` | Failure or reconnect reason code |
| `data`, `post`, `payload` | The original objects, untouched, for anything not listed above (for example the weekly digest counts in `data`) |

**Alert** and **Log event** add a `message` field and keep all of the above.

## Setup

1. **n8n -> Workflows -> Import from File** and choose `heardloop-webhook.json`.
2. **Heardloop -> Settings -> Webhook** (owner, two-step verification on): enter a temporary URL for now, save.
   Heardloop shows the **secret once**. Copy it.
3. In n8n open **HMAC SHA256** and replace `PASTE_WEBHOOK_SECRET_HERE` with the secret. Do not export or share
   the workflow after this; it then contains the secret. (If your n8n has credentials or environment variables
   for secrets, prefer those.)
4. Open **Heardloop webhook**, copy the **Test URL**, click **Listen for test event**.
5. Back in Heardloop, put that URL in the webhook field and save. The URL must be reachable **from the Heardloop
   containers**:
   - n8n on another machine in your network: `http://<its LAN address>:5678/webhook-test/heardloop`.
   - n8n in Docker on the same computer: `http://host.docker.internal:5678/webhook-test/heardloop`.  <!-- privacy-allow -->
   - `localhost` / `127.0.0.1` never work: inside the container they point to Heardloop itself, and Heardloop
     refuses them on purpose.
6. **Send test.** n8n shows one execution ending in **Log event** with `event = webhook.test`; Heardloop's
   delivery list shows **Delivered 200**.
7. Negative check (optional): change one character of the secret in n8n, send a test again: n8n answers **401**,
   Heardloop shows the delivery as pending and retries. Put the correct secret back.
8. When it works: copy the **Production URL**, activate the workflow in n8n, and update the URL in Heardloop.

If the secret was ever exposed (screenshot, chat), use **Rotate secret** in Heardloop and update n8n.

## How the signature works

Heardloop sends `X-Heardloop-Timestamp` and `X-Heardloop-Signature = "sha256=" + HMAC-SHA256(secret,
"<timestamp>.<body>")`. The body is JSON with sorted keys, no spaces, and non-ASCII characters escaped as
`\uXXXX`. n8n hands the workflow parsed JSON, so **Build signed string** rebuilds those exact bytes before
hashing; this works for every Heardloop payload (strings, whole numbers, booleans, null).
