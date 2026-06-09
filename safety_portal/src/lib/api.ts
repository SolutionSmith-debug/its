// Thin fetch wrappers over the same-origin Worker API. Cookies are same-origin;
// the signed session cookie is HttpOnly (set by the Worker) so it's never read here.

export type Role = "submitter" | "admin";

export interface SessionUser {
  username: string;
  /** Authorization role from the server. Drives whether the SPA shows the admin
   *  tabs — but every admin action is independently re-gated server-side. */
  role: Role;
}

async function postJson(path: string, body?: unknown): Promise<Response> {
  return fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export async function login(username: string, password: string): Promise<SessionUser> {
  const res = await postJson("/api/login", { username, password });
  if (!res.ok) {
    throw new Error(
      res.status === 401 ? "Invalid username or password." : "Login failed. Please try again.",
    );
  }
  const data = (await res.json()) as { user: SessionUser };
  return data.user;
}

export async function fetchSession(): Promise<SessionUser | null> {
  const res = await fetch("/api/session", { credentials: "same-origin" });
  if (!res.ok) return null;
  const data = (await res.json()) as { user: SessionUser };
  return data.user;
}

export async function logout(): Promise<void> {
  await postJson("/api/logout");
}

export interface Job {
  job_id: string;
  project_name: string;
}

export async function fetchJobs(): Promise<Job[]> {
  const res = await fetch("/api/jobs", { credentials: "same-origin" });
  if (!res.ok) throw new Error("Could not load jobs.");
  return ((await res.json()) as { jobs: Job[] }).jobs;
}

export interface RecentSubmission {
  submission_uuid: string;
  values: Record<string, unknown>;
}

export async function fetchRecent(
  job: string,
  form: string,
  date: string,
): Promise<RecentSubmission | null> {
  const q = new URLSearchParams({ job, form, date });
  const res = await fetch(`/api/recent?${q.toString()}`, { credentials: "same-origin" });
  if (!res.ok) return null;
  return ((await res.json()) as { submission: RecentSubmission | null }).submission;
}

export interface SubmitBody {
  job_id: string;
  form_code: string;
  variant_label: string | null;
  work_date: string;
  values: Record<string, unknown>;
  submission_uuid: string;
  amends_uuid?: string | null;
  /** Admin "filled out as": the account this submission is attributed to. Omitted
   *  (or === the caller) for a normal self-submit. The server re-gates: a non-admin
   *  sending a non-self value is rejected 403, so this is never the security boundary. */
  submitted_as?: string;
}

export async function submitForm(body: SubmitBody): Promise<void> {
  const res = await postJson("/api/submit", body);
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(
      data.error === "unknown_job"
        ? "That job is no longer active — pick another."
        : "Submission failed. Please try again.",
    );
  }
}

// ── Admin: account management (session + role-gated /api/admin/*) ─────────────

export interface Account {
  username: string;
  role: Role;
  /** 1 = locked out (operator-disabled via the portal_admin CLI), 0 = active. */
  disabled: number;
  created_at: number;
}

/** Result of an admin mutation. `reauth` means the caller edited their OWN login
 *  (or role/deleted themselves) and must re-authenticate — the server cleared the
 *  session cookie. */
export interface AdminResult {
  ok: boolean;
  reauth?: boolean;
  username?: string;
  role?: Role;
}

/** Carries the server's machine-readable error code so the UI can map it to a
 *  human message (e.g. "last_admin" → "Can't remove the last admin"). */
export class AdminError extends Error {
  constructor(
    public code: string,
    public status: number,
  ) {
    super(code);
    this.name = "AdminError";
  }
}

async function adminPost(path: string, body: unknown): Promise<AdminResult> {
  const res = await postJson(path, body);
  const data = (await res.json().catch(() => ({}))) as AdminResult & { error?: string };
  if (!res.ok) throw new AdminError(data.error ?? `http_${res.status}`, res.status);
  return data;
}

export async function listAccounts(): Promise<Account[]> {
  const res = await fetch("/api/admin/users", { credentials: "same-origin" });
  if (res.status === 403) throw new AdminError("forbidden", 403);
  if (!res.ok) throw new Error("Could not load accounts.");
  return ((await res.json()) as { users: Account[] }).users;
}

