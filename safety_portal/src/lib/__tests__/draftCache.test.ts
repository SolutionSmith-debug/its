import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { blankDefinition } from "../../forms/editorModel";
import { type CachedDraft, clearDraft, loadDraft, saveDraft } from "../draftCache";

// The SPA jsdom env doesn't reliably provide localStorage (Node's experimental global shadows
// jsdom's), so install a deterministic in-memory Storage. Production uses the real browser
// localStorage; draftCache wraps every access in try/catch for the unavailable case.
function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    removeItem: (k: string) => void m.delete(k),
    setItem: (k: string, v: string) => void m.set(k, String(v)),
  };
}

beforeEach(() => vi.stubGlobal("localStorage", memoryStorage()));
afterEach(() => vi.unstubAllGlobals());

function sample(overrides: Partial<CachedDraft> = {}): CachedDraft {
  return {
    mode: { kind: "create" },
    draft: blankDefinition(),
    identity: "incident-report",
    parent: "incident-report",
    ...overrides,
  };
}

describe("draftCache", () => {
  it("round-trips an editor draft for an account", () => {
    saveDraft("alice", sample());
    const got = loadDraft("alice");
    expect(got?.identity).toBe("incident-report");
    expect(got?.mode.kind).toBe("create");
    expect(got?.draft.sections).toBeInstanceOf(Array);
  });

  it("isolates drafts per account (no cross-admin clobber on a shared browser)", () => {
    saveDraft("alice", sample({ identity: "alice-form" }));
    expect(loadDraft("bob")).toBeNull();
    saveDraft("bob", sample({ identity: "bob-form" }));
    expect(loadDraft("alice")?.identity).toBe("alice-form");
    expect(loadDraft("bob")?.identity).toBe("bob-form");
  });

  it("clears a draft", () => {
    saveDraft("alice", sample());
    clearDraft("alice");
    expect(loadDraft("alice")).toBeNull();
  });

  it("returns null for empty / corrupt / non-editor entries", () => {
    expect(loadDraft("nobody")).toBeNull();
    localStorage.setItem("its-portal-draft:v1:carol", "{not-json");
    expect(loadDraft("carol")).toBeNull();
    // a stale "view" entry is never restored
    localStorage.setItem("its-portal-draft:v1:dave", JSON.stringify({ mode: { kind: "view" }, draft: {} }));
    expect(loadDraft("dave")).toBeNull();
  });

  it("no-ops on an empty username (logged-out)", () => {
    saveDraft("", sample());
    expect(loadDraft("")).toBeNull();
  });
});
