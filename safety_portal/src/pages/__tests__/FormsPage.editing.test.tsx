import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Guards the Invariant-2 keep-alive wiring (slice 8b, C10): FormsPage must report a dirty editor
// UP to the admin shell (which drives useIdleLogout's paused keep-alive) AND must RESET to
// not-editing on unmount. Without the unmount reset, leaving a dirty Forms editor and switching
// tabs would pin the shell `editing=true` forever, permanently disabling the proactive idle
// logout shell-wide. The hook-level test (useIdleLogout.test.ts) can't see this wiring — only a
// real FormsPage render can. The SERVER cookie window is still the real boundary; this is the
// defense-in-depth UX layer.

// Read a username without an AuthProvider / network round-trip.
vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    user: { username: "admin.test", role: "admin" },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

// PublishMonitor fetches publish status on mount — stub it to an empty list (keeps the network
// out of the render; the editing wiring under test is independent of it).
vi.mock("../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/api")>();
  return { ...actual, fetchPublishStatus: vi.fn().mockResolvedValue([]) };
});

import { FormsPage } from "../FormsPage";

// jsdom doesn't reliably provide localStorage; draftCache wraps every access in try/catch, but an
// in-memory Storage keeps the restore-draft effect deterministic (no stray cached draft).
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
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("FormsPage — reports a dirty editor to the admin shell (idle keep-alive wiring)", () => {
  it("reports editing=true when the editor opens, and resets to false on unmount", () => {
    const onEditingChange = vi.fn();
    const { getByText, unmount } = render(
      <FormsPage tabBar={null} onEditingChange={onEditingChange} />,
    );

    // On mount no editor is open → reported not-editing.
    expect(onEditingChange).toHaveBeenLastCalledWith(false);

    // Open the create editor → a dirty draft now exists → reported editing.
    fireEvent.click(getByText("+ New form"));
    expect(onEditingChange).toHaveBeenLastCalledWith(true);

    // Unmount (tab switch) MUST reset to false even though a draft is still present — this is the
    // unmount-reset guard that stops `editing` pinning true forever.
    onEditingChange.mockClear();
    unmount();
    expect(onEditingChange).toHaveBeenCalledWith(false);
  });
});