export function createAccount(username: string, password: string, role: Role): Promise<AdminResult> {
  return adminPost("/api/admin/users", { username, password, role });
}

export function editCredentials(
  username: string,
  changes: { new_username?: string; new_password?: string },
): Promise<AdminResult> {
  return adminPost("/api/admin/users/credentials", { username, ...changes });
}

export function setRole(username: string, role: Role): Promise<AdminResult> {
  return adminPost("/api/admin/users/role", { username, role });
}

export function deleteAccount(username: string): Promise<AdminResult> {
  return adminPost("/api/admin/users/delete", { username });
}

// ── Admin: form-editor publish pipeline (session + role-gated /api/admin/publish) ──
// SEND-FREE enqueue: the Worker validates the composed definition and queues a
// publish_requests row; the Mac daemon is the sole privileged actuator (it commits /
// deploys). The SPA only ever (a) POSTs a request and (b) polls its status — it never
// writes the catalog or any form file directly. Mirrors the External Send Gate: the
// cloud can only queue.
import type { FormDefinition } from "../forms/types";

export type PublishOp = "create" | "edit" | "add_version" | "delete" | "rollback";

/** The enqueue request. create/edit/add_version carry `definition`; delete/rollback
 *  carry `target_form_code` only. `identity` + `parent_form_code` come from the row
 *  identity (the server re-derives form_code = identity-v<version> from the definition). */
export interface PublishPayload {
  op: PublishOp;
  identity: string;
  parent_form_code: string;
  target_form_code?: string;
  definition?: FormDefinition;
}

/** A 400 from the publish gate carries a human-readable `reason` (the failing
 *  validation rule) — surface it verbatim. Distinct from AdminError (no `reason`). */
export class PublishError extends Error {
  constructor(
    public code: string,
    public status: number,
    public reason?: string,
  ) {
    super(reason ?? code);
    this.name = "PublishError";
  }
}

export interface PublishEnqueueResult {
  ok: boolean;
  id: number | null;
  status: "queued";
}

/** Enqueue a publish request. Throws PublishError on 400 (with the server `reason`),
 *  409 (publish_in_progress), or 403 (non-admin). */
export async function publishForm(payload: PublishPayload): Promise<PublishEnqueueResult> {
  const res = await postJson("/api/admin/publish", payload);
  const data = (await res.json().catch(() => ({}))) as
    & Partial<PublishEnqueueResult>
    & { error?: string; reason?: string };
  if (!res.ok) {
    throw new PublishError(data.error ?? `http_${res.status}`, res.status, data.reason);
  }
  return { ok: data.ok ?? true, id: data.id ?? null, status: "queued" };
}

/** One row of the publish state machine, as the status monitor renders it. */
export interface PublishRequest {
  id: number;
  created_at: string | number;
  updated_at: string | number;
  requested_by?: string;
  op: PublishOp;
  identity: string;
  parent_form_code: string;
  target_form_code: string | null;
  status: "queued" | "validated" | "tested" | "merged" | "live" | "archived" | "failed";
  failed_stage: string | null;
  failure_reason: string | null;
}

/** Read the publish state machine (most-recent first) for the monitor. */
export async function fetchPublishStatus(): Promise<PublishRequest[]> {
  const res = await fetch("/api/admin/publish-status", { credentials: "same-origin" });
  if (res.status === 403) throw new AdminError("forbidden", 403);
  if (!res.ok) throw new Error("Could not load publish status.");
  return ((await res.json()) as { requests: PublishRequest[] }).requests;
}

/** Clear all TERMINAL (archived / failed) publish requests from the monitor. Returns the
 *  count cleared. In-flight publishes are never touched (the Worker deletes only finished). */
export async function dismissFinishedPublishes(): Promise<number> {
  const res = await postJson("/api/admin/publish-dismiss");
  if (res.status === 403) throw new AdminError("forbidden", 403);
  if (!res.ok) throw new Error("Could not clear finished publishes.");
  return ((await res.json()) as { cleared?: number }).cleared ?? 0;
}
