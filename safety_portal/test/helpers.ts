import { env, SELF } from "cloudflare:test";
import { expect } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// Shared worker-test helpers — consolidated from the boilerplate that was copied
// (and had started to drift) across the fieldops-* suites. Behavior is
// byte-equivalent to the per-file originals; where a file's local copy diverged
// (custom project names / statuses / seed columns), the divergence rides the
// options parameters below instead of another drifted local copy.
//
// Both `p`/`g` and `post`/`get` spellings are exported so converted files keep
// their existing call-site names — the conversion is import-mechanical only.
// ─────────────────────────────────────────────────────────────────────────────

export const BASE = "https://portal.test";
export const ADMIN_BEARER = "test-admin-token";

export type Init = RequestInit & { cookie?: string; bearer?: string };

export function call(path: string, init: Init = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.cookie) headers.set("Cookie", init.cookie);
  if (init.bearer) headers.set("Authorization", `Bearer ${init.bearer}`);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  return SELF.fetch(BASE + path, { ...init, headers });
}

export function cookieFrom(res: Response): string {
  return (res.headers.get("set-cookie") ?? "").split(";")[0];
}

export async function provision(
  username: string,
  password: string,
  role: "submitter" | "manager" | "admin",
): Promise<void> {
  const res = await call("/api/internal/admin/users", {
    method: "POST",
    bearer: ADMIN_BEARER,
    body: JSON.stringify({ username, password, role }),
  });
  expect(res.status, await res.clone().text()).toBe(201);
}

export async function login(username: string, password: string): Promise<string> {
  const res = await call("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
  expect(res.status, await res.clone().text()).toBe(200);
  return cookieFrom(res);
}

// Cookie-authed one-liners (both spellings — see header note).
export const g = (cookie: string, path: string): Promise<Response> => call(path, { cookie });
export const get = g;
export const p = (cookie: string, path: string, body?: unknown): Promise<Response> =>
  call(path, { method: "POST", cookie, body: body === undefined ? undefined : JSON.stringify(body) });
export const post = p;

/** Typed body read — replaces the ad-hoc `(await res.json()) as T` casts. */
export async function json<T>(res: Response): Promise<T> {
  return (await res.json()) as T;
}

export interface SeedJobOptions {
  /** default `Project ${jobId}` */
  projectName?: string;
  /** default "active" (or "closed" when `active === 0`) */
  status?: string;
  /** default derived: `status === "closed" ? 0 : 1` */
  active?: 0 | 1;
  /** default 'smartsheet' — the 0017 schema default */
  origin?: string;
  /** default 0 — the 0014 schema default */
  progress?: number;
  /** default null */
  client_id?: number | null;
  /** default 1_700_000_000 */
  createdAt?: number;
}

export async function seedJob(jobId: string, opts: SeedJobOptions = {}): Promise<void> {
  const status = opts.status ?? (opts.active === 0 ? "closed" : "active");
  const active = opts.active ?? (status === "closed" ? 0 : 1);
  await env.DB.prepare(
    "INSERT INTO jobs (job_id, project_name, active, status, progress, client_id, origin, created_at) VALUES (?,?,?,?,?,?,?,?)",
  )
    .bind(
      jobId,
      opts.projectName ?? `Project ${jobId}`,
      active,
      status,
      opts.progress ?? 0,
      opts.client_id ?? null,
      opts.origin ?? "smartsheet",
      opts.createdAt ?? 1_700_000_000,
    )
    .run();
}

// A roster person linked to `username`, optionally placed on `currentJob` (personnel.current_job).
export async function seedPersonnel(
  name: string,
  username: string | null = null,
  currentJob: string | null = null,
  opts: { active?: number; trade?: string | null; created_by?: string | null } = {},
): Promise<number> {
  await env.DB.prepare(
    "INSERT INTO personnel (name, username, current_job, trade, created_by, active) VALUES (?,?,?,?,?,?)",
  )
    .bind(name, username, currentJob, opts.trade ?? null, opts.created_by ?? null, opts.active ?? 1)
    .run();
  return (await env.DB.prepare("SELECT id FROM personnel WHERE name=? ORDER BY id DESC LIMIT 1")
    .bind(name)
    .first<{ id: number }>())!.id;
}
