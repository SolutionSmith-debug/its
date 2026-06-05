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
