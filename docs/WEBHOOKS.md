# Webhooks

An owner can send every notification event to one URL per workspace (for example an n8n or Zapier webhook):
Settings -> Webhook. Two-step verification is required for this.

## Events

| Event | When |
| --- | --- |
| `post.submitted` | A post was sent for approval |
| `post.approved` / `post.rejected` | A reviewer decided |
| `post.published` | LinkedIn accepted the post |
| `post.failed` | Publishing failed (see `data.error`) |
| `account.expiring` | A LinkedIn connection expires within 7 days |
| `account.reconnect_needed` | A LinkedIn account must be reconnected (`data`: `account_id`, `reason` only); at most once per account per 24 hours |
| `digest.weekly` | Monday morning summary (counts and the strongest post's id, in `data`) |
| `backup.stale` / `backup.failed` | The last backup is 36 hours old or missing, or it failed; at most once per 24 hours (`data`: `reason`, `finished_at`, `age_hours` or `error`) |
| `app.update_available` | A newer Heardloop release was detected (`data`: `current`, `latest`, `url`); once per version per workspace |
| `webhook.test` | "Send test" in Settings |

## Example: n8n

A ready-made workflow that verifies the signature and branches on the event name is in
[examples/n8n/README.md](../examples/n8n/README.md) (Hebrew: [docs/he/n8n.md](he/n8n.md)).

## Request

`POST` with a JSON body:

```json
{
  "id": "4f2c...",
  "event": "post.approved",
  "occurred_at": "2026-10-16T08:00:00+00:00",
  "workspace_id": "...",
  "data": {},
  "post": {
    "id": "...", "status": "approved", "campaign_id": null,
    "scheduled_at": null, "published_at": null, "linkedin_post_urn": null,
    "url": "http://localhost:8380/?section=posts&post=..."
  }
}
```

By default the payload carries ids and statuses only. With "Include the post text" on, it also has the text and
the names of the people and accounts involved:

```json
{
  "event": "post.approved",
  "data": { "actor_name": "Alex Reviewer" },
  "post": { "id": "...", "status": "approved", "text": "...", "...": "..." }
}
```

| Field | Always | Only with "Include the post text" |
| --- | --- | --- |
| `id`, `event`, `occurred_at`, `workspace_id` | yes | |
| `post.id`, `post.status`, `post.campaign_id`, `post.scheduled_at`, `post.published_at`, `post.linkedin_post_urn`, `post.url` | yes | |
| `data.error`, `data.published_last_week` and other counts or codes | yes | |
| `post.text` | | yes |
| `data.comment` (reviewer comment) | | yes |
| `data.actor_name` (who acted) | | yes |
| `data.account_name` (LinkedIn account name) | | yes |

Headers: `X-Heardloop-Event`, `X-Heardloop-Delivery` (stable across retries), `X-Heardloop-Timestamp` (Unix seconds),
`X-Heardloop-Signature`.

## Verify the signature

```python
import hashlib, hmac, time

def verify(secret: str, timestamp: str, raw_body: bytes, signature: str) -> bool:
    if abs(time.time() - int(timestamp)) > 300:  # reject old or replayed calls
        return False
    expected = "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

Use the raw request body, not re-serialized JSON. De-duplicate on `X-Heardloop-Delivery` or `id`.

## Delivery

- Any 2xx response counts as delivered; the response body is ignored. Redirects are not followed.
- 15 seconds per call. Retries after 1, 5, 15 and 60 minutes, then the delivery is marked failed.
- The last 20 deliveries and their status are shown in Settings; history is kept 30 days.
- The worker refuses to call this stack's own services (and the host of `APP_BASE_URL`), loopback, link-local
  (cloud metadata), multicast and every other address that is not publicly routable (for example the shared
  range used by carrier-grade NAT). Private network addresses (e.g. n8n on the same Docker network) are allowed.
  The address that was actually connected is checked again after the connection is made.
