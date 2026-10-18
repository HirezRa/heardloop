import type { UiPrefs } from './prefs';

export type HealthState = 'checking' | 'healthy' | 'unhealthy' | 'offline';
export type Role = 'owner' | 'editor' | 'approver' | 'viewer';

export interface Me {
  user: { id: string; email: string; display_name: string; locale: string };
  memberships: { workspace_id: string; workspace_name: string; role: Role }[];
  csrf_token: string;
  mfa_enabled: boolean;
  ui_prefs: UiPrefs;
}

export interface MfaChallenge {
  mfa_required: true;
  csrf_token: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    /** Structured error body when the server sends `detail` as an object ({ code, ... }). */
    public info?: Record<string, unknown>,
  ) {
    super(`${status} ${detail}`);
  }
}

/** `detail` is a code string, or an object with a `code` plus context (e.g. post_not_found). */
function detailOf(data: { detail?: unknown }, fallback: string): { detail: string; info?: Record<string, unknown> } {
  const d = data.detail;
  if (typeof d === 'string') return { detail: d };
  if (d && typeof d === 'object' && typeof (d as { code?: unknown }).code === 'string') {
    return { detail: (d as { code: string }).code, info: d as Record<string, unknown> };
  }
  return { detail: fallback };
}

let csrfToken = '';

