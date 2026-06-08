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
