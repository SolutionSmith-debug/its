// Thin fetch wrappers over the same-origin Worker API. Cookies are same-origin;
// the signed session cookie is HttpOnly (set by the Worker) so it's never read here.

export interface SessionUser {
  username: string;
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