/** Every request: same-origin cookie, client header, CSRF token on unsafe methods. */
export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', 'X-Requested-With': 'heardloop' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken;
  const res = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    let info: Record<string, unknown> | undefined;
    try {
      ({ detail, info } = detailOf((await res.json()) as { detail?: unknown }, detail));
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(res.status, detail, info);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

/** Step 1. Returns the user, or a challenge when a second factor is required. */
export async function login(email: string, password: string): Promise<Me | MfaChallenge> {
  const res = await request<Me | MfaChallenge>('POST', '/api/v1/auth/login', { email, password });
  csrfToken = res.csrf_token;
  return res;
}

/** Drops RTL bidi marks / spaces browsers insert into ltr fields on a dir=rtl page. */
export function sanitizeMfaCode(code: string): string {
  return code.replace(/[\u200e\u200f\u202a-\u202e\s]/g, '').trim();
}

/** Step 2: 6-digit code from the authenticator app, or a recovery code. */
export async function verifyMfa(code: string): Promise<Me> {
  const me = await request<Me>('POST', '/api/v1/auth/mfa/verify', { code: sanitizeMfaCode(code) });
  csrfToken = me.csrf_token;
  return me;
}

export const mfaSetup = (password: string) =>
  request<{ secret: string; otpauth_uri: string }>('POST', '/api/v1/auth/mfa/setup', { password });
export const mfaEnable = (code: string) =>
  request<{ recovery_codes: string[] }>('POST', '/api/v1/auth/mfa/enable', { code: sanitizeMfaCode(code) });
export const mfaRegenerateCodes = (password: string, code: string) =>
  request<{ recovery_codes: string[] }>('POST', '/api/v1/auth/mfa/recovery-codes', {
    password,
    code: sanitizeMfaCode(code),
  });
export const mfaDisable = (password: string, code: string) =>
  request<void>('POST', '/api/v1/auth/mfa/disable', { password, code: sanitizeMfaCode(code) });

/** Returns null when not logged in. */
export async function fetchMe(): Promise<Me | null> {
  try {
    const me = await request<Me>('GET', '/api/v1/auth/me');
    csrfToken = me.csrf_token;
    return me;
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null;
    throw err;
  }
}

export async function logout(): Promise<void> {
  try {
    await request<void>('POST', '/api/v1/auth/logout');
  } finally {
    csrfToken = '';
  }
}

/** Calls GET /api/v1/health. Never throws. */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthState> {
  try {
    const res = await fetch('/api/v1/health', { signal, headers: { Accept: 'application/json' } });
    if (res.status === 503) return 'unhealthy';
    if (!res.ok) return 'offline';
    const body = (await res.json()) as { status?: string };
    return body.status === 'healthy' ? 'healthy' : 'unhealthy';
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') return 'checking';
    return 'offline';
  }
}

// ---- Workspace settings, members, audit ----

export interface WorkspaceSettings {
  workspace_id: string;
  name: string;
  time_zone: string;
  require_approval: boolean;
  weekly_digest: boolean;
  can_edit: boolean;
}

export interface Member {
  user_id: string;
  email: string;
  display_name: string;
  role: Role;
}

export interface AuditEntry {
  id: number;
  at: string;
  action: string;
  entity: string;
  entity_id: string | null;
  actor_name: string | null;
  ip_address: string | null;
  changes: Record<string, unknown> | null;
}

const ws = (id: string) => `/api/v1/workspaces/${encodeURIComponent(id)}`;

export const getSettings = (id: string) => request<WorkspaceSettings>('GET', `${ws(id)}/settings`);
export const updateSettings = (
  id: string,
  patch: Partial<Pick<WorkspaceSettings, 'name' | 'time_zone' | 'require_approval' | 'weekly_digest'>>,
) =>
  request<WorkspaceSettings>('PATCH', `${ws(id)}/settings`, patch);

/** Owner only. The newest backup as the backup service recorded it: status, time and sizes, nothing else. */
export interface BackupStatus {
  configured: boolean;
  status: 'ok' | 'failed' | 'unreadable' | null;
  stale: boolean;
  finished_at: string | null;
  age_hours: number | null;
  db_bytes: number;
  media_bytes: number;
  encrypted: boolean;
  error: string | null;
}

export const getBackupStatus = (id: string) => request<BackupStatus>('GET', `${ws(id)}/settings/backup`);

export const listMembers = (id: string) => request<Member[]>('GET', `${ws(id)}/members`);
export const addMember = (
  id: string,
  body: { email: string; display_name: string; role: Role; initial_password?: string },
) => request<Member>('POST', `${ws(id)}/members`, body);
export const updateMember = (id: string, userId: string, role: Role) =>
  request<Member>('PATCH', `${ws(id)}/members/${encodeURIComponent(userId)}`, { role });
export const removeMember = (id: string, userId: string) =>
  request<void>('DELETE', `${ws(id)}/members/${encodeURIComponent(userId)}`);
export const anonymiseMember = (id: string, userId: string, code: string) =>
  request<void>('POST', `${ws(id)}/members/${encodeURIComponent(userId)}/anonymise`, { code });

export interface AuditFilter {
  category?: string; // action prefix: post, campaign, auth, member, workspace, linkedin, media
  actor_id?: string;
  since?: string; // ISO 8601 with offset
  until?: string;
  q?: string;
}

export const listAudit = (id: string, beforeId?: number, filter: AuditFilter = {}) => {
  const params = new URLSearchParams({ limit: '50' });
  if (beforeId) params.set('before_id', String(beforeId));
  for (const [k, v] of Object.entries(filter)) if (v) params.set(k, v);
  return request<{ items: AuditEntry[]; next_before_id: number | null }>('GET', `${ws(id)}/audit?${params}`);
};

export const saveUiPrefs = (prefs: UiPrefs) => request<UiPrefs>('PUT', '/api/v1/auth/me/preferences', prefs);

export interface AppUpdateInfo {
  available: boolean;
  latest: string;
  url: string;
  published_at: string | null;
  notes: string;
}

export interface AppVersion {
  version: string;
  git_sha: string;
  update: AppUpdateInfo | null;
}

export const getAppVersion = () => request<AppVersion>('GET', '/api/v1/version');
export const checkAppVersion = () => request<AppVersion>('POST', '/api/v1/version/check');

/** Owner only. `before` = ISO instant; omit to clear everything. The purge is itself logged. */
export const purgeAudit = (id: string, password: string, before?: string) =>
  request<{ deleted: number }>('POST', `${ws(id)}/audit/purge`, { password, before: before ?? null });

export const changePassword = (current_password: string, new_password: string) =>
  request<void>('POST', '/api/v1/auth/password', { current_password, new_password });

// ---- LinkedIn accounts ----

export interface LinkedInAccount {
  id: string;
  display_name: string;
  urn: string;
  type: 'person' | 'organization';
  status: 'active' | 'expired' | 'revoked';
  expires_at: string;
  expiring_soon: boolean;
  needs_reconnect_reason: string | null;
  needs_reconnect_at: string | null;
}

export const listAccounts = (id: string) => request<LinkedInAccount[]>('GET', `${ws(id)}/linkedin/accounts`);
export const startLinkedInConnect = (id: string) =>
  request<{ authorization_url: string }>('POST', `${ws(id)}/linkedin/connect`);
export const disconnectAccount = (id: string, accountId: string) =>
  request<void>('DELETE', `${ws(id)}/linkedin/accounts/${encodeURIComponent(accountId)}`);
export const retryFailedAccountPosts = (id: string, accountId: string) =>
  request<{ count: number; skipped_unapproved: number }>(
    'POST',
    `${ws(id)}/linkedin/accounts/${encodeURIComponent(accountId)}/retry-failed`,
  );

// ---- Posts ----

export type PostStatus =
  | 'draft'
  | 'pending_approval'
  | 'approved'
  | 'scheduled'
  | 'publishing'
  | 'published'
  | 'failed';

export interface LinkCard {
  url: string;
  title: string;
  description: string | null;
}

export interface LinkPreview {
  url: string;
  title: string | null;
  description: string | null;
  image_url: string | null;
  site_name: string | null;
}

export interface Post {
  id: string;
  status: PostStatus;
  account_id: string;
  account_name: string;
  account_needs_reconnect_reason: string | null;
  campaign_id: string | null;
  body: string;
  media: { id: string; url: string; mime_type?: string }[];
  link: LinkCard | null;
  version_count: number;
  scheduled_at: string | null;
  published_at: string | null;
  linkedin_post_urn: string | null;
  linkedin_url: string | null;
  external_source: string | null; // 'linkedin_export' for posts created by an analytics import
  last_error: string | null;
  last_review_comment: string | null;
  created_at: string;
  updated_at: string;
}

const posts = (id: string) => `${ws(id)}/posts`;
const post = (id: string, postId: string) => `${posts(id)}/${encodeURIComponent(postId)}`;

export const listPosts = (
  id: string,
  filter: { status?: PostStatus; campaign_id?: string; order?: 'newest' | 'oldest' } = {},
) => {
  const q = new URLSearchParams({ limit: '100' });
  if (filter.order) q.set('order', filter.order);
  if (filter.status) q.set('status', filter.status);
  if (filter.campaign_id) q.set('campaign_id', filter.campaign_id);
  return request<Post[]>('GET', `${posts(id)}?${q.toString()}`);
};
export const getPost = (id: string, postId: string) => request<Post>('GET', post(id, postId));
export const createPost = (
  id: string,
  body: { account_id: string; body: string; media_ids: string[]; campaign_id?: string | null; link?: LinkCard | null },
) => request<Post>('POST', posts(id), body);
/** campaign_id / link: null removes it; leave the field out to keep the current value. */
export const updatePost = (
  id: string,
  postId: string,
  body: { account_id?: string; body?: string; media_ids?: string[]; campaign_id?: string | null; link?: LinkCard | null },
) => request<Post>('PATCH', post(id, postId), body);
export const fetchLinkPreview = (id: string, url: string) =>
  request<LinkPreview>('POST', `${posts(id)}/link-preview`, { url });
export const deletePost = (id: string, postId: string) => request<void>('DELETE', post(id, postId));
export const postAction = (
  id: string,
  postId: string,
  action: 'submit' | 'approve' | 'publish-now' | 'unschedule',
) => request<Post>('POST', `${post(id, postId)}/${action}`);
export const rejectPost = (id: string, postId: string, comment: string) =>
  request<Post>('POST', `${post(id, postId)}/reject`, { comment });
/** localTime = "YYYY-MM-DDTHH:mm", interpreted by the server in the workspace time zone. */
export const schedulePost = (id: string, postId: string, localTime: string) =>
  request<Post>('POST', `${post(id, postId)}/schedule`, { local_time: localTime });

export interface PostVersion {
  id: string;
  created_at: string;
  author_id: string;
  author_name: string;
  body: string;
  media_ids: string[];
  link: LinkCard | null;
  is_current: boolean;
  approved: boolean;
}

export interface RestoreResult extends Post {
  warnings: string[];
}

export const listPostVersions = (id: string, postId: string) =>
  request<PostVersion[]>('GET', `${post(id, postId)}/versions`);
export const restorePostVersion = (id: string, postId: string, versionId: string) =>
  request<RestoreResult>('POST', `${post(id, postId)}/versions/${encodeURIComponent(versionId)}/restore`);
export const savePostAsTemplate = (id: string, postId: string, body: { name: string; tags?: string[] }) =>
  request<PostTemplate>('POST', `${post(id, postId)}/save-as-template`, body);

export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
export const MAX_VIDEO_BYTES = 200 * 1024 * 1024;
export const MIN_VIDEO_BYTES = 75 * 1024;

export interface UploadedMedia {
  id: string;
  url: string;
  mime_type: string;
  size_bytes: number;
}

/**
 * Uploads the raw file (PNG/JPEG/GIF or MP4); the server checks the real type from the bytes.
 * XMLHttpRequest instead of fetch so a 200 MB video can show upload progress.
 */
export function uploadMedia(id: string, file: File, onProgress?: (fraction: number) => void): Promise<UploadedMedia> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${ws(id)}/media`);
    xhr.withCredentials = true;
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    xhr.setRequestHeader('X-Requested-With', 'heardloop');
    xhr.setRequestHeader('X-CSRF-Token', csrfToken);
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.upload.onprogress = (e) => {
      if (onProgress && e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onerror = () => reject(new ApiError(0, 'network'));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as UploadedMedia);
        return;
      }
      let detail = xhr.status === 413 ? 'file_too_large' : xhr.statusText; // nginx's own 413 page is HTML
      try {
        const data = JSON.parse(xhr.responseText) as { detail?: unknown };
        if (typeof data.detail === 'string') detail = data.detail;
      } catch {
        /* not JSON */
      }
      reject(new ApiError(xhr.status, detail));
    };
    xhr.send(file);
  });
}

// ---- Hashtag library ----

export interface Hashtags {
  saved: string[];
  recent: string[];
  recommended_max: number;
}
export const getHashtags = (id: string) => request<Hashtags>('GET', `${posts(id)}/hashtags`);
export const saveHashtag = (id: string, tag: string) => request<Hashtags>('POST', `${posts(id)}/hashtags`, { tag });
export const deleteHashtag = (id: string, tag: string) =>
  request<void>('DELETE', `${posts(id)}/hashtags/${encodeURIComponent(tag)}`);

// ---- Campaigns ----

export type CampaignStatus = 'planned' | 'active' | 'completed' | 'archived';

export interface Campaign {
  id: string;
  name: string;
  goal: string | null;
  audience: string | null;
  start_date: string | null; // YYYY-MM-DD
  end_date: string | null;
  tags: string[];
  color: string;
  status: CampaignStatus;
  post_counts: Partial<Record<PostStatus, number>>;
  created_at: string;
}

export type CampaignInput = {
  name: string;
  goal: string | null;
  audience: string | null;
  start_date: string | null;
  end_date: string | null;
  tags: string[];
  color: string;
  status: CampaignStatus;
};

const campaigns = (id: string) => `${ws(id)}/campaigns`;

export const listCampaigns = (id: string, includeArchived = false) =>
  request<Campaign[]>('GET', `${campaigns(id)}${includeArchived ? '?include_archived=true' : ''}`);
export const createCampaign = (id: string, body: CampaignInput) => request<Campaign>('POST', campaigns(id), body);
export const updateCampaign = (id: string, campaignId: string, body: Partial<CampaignInput>) =>
  request<Campaign>('PATCH', `${campaigns(id)}/${encodeURIComponent(campaignId)}`, body);
export const deleteCampaign = (id: string, campaignId: string) =>
  request<void>('DELETE', `${campaigns(id)}/${encodeURIComponent(campaignId)}`);

// ---- Templates ----

export interface PostTemplate {
  id: string;
  name: string;
  body: string;
  link: LinkCard | null;
  campaign_id: string | null;
  campaign_name: string | null;
  tags: string[];
  created_by: string;
  created_at: string;
  updated_at: string;
  use_count: number;
  archived_at: string | null;
}

export type TemplateInput = {
  name: string;
  body: string;
  link?: LinkCard | null;
  campaign_id?: string | null;
  tags?: string[];
};

export interface TemplateUse {
  body: string;
  link: LinkCard | null;
  campaign_id: string | null;
  hashtags_in_body: string[];
  placeholders_left: string[];
}

const templates = (id: string) => `${ws(id)}/templates`;

export const listTemplates = (id: string, archived = false) =>
  request<PostTemplate[]>('GET', `${templates(id)}${archived ? '?archived=true' : ''}`);
export const getTemplate = (id: string, templateId: string) =>
  request<PostTemplate>('GET', `${templates(id)}/${encodeURIComponent(templateId)}`);
export const createTemplate = (id: string, body: TemplateInput) =>
  request<PostTemplate>('POST', templates(id), body);
export const updateTemplate = (id: string, templateId: string, body: Partial<TemplateInput>) =>
  request<PostTemplate>('PATCH', `${templates(id)}/${encodeURIComponent(templateId)}`, body);
export const archiveTemplate = (id: string, templateId: string) =>
  request<PostTemplate>('POST', `${templates(id)}/${encodeURIComponent(templateId)}/archive`);
export const unarchiveTemplate = (id: string, templateId: string) =>
  request<PostTemplate>('POST', `${templates(id)}/${encodeURIComponent(templateId)}/unarchive`);
export const deleteTemplate = (id: string, templateId: string) =>
  request<void>('DELETE', `${templates(id)}/${encodeURIComponent(templateId)}`);
export const useTemplate = (id: string, templateId: string) =>
  request<TemplateUse>('POST', `${templates(id)}/${encodeURIComponent(templateId)}/use`);

// ---- Calendar ----

export interface CalendarItem {
  post_id: string;
  status: PostStatus;
  at: string;
  local_time: string; // YYYY-MM-DDTHH:mm in the workspace time zone
  snippet: string;
  account_name: string;
  campaign_id: string | null;
  campaign_name: string | null;
  color: string | null;
  can_reschedule: boolean;
}

/** start/end: YYYY-MM-DD, inclusive, in the workspace time zone (at most 62 days). */
export const getCalendar = (id: string, start: string, end: string) =>
  request<{ time_zone: string; start: string; end: string; items: CalendarItem[]; truncated: boolean }>(
    'GET',
    `${ws(id)}/calendar?start=${start}&end=${end}`,
  );

// ---- Notifications ----

export interface AppNotification {
  id: string;
  workspace_id: string;
  kind: string;
  post_id: string | null;
  data: Record<string, unknown>;
  created_at: string;
  read_at: string | null;
}

export const listNotifications = () =>
  request<{ items: AppNotification[]; unread: number }>('GET', '/api/v1/notifications?limit=50');
export const unreadNotifications = () => request<{ unread: number }>('GET', '/api/v1/notifications/unread-count');
export const markNotificationRead = (id: string) =>
  request<void>('POST', `/api/v1/notifications/${encodeURIComponent(id)}/read`);
export const markAllNotificationsRead = () => request<void>('POST', '/api/v1/notifications/read-all');
export const getNotificationPrefs = () =>
  request<{ email_notifications: boolean; email_available: boolean }>('GET', '/api/v1/notifications/preferences');
export const setNotificationPrefs = (email_notifications: boolean) =>
  request<{ email_notifications: boolean; email_available: boolean }>('PUT', '/api/v1/notifications/preferences', {
    email_notifications,
  });

// ---- Workspace webhook (owners) ----

export interface WebhookConfig {
  configured: boolean;
  url: string | null;
  include_text: boolean;
  secret: string | null; // only right after create / rotate
}

export interface WebhookDelivery {
  id: number;
  event: string;
  status: 'pending' | 'sent' | 'failed';
  attempts: number;
  last_status_code: number | null;
  last_error: string | null;
  created_at: string;
  sent_at: string | null;
}

const webhook = (id: string) => `${ws(id)}/webhook`;
export const getWebhook = (id: string) => request<WebhookConfig>('GET', webhook(id));
export const saveWebhook = (id: string, url: string, include_text: boolean) =>
  request<WebhookConfig>('PUT', webhook(id), { url, include_text });
export const rotateWebhookSecret = (id: string) => request<WebhookConfig>('POST', `${webhook(id)}/rotate-secret`);
export const deleteWebhook = (id: string) => request<void>('DELETE', webhook(id));
export const testWebhook = (id: string) => request<{ queued: boolean }>('POST', `${webhook(id)}/test`);
export const listWebhookDeliveries = (id: string) => request<WebhookDelivery[]>('GET', `${webhook(id)}/deliveries`);

// ---- Metrics and reports ----

export interface MetricsEntry {
  id: string;
  captured_at: string;
  impressions: number | null;
  reactions: number | null;
  comments: number | null;
  reposts: number | null;
  clicks: number | null;
  saves: number | null;
  followers: number | null;
  // Only filled by LinkedIn exports.
  members_reached: number | null;
  profile_viewers: number | null;
  followers_gained: number | null;
  sends: number | null;
  demographics: Demographic[] | null;
  top: TopFields | null;
  note: string | null;
  source: string; // manual | linkedin_export | api
  recorded_by_name: string | null;
}

export interface Demographic {
  category: string;
  value: string;
  share: number; // 0-1
}

export interface TopFields {
  job_title: string | null;
  location: string | null;
  industry: string | null;
}

export interface ImportResult extends MetricsEntry {
  post_id: string;
  created: boolean;
}

export interface ImportOptions {
  /** ISO 8601 with offset; leave out to let the server use the export day (layout A) or now (layout B). */
  capturedAt?: string;
  createMissing?: boolean;
  accountId?: string;
  text?: string;
}

/**
 * Imports one LinkedIn "Post analytics" .xlsx. 404 `post_not_found` carries `info` with
 * { share_id, post_url, published_at_hint } so the UI can offer to create the post.
 */
export async function importMetricsExport(id: string, file: File, opts: ImportOptions = {}): Promise<ImportResult> {
  const form = new FormData();
  form.append('file', file, 'export.xlsx'); // the real file name is never sent
  if (opts.capturedAt) form.append('captured_at', opts.capturedAt);
  if (opts.createMissing) {
    form.append('create_missing', 'true');
    if (opts.accountId) form.append('account_id', opts.accountId);
    if (opts.text?.trim()) form.append('text', opts.text);
  }
  let res: Response;
  try {
    res = await fetch(`${ws(id)}/metrics/import`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'X-Requested-With': 'heardloop', 'X-CSRF-Token': csrfToken },
      credentials: 'same-origin',
      body: form, // the browser adds the multipart boundary
    });
  } catch {
    throw new ApiError(0, 'network');
  }
  if (!res.ok) {
    let detail = res.status === 413 ? 'file_too_large' : res.statusText; // nginx's own 413 page is HTML
    let info: Record<string, unknown> | undefined;
    try {
      ({ detail, info } = detailOf((await res.json()) as { detail?: unknown }, detail));
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail, info);
  }
  return (await res.json()) as ImportResult;
}

export type MetricsInput = Partial<
  Pick<MetricsEntry, 'impressions' | 'reactions' | 'comments' | 'reposts' | 'clicks' | 'saves' | 'followers' | 'note'>
> & { captured_at?: string };

export const listMetrics = (id: string, postId: string) => request<MetricsEntry[]>('GET', `${post(id, postId)}/metrics`);
export const addMetrics = (id: string, postId: string, body: MetricsInput) =>
  request<MetricsEntry>('POST', `${post(id, postId)}/metrics`, body);
export const deleteMetrics = (id: string, postId: string, metricId: string) =>
  request<void>('DELETE', `${post(id, postId)}/metrics/${encodeURIComponent(metricId)}`);

export interface ReportPost {
  post_id: string;
  published_at: string;
  snippet: string;
  campaign_id: string | null;
  campaign_name: string | null;
  media_kind: 'none' | 'image' | 'video' | 'link';
  impressions: number | null;
  reactions: number | null;
  comments: number | null;
  reposts: number | null;
  clicks: number | null;
  saves: number | null;
  members_reached: number | null;
  followers_gained: number | null;
  engagement: number;
  engagement_rate: number | null;
  measured_at: string | null;
}

export interface ReportBucket {
  key: string;
  label: string | null;
  posts: number;
  measured: number;
  impressions: number;
  engagement: number;
  members_reached: number;
  followers_gained: number;
  avg_impressions: number | null;
  avg_engagement_rate: number | null;
}

export interface Report {
  start: string;
  end: string;
  time_zone: string;
  totals: ReportBucket;
  trend: ReportBucket[];
  by_campaign: ReportBucket[];
  by_weekday: ReportBucket[];
  by_hour: ReportBucket[];
  by_media: ReportBucket[];
  top_posts: ReportPost[];
  unmeasured: ReportPost[];
}

export const getReport = (id: string, start?: string | null, end?: string | null, campaignId?: string) => {
  const q = new URLSearchParams();
  if (start) q.set('start', start);
  if (end) q.set('end', end);
  if (campaignId) q.set('campaign_id', campaignId);
  return request<Report>('GET', `${ws(id)}/reports?${q}`);
};
export const reportCsvUrl = (id: string, start?: string | null, end?: string | null) => {
  const q = new URLSearchParams();
  if (start) q.set('start', start);
  if (end) q.set('end', end);
  return `${ws(id)}/reports/export.csv?${q}`;
};
