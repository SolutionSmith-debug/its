import type { FormDefinition } from "../forms/types";

/**
 * Per-account localStorage cache of the in-progress Form Editor draft.
 *
 * The editor draft otherwise lives only in React state, so the admin 5-minute idle logout
 * (or any reload/tab-close) unmounts the editor and the work-in-progress form is lost. This
 * persists it keyed by the admin's username so it survives logout → re-login, and restores on
 * the next editor open. Best-effort: every access is wrapped — localStorage being unavailable
 * (private mode, quota) must NEVER block editing. Only EDITOR modes are cached; the cache is
 * cleared on an explicit Discard or a successful publish (FormsPage).
 *
 * One draft per account — starting a new form replaces the cached one (the operator only ever
 * builds one at a time).
 */

const KEY_PREFIX = "its-portal-draft:v1:";

/** The editor modes worth caching — the non-"view" arm of FormsPage's `Mode`. */
export type EditorMode =
  | { kind: "create" }
  | { kind: "edit"; sourceCode: string; identity: string }
  | { kind: "add_version"; sourceCode: string };

export interface CachedDraft {
  mode: EditorMode;
  draft: FormDefinition;
  identity: string;
  parent: string;
}

function keyFor(username: string): string {
  return `${KEY_PREFIX}${username}`;
}

export function saveDraft(username: string, value: CachedDraft): void {
  if (!username) return;
  try {
    localStorage.setItem(keyFor(username), JSON.stringify(value));
  } catch {
    // localStorage unavailable / over quota — caching is best-effort; never block editing.
  }
}

export function loadDraft(username: string): CachedDraft | null {
  if (!username) return null;
  try {
    const raw = localStorage.getItem(keyFor(username));
    if (!raw) return null;
    // Validate against a LOOSE shape (not CachedDraft) so the guard can reject a stale "view"
    // entry without TS narrowing mode.kind to the editor-only union.
    const parsed = JSON.parse(raw) as { mode?: { kind?: string }; draft?: unknown };
    if (!parsed || typeof parsed !== "object") return null;
    const kind = parsed.mode?.kind;
    if (!parsed.draft || !kind || kind === "view") return null;
    return parsed as unknown as CachedDraft;
  } catch {
    return null;
  }
}

export function clearDraft(username: string): void {
  if (!username) return;
  try {
    localStorage.removeItem(keyFor(username));
  } catch {
    // ignore — see saveDraft.
  }
}
